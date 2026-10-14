"""Exercise deployment preservation, ownership, routes, and private panel access."""

import json
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from scripts import deploy_server

DNS_NAME = "astrbot.test-tailnet.ts.net"


@pytest.fixture
def checkout(tmp_path):
    """Create a checkout with only portable source and timetable inputs.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        The temporary checkout root.
    """
    for plugin in deploy_server.PLUGINS:
        directory = tmp_path / "data" / "plugins" / plugin
        directory.mkdir(parents=True)
        (directory / "main.py").write_text("# Plugin source\n")
    course = tmp_path / "data" / "plugin_data" / "astrbot_plugin_course"
    (course / "ics").mkdir(parents=True)
    (course / "bindings.json").write_text('{"bindings": {"owner": {}}}')
    (course / "ics" / "owner.ics").write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
    (course / "private-token.json").write_text('{"secret": "not-portable"}')
    return tmp_path


def test_initialization_is_private_and_repeat_preserves_live_data(checkout):
    deploy_server.prepare_server(checkout, DNS_NAME)
    runtime = checkout / "runtime"
    secret = runtime / "secrets" / "vikunja_secret"
    original_secret = secret.read_bytes()
    assert len(original_secret.strip()) == 64
    assert secret.stat().st_mode & 0o777 == 0o600
    assert runtime.stat().st_mode & 0o777 == 0o700
    assert (checkout / ".env").stat().st_mode & 0o777 == 0o600
    assert original_secret.strip() not in (checkout / ".env").read_bytes()
    course = runtime / "astrbot" / "plugin_data" / "astrbot_plugin_course"
    assert (course / "ics" / "owner.ics").is_file()
    assert not (course / "private-token.json").exists()
    (course / "bindings.json").write_text('{"bindings": {"new-owner": {}}}')
    (course / "ics" / "owner.ics").unlink()
    config = runtime / "astrbot" / "config" / "astrbot_plugin_vikunja_config.json"
    values = json.loads(config.read_text())
    assert values["vikunja_url"] == "http://vikunja:3456"
    assert values["vikunja_web_url"] == f"https://{DNS_NAME}"
    values["api_token"] = "keep-current-token"
    config.write_text(json.dumps(values))
    database = runtime / "vikunja" / "db" / "vikunja.db"
    database.write_bytes(b"existing-database")
    before = {path: path.read_bytes() for path in runtime.rglob("*") if path.is_file()}
    deploy_server.prepare_server(checkout, DNS_NAME)
    assert before == {
        path: path.read_bytes() for path in runtime.rglob("*") if path.is_file()
    }
    assert secret.read_bytes() == original_secret


def test_repeat_accepts_plugin_configuration_saved_by_astrbot(checkout):
    deploy_server.prepare_server(checkout, DNS_NAME)
    config = checkout / "runtime/astrbot/config/astrbot_plugin_char_config.json"
    values = json.loads(config.read_text(encoding="utf-8"))
    config.write_text(json.dumps(values), encoding="utf-8-sig")
    before = config.read_bytes()
    deploy_server.prepare_server(checkout, DNS_NAME)
    assert config.read_bytes() == before


def test_existing_database_does_not_get_a_replacement_secret(checkout):
    database = checkout / "runtime" / "vikunja" / "db" / "vikunja.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"existing")
    with pytest.raises(RuntimeError, match="Restore the secret"):
        deploy_server.prepare_server(checkout, DNS_NAME)
    assert not (checkout / "runtime" / "secrets" / "vikunja_secret").exists()
    assert not (checkout / ".env").exists()


def test_interrupted_initial_course_copy_can_be_retried(checkout, monkeypatch):
    copy = shutil.copy2

    def interrupt_calendar(source, target):
        """Simulate a storage failure partway through portable-data initialization.

        Args:
            source: Input file path.
            target: Destination file path.

        Returns:
            The copied file path for non-calendar files.

        Raises:
            OSError: The calendar copy is interrupted.
        """
        if Path(source).suffix == ".ics":
            raise OSError("Simulated interrupted copy")
        return copy(source, target)

    monkeypatch.setattr(deploy_server.shutil, "copy2", interrupt_calendar)
    with pytest.raises(OSError, match="interrupted"):
        deploy_server.prepare_server(checkout, DNS_NAME)
    course = checkout / "runtime" / "astrbot" / "plugin_data" / "astrbot_plugin_course"
    assert not course.exists()
    monkeypatch.setattr(deploy_server.shutil, "copy2", copy)
    deploy_server.prepare_server(checkout, DNS_NAME)
    assert (course / "ics" / "owner.ics").is_file()
    assert (course / "bindings.json").is_file()


def test_existing_domain_and_insecure_panel_configuration_are_preserved(checkout):
    deploy_server.prepare_server(checkout, DNS_NAME)
    original = (checkout / ".env").read_bytes()
    with pytest.raises(RuntimeError, match="VIKUNJA_PUBLIC_URL"):
        deploy_server.prepare_server(checkout, "other.test-tailnet.ts.net")
    assert (checkout / ".env").read_bytes() == original
    config = (
        checkout / "runtime" / "astrbot" / "config" / "astrbot_plugin_char_config.json"
    )
    values = json.loads(config.read_text())
    values["memory"]["memory_panel_require_token"] = False
    config.write_text(json.dumps(values))
    original = config.read_bytes()
    with pytest.raises(RuntimeError, match="memory_panel_require_token"):
        deploy_server.prepare_server(checkout, DNS_NAME)
    assert config.read_bytes() == original


@pytest.fixture
def host_commands(monkeypatch, checkout):
    """Simulate host inspection while exercising the actual conflict decisions.

    Args:
        monkeypatch: Pytest patch fixture.
        checkout: Temporary checkout root.

    Returns:
        Mutable host state for each test.
    """
    host = {
        "state": "Running",
        "serve": {},
        "containers": [],
    }

    def run(command, **kwargs):
        """Return the requested inspection result from the simulated host.

        Args:
            command: Executable and arguments.
            **kwargs: Subprocess options accepted by the production call.

        Returns:
            A completed subprocess result.
        """
        assert kwargs["timeout"] == 30
        if command[:2] == ["tailscale", "status"]:
            output = json.dumps(
                {"BackendState": host["state"], "Self": {"DNSName": DNS_NAME + "."}}
            )
        elif command[:2] == ["docker", "ps"]:
            output = "\n".join(str(i) for i in range(len(host["containers"])))
        elif command[:2] == ["docker", "inspect"]:
            output = "\n".join(json.dumps(item) for item in host["containers"])
        else:
            assert command == ["tailscale", "serve", "status", "--json"]
            output = json.dumps(host["serve"])
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(deploy_server.subprocess, "run", run)
    # Reserve a unique ephemeral port instead of depending on development services.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr(deploy_server, "ROUTES", {8444: port})
    host["port"] = port
    return host


def test_own_routes_can_be_reused_without_touching_unrelated_routes(
    checkout, host_commands
):
    host_commands["serve"] = {
        "TCP": {"8444": {"HTTPS": True}, "9443": {"HTTPS": True}},
        "Web": {
            f"{DNS_NAME}:8444": {
                "Handlers": {
                    "/": {"Proxy": f"http://127.0.0.1:{host_commands['port']}"}
                }
            },
            f"{DNS_NAME}:9443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9999"}}},
        },
    }
    original = json.dumps(host_commands["serve"])
    assert deploy_server.check_server(checkout) == DNS_NAME
    assert json.dumps(host_commands["serve"]) == original


@pytest.mark.parametrize(
    "conflict", ["target", "extra-path", "tcp", "foreground", "funnel"]
)
def test_conflicting_serve_configuration_is_refused(checkout, host_commands, conflict):
    endpoint = f"{DNS_NAME}:8444"
    config = {
        "TCP": {"8444": {"HTTPS": True}},
        "Web": {
            endpoint: {
                "Handlers": {
                    "/": {"Proxy": f"http://127.0.0.1:{host_commands['port']}"}
                }
            }
        },
    }
    if conflict == "target":
        config["Web"][endpoint]["Handlers"]["/"]["Proxy"] = "http://127.0.0.1:9999"
    elif conflict == "extra-path":
        config["Web"][endpoint]["Handlers"]["/other"] = {"Text": "another service"}
    elif conflict == "tcp":
        config["TCP"]["8444"] = {"TCPForward": "localhost:9999"}
    elif conflict == "foreground":
        config["Foreground"] = {"session": {"TCP": {"8444": {"HTTPS": True}}}}
    else:
        config["AllowFunnel"] = {endpoint: True}
    host_commands["serve"] = config
    with pytest.raises(RuntimeError):
        deploy_server.check_server(checkout)


def test_busy_ports_require_ownership_by_this_checkout(checkout, host_commands):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", host_commands["port"]))
        occupied.listen()
        with pytest.raises(RuntimeError, match="occupied"):
            deploy_server.check_server(checkout)
        host_commands["containers"] = [
            {
                "labels": {"com.docker.compose.project.working_dir": str(checkout)},
                "running": True,
                "ports": {"7835/tcp": [{"HostPort": str(host_commands["port"])}]},
            }
        ]
        assert deploy_server.check_server(checkout) == DNS_NAME
        host_commands["containers"][0]["labels"][
            "com.docker.compose.project.working_dir"
        ] = "/other-checkout"
        with pytest.raises(RuntimeError, match="another checkout"):
            deploy_server.check_server(checkout)


def test_disconnected_tailscale_and_missing_plugin_are_refused(checkout, host_commands):
    host_commands["state"] = "NeedsLogin"
    with pytest.raises(RuntimeError, match="not connected"):
        deploy_server.check_server(checkout)
    host_commands["state"] = "Running"
    (checkout / "data" / "plugins" / "astrbot_plugin_vikunja" / "main.py").unlink()
    with pytest.raises(RuntimeError, match="Missing plugin"):
        deploy_server.check_server(checkout)


def test_host_inspection_timeout_does_not_initialize_runtime(checkout, monkeypatch):
    def stalled_command(command, **kwargs):
        """Simulate a daemon that does not respond within the inspection limit.

        Args:
            command: Host inspection command.
            **kwargs: Subprocess execution options.

        Raises:
            subprocess.TimeoutExpired: The simulated daemon does not respond.
        """
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(deploy_server.subprocess, "run", stalled_command)
    with pytest.raises(subprocess.TimeoutExpired) as error:
        deploy_server.check_server(checkout)
    assert error.value.timeout == 30
    assert not (checkout / "runtime").exists()
    assert not (checkout / ".env").exists()


def test_memory_panel_http_requires_current_token():
    from data.plugins.astrbot_plugin_char.memory_panel_utils import MemoryPanelManager

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    manager = MemoryPanelManager(
        SimpleNamespace(),
        SimpleNamespace(
            memory_panel_host="127.0.0.1",
            memory_panel_port=port,
            memory_panel_require_token=True,
        ),
    )
    try:
        link = manager.ensure_started()
        # Static assets contain no private data; every data API must check the token.
        manager._handle_api_get = lambda path, query: (200, {"authorized": True})
        for path in ("/api/overview", "/api/overview?token=wrong"):
            with pytest.raises(HTTPError) as error:
                urlopen(f"http://127.0.0.1:{port}{path}", timeout=3)
            assert error.value.code == 403
        with urlopen(link, timeout=3) as response:
            assert response.status == 200
        request = Request(
            f"http://127.0.0.1:{port}/api/overview",
            headers={"X-Memory-Token": manager.token},
        )
        with urlopen(request, timeout=3) as response:
            assert response.status == 200
    finally:
        manager.stop()


@pytest.fixture
def installer_host(checkout):
    """Run the real installer with isolated host commands and a temporary checkout.

    Args:
        checkout: Temporary repository with portable inputs.

    Returns:
        Checkout root, command environment, and simulated host-state file.
    """
    actual_root = Path(__file__).resolve().parent.parent
    (checkout / "scripts").mkdir()
    (checkout / ".git").mkdir()
    port_mapping = {}
    for port in (3456, 6185, 7835):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port_mapping[port] = probe.getsockname()[1]
    for name in ("deploy-server.sh", "deploy_server.py"):
        source = (actual_root / "scripts" / name).read_text()
        # Use free host ports so the tests can coexist with actual development services.
        for port, free_port in port_mapping.items():
            source = re.sub(rf"\b{port}\b", str(free_port), source)
        (checkout / "scripts" / name).write_text(source)
    shutil.copy2(actual_root / "compose.yml", checkout / "compose.yml")
    bin_dir = checkout / "fake-bin"
    bin_dir.mkdir()
    state_file = checkout / "host-state.json"
    state_file.write_text(json.dumps({"root": str(checkout), "calls": [], "serve": {}}))
    stub = bin_dir / "host-command"
    stub.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
state_path = Path(os.environ["ASTRBOT_INSTALLER_TEST_STATE"])
state = json.loads(state_path.read_text())
tool, args = Path(sys.argv[0]).name, sys.argv[1:]
state["calls"].append([tool, *args])
if tool == "docker" and args and args[0] == "compose":
    position = 1
    while position < len(args) and args[position].startswith("--"):
        option = args[position]
        position += 1 if "=" in option else 2
    args = ["compose", *args[position:]]
    if args[1:2] == ["build"]:
        state["build_environment"] = {
            name: os.environ.get(name, "")
            for name in (
                "PYPI_INDEX_URL", "NPM_REGISTRY", "DEBIAN_MIRROR",
                "DEBIAN_SECURITY_MIRROR", "PLAYWRIGHT_DOWNLOAD_HOST",
                "PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT",
            )
        }
code = 0
if tool == "id":
    print("0")
elif tool == "dpkg":
    print("amd64")
elif tool == "docker":
    if args[:2] == ["version", "--format"]:
        print("29.0.0")
    elif args[:2] == ["ps", "-aq"]:
        if state.get("deployed"):
            print("test-container")
    elif args[0] == "inspect":
        print(json.dumps({"labels": {"com.docker.compose.project.working_dir": state["root"]}, "running": state.get("running", False), "ports": {"6185/tcp": [{"HostPort": "6185"}], "7835/tcp": [{"HostPort": "7835"}], "3456/tcp": [{"HostPort": "3456"}]}}))
    elif args[:2] == ["compose", "build"] and state.get("fail_build"):
        code = 1
    elif args[:2] == ["compose", "pull"] and state.get("fail_pull"):
        code = 1
    elif args[:2] == ["compose", "stop"]:
        state["running"] = False
    elif args[:2] == ["compose", "up"]:
        state["deployed"] = state["running"] = True
elif tool == "tailscale":
    if args[:2] == ["status", "--json"]:
        print(json.dumps({"BackendState": "Running", "Self": {"DNSName": "astrbot.test-tailnet.ts.net."}}))
    elif args[:3] == ["serve", "status", "--json"]:
        print(json.dumps(state["serve"]))
    elif args[:2] == ["serve", "--bg"]:
        port = args[2].split("=")[1]
        if state.get("fail_serve") == port:
            code = 1
        else:
            state["serve"].setdefault("TCP", {})[port] = {"HTTPS": True}
            state["serve"].setdefault("Web", {})["astrbot.test-tailnet.ts.net:" + port] = {"Handlers": {"/": {"Proxy": args[3]}}}
elif tool not in {"systemctl", "chown", "curl"}:
    raise RuntimeError("Unexpected host command: " + tool)
state_path.write_text(json.dumps(state))
sys.exit(code)
""")
    stub.chmod(0o755)
    for name in ("id", "dpkg", "docker", "tailscale", "systemctl", "chown", "curl"):
        (bin_dir / name).symlink_to(stub)
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "ASTRBOT_INSTALLER_TEST_STATE": str(state_file),
    }
    for name in (
        "PYPI_INDEX_URL",
        "NPM_REGISTRY",
        "DEBIAN_MIRROR",
        "DEBIAN_SECURITY_MIRROR",
        "PLAYWRIGHT_DOWNLOAD_HOST",
        "PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT",
    ):
        env.pop(name, None)
    return checkout, env, state_file


@pytest.mark.parametrize("options", [["--install-only"], ["--cn", "--install-only"]])
def test_install_only_does_not_initialize_or_start_applications(
    installer_host, options
):
    root, env, state_file = installer_host
    result = subprocess.run(
        ["bash", str(root / "scripts" / "deploy-server.sh"), *options],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "Deployment completed" not in result.stdout
    assert not (root / "runtime").exists()
    assert not (root / ".env").exists()
    calls = json.loads(state_file.read_text())["calls"]
    assert not any(
        call[:2] == ["docker", "compose"]
        and any(stage in call for stage in ("build", "pull", "stop", "up"))
        for call in calls
    )
    assert not any(
        call[:2] in (["tailscale", "up"], ["tailscale", "serve"]) for call in calls
    )


def test_invalid_installer_option_is_refused_before_host_changes(installer_host):
    root, env, state_file = installer_host
    result = subprocess.run(
        ["bash", str(root / "scripts" / "deploy-server.sh"), "--mistyped-option"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert not (root / "runtime").exists()
    assert not (root / ".env").exists()
    assert not json.loads(state_file.read_text())["calls"]


def test_cn_mirrors_reach_the_build_without_becoming_runtime_configuration(
    installer_host,
):
    root, env, state_file = installer_host
    result = subprocess.run(
        ["bash", str(root / "scripts" / "deploy-server.sh"), "--cn"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    build_environment = json.loads(state_file.read_text())["build_environment"]
    assert build_environment == {
        "PYPI_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple",
        "NPM_REGISTRY": "https://registry.npmmirror.com",
        "DEBIAN_MIRROR": "https://mirrors.tuna.tsinghua.edu.cn/debian",
        "DEBIAN_SECURITY_MIRROR": "https://mirrors.tuna.tsinghua.edu.cn/debian-security",
        "PLAYWRIGHT_DOWNLOAD_HOST": "",
        "PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT": "",
    }
    assert "mirrors." not in (root / ".env").read_text()
    assert set(json.loads(state_file.read_text())["serve"]["TCP"]) == {
        "443",
        "8443",
        "8444",
    }


def test_installer_repeats_without_resetting_state(installer_host):
    root, env, state_file = installer_host
    command = ["bash", str(root / "scripts" / "deploy-server.sh")]
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Deployment completed" in result.stdout
    secret = root / "runtime" / "secrets" / "vikunja_secret"
    before = secret.read_bytes()
    assert (
        root / "runtime" / "astrbot" / "plugins" / "astrbot_plugin_vikunja" / "main.py"
    ).is_file()
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert secret.read_bytes() == before
    state = json.loads(state_file.read_text())
    assert set(state["serve"]["TCP"]) == {"443", "8443", "8444"}
    assert before.strip().decode() not in result.stdout


@pytest.mark.parametrize("failed_stage", ["build", "pull"])
def test_failed_download_or_build_does_not_stop_services_or_publish_routes(
    installer_host, failed_stage
):
    root, env, state_file = installer_host
    state = json.loads(state_file.read_text())
    state[f"fail_{failed_stage}"] = True
    state_file.write_text(json.dumps(state))
    result = subprocess.run(
        ["bash", str(root / "scripts" / "deploy-server.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Deployment completed" not in result.stdout
    state = json.loads(state_file.read_text())
    assert any(
        call[:2] == ["docker", "compose"] and failed_stage in call
        for call in state["calls"]
    )
    assert not any(
        call[:2] == ["docker", "compose"] and "stop" in call for call in state["calls"]
    )
    assert not any(
        call[:3] == ["tailscale", "serve", "--bg"] for call in state["calls"]
    )


def test_partially_configured_serve_can_resume_after_failure(installer_host):
    root, env, state_file = installer_host
    state = json.loads(state_file.read_text())
    state["fail_serve"] = "8443"
    state_file.write_text(json.dumps(state))
    command = ["bash", str(root / "scripts" / "deploy-server.sh")]
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Deployment completed" not in result.stdout
    state = json.loads(state_file.read_text())
    assert set(state["serve"]["TCP"]) == {"443"}
    del state["fail_serve"]
    state_file.write_text(json.dumps(state))
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert set(json.loads(state_file.read_text())["serve"]["TCP"]) == {
        "443",
        "8443",
        "8444",
    }
