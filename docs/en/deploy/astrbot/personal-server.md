# Personal server: AstrBot, Vikunja, and Tailscale

This private fork deploys its own core and Dashboard, the char/course/Vikunja plugins, and the tracked timetable. It uses two containers, SQLite for Vikunja, and Tailscale on the host. Configure models, WeChat, and a new Vikunja account on the server. WeChat's `weixin_oc` adapter uses outbound long polling, so chatting needs neither a public webhook nor Tailscale on the phone.

## Prepare a fresh server

Choose a plain Ubuntu 22.04 x86_64 system image for this installation. Ubuntu 22.04 amd64 and arm64 are supported; allow roughly 4 GB RAM and 15 GB free disk for the initial source build. Use a sudo-capable account. Reinstalling the system disk in the cloud console removes old applications, credentials, and configuration; the deployment script does not perform this cloud operation. Keep ordinary SSH access and normal outbound connectivity in the cloud security group, without publishing TCP 3456, 6185, 7835, 443, 8443, or 8444 to the internet. Docker Engine 28+ is required; see [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/).

Reinstallation can change the server's SSH host key. If your computer reports `REMOTE HOST IDENTIFICATION HAS CHANGED`, inspect the new fingerprint through the cloud VNC/serial console:

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

After confirming this is the reinstalled instance, run these commands on **your computer** and compare the new connection's fingerprint:

```bash
ssh-keygen -R YOUR_SERVER_IP
ssh ubuntu@YOUR_SERVER_IP
```

Do not disable host-key checking. Use an ordinary SSH terminal for the following server commands. Keep VS Code Remote SSH and other builds closed during the first build; the previous freeze has no confirmed cause without diagnostic logs.

Keep the cloud vendor's existing Ubuntu APT mirror first. Install basic tools with bounded retries/timeouts and start a persistent terminal session:

```bash
sudo apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 -o Acquire::http::Timeout=30 update
sudo apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 -o Acquire::http::Timeout=30 install -y git tmux ca-certificates
tmux new -s astrbot-deploy
```

Continue cloning and installation inside tmux. After a dropped SSH connection, reconnect and run `tmux attach -t astrbot-deploy`; do not start a second deployment. tmux protects against terminal disconnection, not memory exhaustion or a system failure.

If APT downloads fail, back up and replace the Ubuntu sources using [TUNA's Ubuntu instructions](https://mirrors.tuna.tsinghua.edu.cn/help/ubuntu/). The `ubuntu/` repository serves x86; arm64 needs [ubuntu-ports](https://mirrors.tuna.tsinghua.edu.cn/help/ubuntu-ports/). Keep signature checks and preferably the official security-update source. The [Chinese guide](../../../zh/deploy/astrbot/personal-server.md) includes complete Ubuntu 22.04 fallback commands.

On a roughly 4 GB server with no swap, optionally add 4 GB before building. Check `free -h`, `swapon --show`, and `df -h /` first. Run this only with enough disk space and no existing swap; it refuses to overwrite an existing `/swapfile`:

```bash
sudo bash <<'SH'
set -euo pipefail
test ! -e /swapfile
fallocate -l 4G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
if ! grep -qsE '^/swapfile[[:space:]]' /etc/fstab; then
  printf '/swapfile none swap sw 0 0\n' >> /etc/fstab
fi
free -h
SH
```

The script does not create swap or change the host Docker daemon automatically. A working existing server can still be reused after resolving conflicting ports/routes; this fresh setup does not import old service data.

## Clone the private repository

Use an existing repository SSH key or create a read-only GitHub deploy key:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keygen -t ed25519 -f ~/.ssh/astrbot_repo -C astrbot-server
cat ~/.ssh/astrbot_repo.pub
```

Add its public key in repository Settings → Deploy keys. Keep the private key on the server. Add a host to `~/.ssh/config`, preserving existing entries; edit an existing same-name entry instead of appending it again:

```bash
cat >> ~/.ssh/config <<'SSHCONFIG'
Host github-astrbot
    HostName ssh.github.com
    Port 443
    User git
    IdentityFile ~/.ssh/astrbot_repo
    IdentitiesOnly yes
    ConnectTimeout 15
    ServerAliveInterval 30
SSHCONFIG
```

This uses [GitHub's official SSH endpoint on port 443](https://docs.github.com/en/authentication/troubleshooting-ssh/using-ssh-over-the-https-port). Verify the first connection against [GitHub's published fingerprints](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints). If port 443 is unavailable, try `HostName github.com` and `Port 22`. Keep private repositories and credentials away from unknown GitHub proxy services.

Replace the repository placeholder:

```bash
chmod 600 ~/.ssh/config
git clone git@github-astrbot:YOUR_ACCOUNT/YOUR_REPO.git ~/Astrbot_dev
cd ~/Astrbot_dev
```

## Install the host, configure networking, then deploy

Install the host tools before any application image download:

```bash
sudo bash scripts/deploy-server.sh --cn --install-only
```

`--install-only` installs and checks Docker/Compose/Buildx and Tailscale, without logging into Tailscale or building/starting applications. Existing working tools are reused; conflicting Docker packages and an Engine older than 28 need explicit migration using [Docker's official instructions](https://docs.docker.com/engine/install/ubuntu/).

`--cn` selects [TUNA for Docker CE packages](https://mirrors.tuna.tsinghua.edu.cn/help/docker-ce/), Debian packages, and [PyPI](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/), [USTC for Tailscale packages](https://mirrors.ustc.edu.cn/help/tailscale.html), and [npmmirror](https://npmmirror.com/) for npm/pnpm packages. Docker and Tailscale signing keys still come from their official sources. APT signatures, locked dependency versions, and file-integrity checks stay enabled. Container images need a separate Docker Hub mirror. Chromium uses Playwright's official download service with a connection timeout; no third-party browser mirror is selected by default. Omit `--cn` to use default official sources. The build reduces package-download concurrency and Node's memory limit, and runs Dashboard and Python dependency stages sequentially; retry limits do not imply a fixed total deployment deadline.

Build progress output requires Compose 2.19 or newer, including 5.x. The official Compose plugin installed by this fresh setup meets that requirement.

### Tencent Cloud Docker Hub mirror

[Tencent Cloud's official guide](https://cloud.tencent.com/document/product/213/8623) provides `https://mirror.ccs.tencentyun.com` for Tencent's internal network only. This does not require an assumed accelerator menu in the console, and availability of every image/tag is not guaranteed. Other providers need their own supported mirror. No random public proxy is configured by this repository.

On a fresh host without other running workloads, back up and merge the mirror into Docker's JSON configuration without discarding existing fields:

```bash
sudo bash <<'SH'
set -euo pipefail
python3 - <<'PY'
import json
import shutil
from datetime import datetime
from pathlib import Path

path = Path('/etc/docker/daemon.json')
config = json.loads(path.read_text()) if path.exists() else {}
if path.exists():
    shutil.copy2(path, path.with_name('daemon.json.before-astrbot-' + datetime.now().strftime('%Y%m%d-%H%M%S')))
mirrors = config.setdefault('registry-mirrors', [])
mirror = 'https://mirror.ccs.tencentyun.com'
if mirror not in mirrors:
    mirrors.append(mirror)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(config, indent=2) + '\n')
PY
dockerd --validate --config-file=/etc/docker/daemon.json
systemctl restart docker
docker info --format '{{json .RegistryConfig.Mirrors}}'
timeout 300 docker pull node:22-bookworm-slim
timeout 300 docker pull python:3.12-slim-bookworm
timeout 300 docker pull vikunja/vikunja:2.7.0
SH
```

Actual successful pulls validate downloading on this server; a listed mirror in `docker info` alone does not. [Docker registry mirrors](https://docs.docker.com/docker-hub/image-library/mirror/) cover Docker Hub, not arbitrary registries. If this fails, inspect networking and, if needed, remove only the added URL, validate/restart, and try official downloads. Preserve unrelated daemon settings and do not run Docker prune.

### Rejoin Tailscale

Reinstallation removes local Tailscale identity. In the [Machines console](https://login.tailscale.com/admin/machines), remove the old server entry only after confirming that instance was destroyed by reinstallation; preserve your computers and phones. Join the same tailnet on the new server using a nonsensitive hostname:

```bash
sudo tailscale up --hostname=astrbot-server --timeout=60s
```

Open the login URL in your computer's browser. Enable Device approval in [Device management](https://login.tailscale.com/admin/settings/device-management) and approve the new server after checking its identity. Complete approval even if the CLI times out after 60 seconds, then check `sudo tailscale status`. See [device approval](https://tailscale.com/docs/features/access-control/device-management/device-approval).

Enable MagicDNS and HTTPS Certificates in the [DNS console](https://login.tailscale.com/admin/dns). Certificate names enter public certificate-transparency logs, so use nonsensitive machine/tailnet names; see [HTTPS setup](https://tailscale.com/docs/how-to/set-up-https-certificates). The reinstalled server's IP and DNS name may differ from the old ones. Update the device policy in the next section using the actual new device IP before deploying:

```bash
sudo tailscale status
tailscale ip -4
sudo bash scripts/deploy-server.sh --cn
```

The script reads the actual MagicDNS name and uses persistent `tailscale serve --bg` routes. Complete any first-use HTTPS consent link and rerun if needed. Separate `tailscale cert` commands or Funnel are unnecessary. Do not rename the server after deployment without updating application URLs. Docker and Tailscale start at boot.

The terminal identifies each installation, Tailscale, build, image-pull, startup, and HTTPS stage. The AstrBot build is limited to 60 minutes, the Vikunja pull to 15 minutes, and each Serve command to 120 seconds; a timeout exits with the failing stage. The script does not create a new log file. Retain its tmux output for diagnosis, fix the problem, then rerun the same command; runtime data is preserved.

| Page | Private address | Host backend |
| --- | --- | --- |
| Vikunja | `https://SERVER.TAILNET.ts.net/` | `127.0.0.1:3456` |
| Dashboard | `https://SERVER.TAILNET.ts.net:8443/` | `127.0.0.1:6185` |
| Memory panel | `https://SERVER.TAILNET.ts.net:8444/` | `127.0.0.1:7835` |

Use the real URLs printed by the script. The memory panel starts on the administrator's `/记忆面板` command and requires its returned token. A bare link is insufficient. Container data lives in `runtime/astrbot`, `runtime/vikunja/db`, and `runtime/vikunja/files`; the signing secret is persisted in `runtime/secrets/vikunja_secret` and mounted as a Compose secret. `.env`, `runtime/`, and `backups/` are ignored by Git, and runtime state is excluded from the Docker context.

Rerunning preserves existing configuration, secrets, and data. Timetable files are seeded only when the runtime course directory is first created. The installer refuses conflicting host ports, Serve routes, public Funnel routes, and a same-name Compose project owned by another checkout.

## Restrict access to specific devices

Install Tailscale on authorized computers and on phones that need browser access. Log into the same tailnet, enable Device approval in Device management, and approve recognized devices. Record their actual Tailscale IPs and the server IP:

```bash
tailscale ip -4
sudo tailscale serve status
```

For a new tailnet dedicated to this deployment, replace **every** example address in this policy in [Access controls](https://login.tailscale.com/admin/acls). The `hosts` keys are policy aliases, not automatic machine-name lookups. After reinstallation, `astrbot-server` must contain the new server's actual Tailscale IP, not its public IP:

```json
{
  "acls": [],
  "hosts": {
    "astrbot-server": "100.85.23.17",
    "my-laptop": "100.85.23.18",
    "my-desktop": "100.85.23.19",
    "my-phone": "100.85.23.20",
    "unallowed-device": "100.85.23.21"
  },
  "grants": [{
    "src": ["my-laptop", "my-desktop", "my-phone"],
    "dst": ["astrbot-server"],
    "ip": ["tcp:443", "tcp:8443", "tcp:8444"]
  }],
  "tests": [
    {"src": "100.85.23.18", "accept": ["astrbot-server:443", "astrbot-server:8443", "astrbot-server:8444"], "deny": ["astrbot-server:3456", "astrbot-server:6185", "astrbot-server:7835"]},
    {"src": "100.85.23.21", "deny": ["astrbot-server:443", "astrbot-server:8443", "astrbot-server:8444"]}
  ]
}
```

Remove unused hosts. The unallowed-device test needs an actual device; omit it until one is available for validation. The policy does not affect ordinary SSH over the server's public IP. Authorize TCP 22 separately if you want SSH over Tailscale.

On an existing tailnet, merge the rules while preserving needed services. Grants are additive: narrow rules cannot override existing allow-all ACLs/grants. Remove or narrow broader rules that still allow access to this server, then validate in the console. Device-specific sources restrict devices; an email source generally authorizes all devices belonging to that account. See [grant semantics](https://tailscale.com/docs/reference/syntax/grants).

Ensure the console's policy tests pass. After deployment, `sudo tailscale serve status` on the server should show three `tailnet only` entries with the expected loopback backends. Check the actual URLs from authorized and unauthorized devices, then revoke one test device's access and retry with new connections. A successful `tailscale ping` does not validate HTTPS authorization.

Enable MFA/Passkeys for the Tailscale account. Revoke lost devices in the console and review application sessions. Bookmark the URLs and optionally connect the clients at startup. Use Serve; Funnel publishes to the public internet.

## Configure applications and WeChat

Dashboard username defaults to `astrbot`; find the random initial password in private startup logs and change it on first login:

```bash
sudo docker compose logs astrbot
```

Enable Dashboard's existing TOTP feature and save recovery information. Trusted devices reduce repeated challenges. The initial server configuration enables proxy headers so the backend recognizes Serve's HTTPS requests. Configure a model provider with tool calling for natural-language task operations.

Vikunja registration is disabled. Create your account through the CLI, replacing the email. Enter the password at the interactive prompt instead of in command history:

```bash
sudo docker compose exec vikunja /app/vikunja/vikunja user create --username dingyj --email you@example.com
```

Log into Vikunja, enable TOTP, and create an API token permitting plugin operations on projects/views, tasks/comments/relations/labels, labels, and saved filters. Exclude unrelated account administration permissions. In AstrBot's Vikunja plugin configuration, keep the prefilled `vikunja_url=http://vikunja:3456` and the HTTPS `vikunja_web_url`; fill `api_token`, save, and reload. A permission error identifies a missing token capability. The image is pinned to `vikunja/vikunja:2.7.0`.

Create a personal WeChat robot, scan, confirm, and save. The tracked timetable uses instance ID `weixin_test`; keeping that ID and the same WeChat account preserves the binding. Otherwise use `/绑定课表` to upload the tracked ICS file again. Run only one receiver for that WeChat account.

Send `/sid`. Add the **UID** as an AstrBot administrator and the **UMO** to the robot configuration's session whitelist. The Vikunja plugin does not check its QQ whitelist for WeChat, so the core whitelist limits this personal assistant. Memory administrators use UIDs, not UMOs. The core administrator role already permits `/记忆面板`.

Verify `/今日课表`, `/本周课表`, `/todo diag`, `/todo setup`, `/todo board`, a task creation, and `/记忆面板`. Check browser edits from WeChat and an imminent reminder. Memory tokens change on AstrBot restart; request a new link. Compare an authorized device, an unallowed tailnet device, and an internet device; revoke an authorized device and verify access disappears. Check data and valid WeChat credentials survive container and server restarts.

## Update, backup, and restore

Run Compose commands from the checkout root with sudo:

```bash
sudo docker compose ps
sudo docker compose logs --tail=100 astrbot
sudo docker compose logs --tail=100 vikunja
sudo docker compose restart astrbot
sudo docker compose stop
sudo docker compose up -d --wait
```

Create a consistent backup with applications stopped; the exit trap attempts to restart even after a failed archive:

```bash
sudo bash <<'SH'
set -euo pipefail
umask 077
mkdir -p backups
docker compose stop
trap 'docker compose up -d --wait' EXIT
tar -czf "backups/astrbot-$(date +%Y%m%d-%H%M%S).tar.gz" .env runtime
SH
git rev-parse HEAD
sudo ls -lh backups
```

Record the source commit. Archives contain private credentials and memories: keep restricted permissions and private off-host copies, encrypt before using cloud storage, and keep them out of Git. Then update:

```bash
git status --short
git pull --ff-only
sudo bash scripts/deploy-server.sh --cn
```

Preserve source edits before pulling. The three bundled plugins are managed by this repository; changes to their runtime source or WebUI replacements are overwritten on deployment. Configuration and plugin data are preserved.

Restore using the matching source version and replace the archive placeholder:

```bash
sudo docker compose stop
sudo mkdir -p backups
sudo mv runtime "backups/runtime-before-restore-$(date +%Y%m%d-%H%M%S)"
sudo cp .env .env.before-restore
sudo tar -xzf backups/astrbot-YYYYMMDD-HHMMSS.tar.gz
sudo bash scripts/deploy-server.sh --cn
```

On a different server, authorize its Tailscale device and update `.env`'s `VIKUNJA_PUBLIC_URL`, the Vikunja plugin's `vikunja_web_url`, and char's `memory.memory_panel_public_base_url` to its new hostname. Restore the original signing secret with an existing database.

Disable only these routes when needed; do not reset the node's complete Serve configuration:

```bash
sudo tailscale serve --https=443 off
sudo tailscale serve --https=8443 off
sudo tailscale serve --https=8444 off
```

## Troubleshooting and validation boundaries

- SSH disconnects: reconnect and attach to `tmux attach -t astrbot-deploy` before considering a rerun. Do not deploy concurrently.
- The server stops responding: inspect the cloud console. After recovery, check memory/disk space and kernel logs with `sudo journalctl -k -b --no-pager`; use `-b -1` after a reboot. Diagnose memory exhaustion from OOM evidence rather than assuming VS Code or a mirror caused it.
- Docker CE installed but images time out: the package mirror and Docker Hub acceleration are separate. Test real pulls; Tencent's mirror is restricted to its internal network and does not guarantee every tag.
- `--install-only` finishes without pages: finish image-download tests, Tailscale login/approval, and policy setup, then run the full `--cn` command.
- Old URLs/policy fail after reinstalling: update the server IP in policy aliases and use the new actual DNS name; finalize its hostname before the first deployment.
- Application IP/port URLs do not work remotely: use the complete Serve HTTPS URL. Run `serve status` on the server, not the client.
- Ping works but browsing fails: check approvals, grants, server routes, DNS, and client VPN/proxy conflicts. Test with Clash/Mihomo TUN disabled; route `100.64.0.0/10` directly when re-enabling it.
- HTTPS setup waits for authorization: complete the console/CLI link and rerun.
- Memory panel 502: start it through `/记忆面板`. API 403 requires a valid current token; public static assets do not grant access to data.
- Vikunja permissions: its database/files and secret must be owned by UID 1000. Rerunning repairs top-level ownership; preserve ownership when restoring.
- WeChat login expired: rescan and save. Missing reminders require a live session, enabled plugin settings, and actual platform delivery checks.
- SQLite can return `database is locked` when extremely fast writes overlap asynchronous subscription writes. Inspect task state before retrying an unfinished operation; this single-user setup does not promise dense concurrent writes.
- Installation/build/conflict errors: inspect the reported failure and logs, resolve the problem, then rerun. The installer will not replace another service's routes or clear existing data.

On 2026-10-14, local amd64 validation completed the full updated Docker build with Chinese package mirrors and startup of both containers. npmmirror installed all 692 frozen-lock Node packages; TUNA PyPI supplied 159 runtime dependencies with original lock hashes checked; TUNA Debian supplied system packages; Playwright downloaded Chromium from its official service. The Dashboard build uses a 2048 MB Node heap and peaked at roughly 2.32 GiB RSS locally; actual server memory use can differ.

Checks cover all three plugins, Dashboard/core version matching, initial password changes, the internal Vikunja API and private HTTPS links, scoped tokens, board initialization, task/reminder field edits, Chromium rendering, and credentials/tasks/signing-secret persistence after container restart. The memory panel rejects reads and writes with missing or incorrect tokens and accepts valid-token operations against its real SQLite store. Repeated initialization preserves runtime data, including BOM-prefixed configuration saved by AstrBot. The image excludes development runtime state and `.env` files. Relevant pytest tests (62), plugin unittest tests (134), and Ruff checks pass. Tencent's internal Docker Hub acceleration must still be validated with real pulls on the target server.

Fresh Ubuntu package installation, actual ARM64 execution, Tailscale device connectivity and revocation, public-network isolation, and WeChat scanning/chat/reminder delivery still require acceptance on the target server and your devices. See the [complete Chinese operations guide](../../../zh/deploy/astrbot/personal-server.md) for the detailed checklist and troubleshooting table.
