"""Validate the bundled plugin against an optional local Vikunja release binary."""

import asyncio
import os
import socket
from pathlib import Path

import aiohttp
import pytest

from data.plugins.astrbot_plugin_vikunja import bootstrap
from data.plugins.astrbot_plugin_vikunja.vikunja import VikunjaClient


@pytest.mark.integration
@pytest.mark.asyncio
async def test_vikunja_release_preserves_plugin_operations_and_data(tmp_path):
    """Exercise token-scoped operations against a real, isolated server.

    Args:
        tmp_path: Temporary directory for the server database and release test state.
    """
    binary = os.environ.get("VIKUNJA_TEST_BINARY")
    if not binary:
        pytest.skip("Set VIKUNJA_TEST_BINARY to a verified Vikunja 2.7.0 binary.")
    assert Path(binary).is_file()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    secret = tmp_path / "secret"
    secret.write_text("integration-test-signing-secret-only")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("VIKUNJA_")
    }
    env.update(
        {
            "VIKUNJA_DATABASE_TYPE": "sqlite",
            "VIKUNJA_DATABASE_PATH": str(tmp_path / "vikunja.db"),
            "VIKUNJA_FILES_BASEPATH": str(tmp_path / "files"),
            "VIKUNJA_SERVICE_INTERFACE": f"127.0.0.1:{port}",
            "VIKUNJA_SERVICE_PUBLICURL": base_url + "/",
            "VIKUNJA_SERVICE_SECRET_FILE": str(secret),
            "VIKUNJA_SERVICE_ENABLEREGISTRATION": "false",
            "VIKUNJA_MAILER_ENABLED": "false",
            "VIKUNJA_LOG_LEVEL": "ERROR",
        }
    )
    setup = await asyncio.create_subprocess_exec(
        binary,
        "user",
        "create",
        "--username",
        "integration",
        "--email",
        "integration@example.com",
        "--password",
        "temporary-test-password",
        cwd=tmp_path,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, setup_error = await asyncio.wait_for(setup.communicate(), timeout=30)
    assert setup.returncode == 0, setup_error.decode()
    log = (tmp_path / "server.log").open("w")
    server = await asyncio.create_subprocess_exec(
        binary,
        "web",
        cwd=tmp_path,
        env=env,
        stdout=log,
        stderr=asyncio.subprocess.STDOUT,
    )
    client = None
    try:
        for _ in range(100):
            assert server.returncode is None, (tmp_path / "server.log").read_text()
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=1)
                ) as session:
                    async with session.get(base_url + "/api/v1/info") as response:
                        if response.status == 200:
                            info = await response.json()
                            assert (
                                info["auth"]["local"]["registration_enabled"] is False
                            )
                            break
            except (aiohttp.ClientError, OSError):
                await asyncio.sleep(0.1)
        else:
            pytest.fail("Vikunja did not become ready.")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                base_url + "/api/v1/login",
                json={"username": "integration", "password": "temporary-test-password"},
            ) as response:
                assert response.status == 200
                login = await response.json()
        async with aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {login['token']}"}
        ) as session:
            async with session.get(base_url + "/api/v1/routes") as response:
                assert response.status == 200
                routes = await response.json()
            permissions = {
                name: list(operations)
                for name, operations in routes.items()
                if name
                in {
                    "projects",
                    "projects_views",
                    "tasks",
                    "tasks_labels",
                    "tasks_comments",
                    "tasks_relations",
                    "labels",
                    "filters",
                }
            }
            async with session.put(
                base_url + "/api/v1/tokens",
                json={
                    "title": "Plugin integration",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "permissions": permissions,
                },
            ) as response:
                assert response.status == 201
                api_token = await response.json()
        client = VikunjaClient(
            base_url,
            api_token["token"],
            filter_timezone="Asia/Shanghai",
            web_url="https://astrbot.test-tailnet.ts.net",
        )
        report = await bootstrap.apply(client, include_projects=True)
        assert report.board_url.startswith(
            "https://astrbot.test-tailnet.ts.net/projects/"
        )
        first_projects = await client.list_projects()
        inbox = next(
            project for project in first_projects if project["title"] == "Inbox"
        )
        task = await client.create_task(inbox["id"], {"title": "Deployment validation"})
        # Let Vikunja finish asynchronous subscription writes before a simulated user edit.
        # SQLite's read-to-write transaction upgrades can otherwise fail under contention.
        await asyncio.sleep(0.2)
        updated = await client.update_task(
            task["id"], {"title": "Edited in browser", "description": "Persistent task"}
        )
        assert updated["title"] == "Edited in browser"
        detail = await client.get_task(task["id"])
        assert detail["description"] == "Persistent task"
        await bootstrap.apply(client, include_projects=True)
        assert len(await client.list_projects()) == len(first_projects)
        assert len(await client.list_saved_filters()) == len(bootstrap.FILTER_SPECS)
        await client.close()
        server.terminate()
        await asyncio.wait_for(server.wait(), timeout=10)
        # A persisted database must keep the API token and task usable after restart.
        server = await asyncio.create_subprocess_exec(
            binary,
            "web",
            cwd=tmp_path,
            env=env,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
        )
        for _ in range(100):
            try:
                if (await client.get_task(task["id"]))["title"] == "Edited in browser":
                    break
            except Exception:
                await asyncio.sleep(0.1)
        else:
            pytest.fail("Task/session did not survive restart.")
        async with aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {login['token']}"}
        ) as session:
            async with session.get(base_url + "/api/v1/routes") as response:
                # This JWT also verifies that the signing secret file survives restart.
                assert response.status == 200
    finally:
        if client:
            await client.close()
        if server.returncode is None:
            server.terminate()
        try:
            await asyncio.wait_for(server.wait(), timeout=10)
        except TimeoutError:
            server.kill()
            await server.wait()
        log.close()
