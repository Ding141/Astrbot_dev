"""Check host ownership and prepare private server state for deploy-server.sh."""

import argparse
import json
import re
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path

PROJECT = "astrbot-personal"
PLUGINS = ("astrbot_plugin_char", "astrbot_plugin_course", "astrbot_plugin_vikunja")
ROUTES = {443: 3456, 8443: 6185, 8444: 7835}


def check_server(root: Path) -> str:
    """Check Tailscale routes and host ports before modifying application state.

    Args:
        root: Absolute path to the checkout being deployed.

    Returns:
        The server's actual MagicDNS hostname.

    Raises:
        RuntimeError: Required sources, connectivity, or service ownership are invalid.
        subprocess.CalledProcessError: A host inspection command fails.
    """
    for plugin in PLUGINS:
        if not (root / "data" / "plugins" / plugin / "main.py").is_file():
            raise RuntimeError(f"Missing plugin source: {plugin}. Check the checkout.")
    status_result = subprocess.run(
        ["tailscale", "status", "--json"], capture_output=True, text=True
    )
    status = json.loads(status_result.stdout)
    if status.get("BackendState") != "Running":
        raise RuntimeError(
            "Tailscale is not connected. Log in and approve this device."
        )
    dns_name = str(status.get("Self", {}).get("DNSName", "")).rstrip(".").lower()
    if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net", dns_name):
        raise RuntimeError(
            "No MagicDNS hostname. Enable MagicDNS in the admin console."
        )

    ids = subprocess.run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={PROJECT}",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    own_ports = set()
    if ids:
        containers = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                '{"labels":{{json .Config.Labels}},"running":{{json .State.Running}},'
                '"ports":{{json .NetworkSettings.Ports}}}',
                *ids,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        for line in containers.stdout.splitlines():
            container = json.loads(line)
            checkout = container["labels"].get(
                "com.docker.compose.project.working_dir", ""
            )
            if Path(checkout).resolve() != root.resolve():
                raise RuntimeError(
                    f"Compose project {PROJECT} belongs to another checkout."
                )
            if container["running"]:
                for bindings in (container["ports"] or {}).values():
                    for binding in bindings or []:
                        own_ports.add(int(binding["HostPort"]))
    for port in ROUTES.values():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                if port not in own_ports:
                    raise RuntimeError(
                        f"Host port {port} is occupied by another service."
                    ) from error

    config = json.loads(
        subprocess.run(
            ["tailscale", "serve", "status", "--json"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    for foreground in (config.get("Foreground") or {}).values():
        if set(map(int, (foreground.get("TCP") or {}))) & ROUTES.keys():
            raise RuntimeError(
                "A foreground Tailscale Serve session uses a required port."
            )
    for port, target in ROUTES.items():
        endpoint = f"{dns_name}:{port}"
        web = config.get("Web") or {}
        endpoints = [name for name in web if name.rsplit(":", 1)[-1] == str(port)]
        tcp = (config.get("TCP") or {}).get(str(port))
        if tcp or endpoints:
            expected = {"/": {"Proxy": f"http://127.0.0.1:{target}"}}
            if (
                tcp != {"HTTPS": True}
                or endpoints != [endpoint]
                or web[endpoint].get("Handlers") != expected
            ):
                raise RuntimeError(
                    f"Tailscale Serve port {port} has a conflicting route."
                )
        if (config.get("AllowFunnel") or {}).get(endpoint):
            raise RuntimeError(
                f"Funnel is enabled on {endpoint}. Disable it before deployment."
            )
    return dns_name


def prepare_server(root: Path, dns_name: str) -> None:
    """Initialize missing private files without overwriting existing runtime state.

    Args:
        root: Absolute path to the repository checkout.
        dns_name: Validated MagicDNS hostname of the server.

    Raises:
        RuntimeError: Existing configuration conflicts with the private deployment.
        ValueError: The hostname is invalid.
    """
    if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net", dns_name):
        raise ValueError("Invalid MagicDNS hostname.")
    runtime = root / "runtime"
    config_dir = runtime / "astrbot" / "config"
    secret = runtime / "secrets" / "vikunja_secret"
    public_url = f"https://{dns_name}/"
    env_path = root / ".env"
    if env_path.exists():
        values = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
        if values.get("VIKUNJA_PUBLIC_URL", "").rstrip("/") != public_url.rstrip("/"):
            raise RuntimeError(
                f"Set VIKUNJA_PUBLIC_URL={public_url} in .env, then rerun."
            )
    if (runtime / "vikunja" / "db" / "vikunja.db").exists() and not secret.exists():
        raise RuntimeError(
            "Existing Vikunja database has no signing secret. Restore the secret from backup."
        )

    memory_settings = {
        "memory_panel_enabled": True,
        "memory_panel_require_token": True,
        "memory_panel_host": "0.0.0.0",
        "memory_panel_port": 7835,
        "memory_panel_public_base_url": f"https://{dns_name}:8444",
    }
    char_config = config_dir / "astrbot_plugin_char_config.json"
    if char_config.exists():
        existing = json.loads(char_config.read_text(encoding="utf-8-sig"))
        for key, expected in memory_settings.items():
            actual = existing.get(key, existing.get("memory", {}).get(key))
            if actual != expected:
                raise RuntimeError(
                    f"Set {key}={expected!r} in the char plugin config, then rerun. Existing settings were preserved."
                )

    for directory in (runtime, runtime / "astrbot", config_dir, secret.parent):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
    for directory in (runtime / "vikunja" / "db", runtime / "vikunja" / "files"):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
    if not env_path.exists():
        with env_path.open("x", encoding="utf-8") as stream:
            stream.write(f"TZ=Asia/Shanghai\nVIKUNJA_PUBLIC_URL={public_url}\n")
    env_path.chmod(0o600)
    if not secret.exists():
        with secret.open("x", encoding="utf-8") as stream:
            stream.write(secrets.token_hex(32) + "\n")
    if not secret.read_text(encoding="utf-8").strip():
        raise RuntimeError(
            "The existing Vikunja signing secret is empty. Restore a valid secret."
        )
    secret.chmod(0o600)
    core_config = runtime / "astrbot" / "cmd_config.json"
    if not core_config.exists():
        # Only the private Compose network and host loopback can reach this proxy backend.
        core_config.write_text(
            json.dumps({"dashboard": {"trust_proxy_headers": True}}, indent=2) + "\n",
            encoding="utf-8",
        )
    core_config.chmod(0o600)
    if not char_config.exists():
        char_config.write_text(
            json.dumps(
                {"memory": {**memory_settings, "memory_panel_auto_start": False}},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    vikunja_config = config_dir / "astrbot_plugin_vikunja_config.json"
    if not vikunja_config.exists():
        vikunja_config.write_text(
            json.dumps(
                {
                    "vikunja_url": "http://vikunja:3456",
                    "vikunja_web_url": public_url.rstrip("/"),
                    "api_token": "",
                    "timezone": "Asia/Shanghai",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    for config_path in (char_config, vikunja_config):
        config_path.chmod(0o600)
    course_target = runtime / "astrbot" / "plugin_data" / "astrbot_plugin_course"
    course_source = root / "data" / "plugin_data" / "astrbot_plugin_course"
    # Once the runtime directory exists, never restore a deleted or changed binding.
    if not course_target.exists():
        staging = course_target.with_name(".astrbot_plugin_course.seed")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            bindings = course_source / "bindings.json"
            if bindings.is_file():
                shutil.copy2(bindings, staging / "bindings.json")
            if (course_source / "ics").is_dir():
                (staging / "ics").mkdir()
                for calendar in (course_source / "ics").glob("*.ics"):
                    shutil.copy2(calendar, staging / "ics" / calendar.name)
            staging.rename(course_target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "prepare"))
    parser.add_argument("--dns-name", default="")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        if args.action == "check":
            print(check_server(root))
        else:
            prepare_server(root, args.dns_name)
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Deployment preparation failed: {error}", file=sys.stderr)
        sys.exit(1)
