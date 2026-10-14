# Personal server: AstrBot, Vikunja, and Tailscale

This private fork deploys its own core and Dashboard, the char/course/Vikunja plugins, and the tracked timetable. It uses two containers, SQLite for Vikunja, and Tailscale on the host. Configure models, WeChat, and a new Vikunja account on the server. WeChat's `weixin_oc` adapter uses outbound long polling, so chatting needs neither a public webhook nor Tailscale on the phone.

## Prepare and deploy

Use Ubuntu 22.04 amd64 or arm64 with a sudo-capable account. Allow roughly 4 GB RAM and 15 GB free disk for the initial source build. Keep SSH access in the cloud security group; do not publish application or Serve HTTPS ports to the internet. Docker Engine 28+ is required for fixed localhost publishing behavior; see [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/).

```bash
ssh ubuntu@YOUR_SERVER_IP
sudo apt-get update
sudo apt-get install -y git
```

Use an existing repository SSH key or create a read-only GitHub deploy key:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/astrbot_repo -C astrbot-server
cat ~/.ssh/astrbot_repo.pub
```

Add its public key in repository Settings → Deploy keys. Keep the private key on the server. Add a host to `~/.ssh/config`, preserving existing entries:

```sshconfig
Host github-astrbot
    HostName github.com
    User git
    IdentityFile ~/.ssh/astrbot_repo
    IdentitiesOnly yes
```

Replace the repository placeholder and verify GitHub's SSH fingerprint on first connection:

```bash
chmod 600 ~/.ssh/config
git clone git@github-astrbot:YOUR_ACCOUNT/YOUR_REPO.git ~/Astrbot_dev
cd ~/Astrbot_dev
sudo bash scripts/deploy-server.sh
```

The installer uses official package repositories to install missing Docker/Compose/Buildx, Tailscale, Python 3, and rsync. Existing working installations are reused. Conflicting Docker packages or an Engine older than 28 require an explicit migration using [Docker's official instructions](https://docs.docker.com/engine/install/ubuntu/).

Open the first Tailscale login URL, approve the server, and enable MagicDNS and HTTPS Certificates in the admin console. Login waits for up to 60 seconds; complete authorization and rerun the same command after a timeout. HTTPS setup may also return an authorization link. Certificate names enter public certificate transparency logs, so use a nonsensitive server/tailnet name. Docker and Tailscale start at boot; Serve uses persistent background routes.

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

For a new tailnet dedicated to this deployment, replace **every** example address in this policy:

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
sudo bash scripts/deploy-server.sh
```

Preserve source edits before pulling. The three bundled plugins are managed by this repository; changes to their runtime source or WebUI replacements are overwritten on deployment. Configuration and plugin data are preserved.

Restore using the matching source version and replace the archive placeholder:

```bash
sudo docker compose stop
sudo mkdir -p backups
sudo mv runtime "backups/runtime-before-restore-$(date +%Y%m%d-%H%M%S)"
sudo cp .env .env.before-restore
sudo tar -xzf backups/astrbot-YYYYMMDD-HHMMSS.tar.gz
sudo bash scripts/deploy-server.sh
```

On a different server, authorize its Tailscale device and update `.env`'s `VIKUNJA_PUBLIC_URL`, the Vikunja plugin's `vikunja_web_url`, and char's `memory.memory_panel_public_base_url` to its new hostname. Restore the original signing secret with an existing database.

Disable only these routes when needed; do not reset the node's complete Serve configuration:

```bash
sudo tailscale serve --https=443 off
sudo tailscale serve --https=8443 off
sudo tailscale serve --https=8444 off
```

## Troubleshooting and validation boundaries

- Application IP/port URLs do not work remotely: use the complete Serve HTTPS URL. Run `serve status` on the server, not the client.
- Ping works but browsing fails: check approvals, grants, server routes, DNS, and client VPN/proxy conflicts. Test with Clash/Mihomo TUN disabled; route `100.64.0.0/10` directly when re-enabling it.
- HTTPS setup waits for authorization: complete the console/CLI link and rerun.
- Memory panel 502: start it through `/记忆面板`. API 403 requires a valid current token; public static assets do not grant access to data.
- Vikunja permissions: its database/files and secret must be owned by UID 1000. Rerunning repairs top-level ownership; preserve ownership when restoring.
- WeChat login expired: rescan and save. Missing reminders require a live session, enabled plugin settings, and actual platform delivery checks.
- SQLite can return `database is locked` when extremely fast writes overlap asynchronous subscription writes. Inspect task state before retrying an unfinished operation; this single-user setup does not promise dense concurrent writes.
- Installation/build/conflict errors: inspect the reported failure and logs, resolve the problem, then rerun. The installer will not replace another service's routes or clear existing data.

On 2026-10-14, local amd64 validation completed a real Docker image build and startup of both containers. Checks cover all three plugins, Dashboard/core asset version matching, initial password changes, the internal Vikunja API and private HTTPS links, scoped tokens, board initialization, task/reminder field edits, Chromium rendering, and credentials/tokens/tasks/signing-secret persistence after container restart. The memory panel rejects reads and writes with missing or incorrect tokens and accepts valid-token operations against its real SQLite store. Repeated initialization preserves runtime data, including BOM-prefixed configuration saved by AstrBot. The image excludes development runtime state and `.env` files. Relevant pytest tests (56), plugin unittest tests (134), and Ruff checks pass.

Fresh Ubuntu package installation, actual ARM64 execution, Tailscale device connectivity and revocation, public-network isolation, and WeChat scanning/chat/reminder delivery still require acceptance on the target server and your devices. See the [complete Chinese operations guide](../../../zh/deploy/astrbot/personal-server.md) for the detailed checklist and troubleshooting table.
