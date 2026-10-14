#!/usr/bin/env bash
# Deploy this checkout on Ubuntu 22.04 without exposing application ports publicly.
set -Eeuo pipefail
umask 077
trap 'printf "Deployment failed at line %s. Fix the error and rerun the same command.\n" "$LINENO" >&2' ERR

if [[ $(id -u) -ne 0 ]]; then
    echo "Run: sudo bash scripts/deploy-server.sh" >&2
    exit 1
fi
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$ROOT"
source /etc/os-release
if [[ ${ID:-} != ubuntu || ${VERSION_ID:-} != 22.04 ]]; then
    echo "This installer supports Ubuntu 22.04. Use the documented Compose setup on other systems." >&2
    exit 1
fi
ARCH=$(dpkg --print-architecture)
if [[ $ARCH != amd64 && $ARCH != arm64 ]]; then
    echo "Unsupported architecture: $ARCH. Expected amd64 or arm64." >&2
    exit 1
fi
if [[ ! -d .git ]]; then
    echo "Deploy from a Git clone of the private repository." >&2
    exit 1
fi
for plugin in astrbot_plugin_char astrbot_plugin_course astrbot_plugin_vikunja; do
    test -f "data/plugins/$plugin/main.py"
done

if ! command -v curl >/dev/null || ! command -v rsync >/dev/null || ! command -v python3 >/dev/null; then
    apt-get update
    apt-get install -y ca-certificates curl rsync python3
fi
# Preserve an existing working Docker installation; install official packages only if absent.
if ! command -v docker >/dev/null; then
    if (dpkg-query -W -f='${Status}' docker.io podman-docker containerd runc 2>/dev/null || true) | grep -q 'install ok installed'; then
        echo "Conflicting Docker packages found. Follow Docker's migration instructions first." >&2
        exit 1
    fi
    apt-get update
    apt-get install -y ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu jammy stable\n' "$ARCH" > /etc/apt/sources.list.d/docker.list
    chmod 0644 /etc/apt/sources.list.d/docker.list
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
elif ! docker compose version >/dev/null 2>&1 || ! docker buildx version >/dev/null 2>&1; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu jammy stable\n' "$ARCH" > /etc/apt/sources.list.d/docker.list
    chmod 0644 /etc/apt/sources.list.d/docker.list
    apt-get update
    apt-get install -y docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
docker info >/dev/null
docker compose version
DOCKER_MAJOR=$(docker version --format '{{.Server.Version}}' | cut -d . -f 1)
if [[ ! $DOCKER_MAJOR =~ ^[0-9]+$ || $DOCKER_MAJOR -lt 28 ]]; then
    echo "Docker Engine 28+ is required for the localhost port isolation fix. Upgrade Docker and rerun." >&2
    exit 1
fi

if ! command -v tailscale >/dev/null; then
    install -m 0755 -d /usr/share/keyrings
    curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/jammy.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg
    curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/jammy.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list
    chmod 0644 /usr/share/keyrings/tailscale-archive-keyring.gpg /etc/apt/sources.list.d/tailscale.list
    apt-get update
    apt-get install -y tailscale
fi
systemctl enable --now tailscaled
TS_STATE=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState", ""))') || TS_STATE=NeedsLogin
if [[ $TS_STATE != Running ]]; then
    echo "Open the Tailscale login URL below and approve the server in the admin console."
    tailscale up --timeout=60s
fi
DNS_NAME=$(python3 scripts/deploy_server.py check)
echo "Enable MagicDNS, HTTPS certificates, device approval, and the device-specific access policy in the Tailscale console."
echo "Required private HTTPS ports: 443, 8443, 8444. See docs/zh/deploy/astrbot/personal-server.md."
python3 scripts/deploy_server.py prepare --dns-name "$DNS_NAME"
chown 1000:1000 runtime/vikunja/db runtime/vikunja/files runtime/secrets/vikunja_secret
COMPOSE=(docker compose --project-name astrbot-personal --project-directory "$ROOT" --env-file "$ROOT/.env" --file "$ROOT/compose.yml")
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" build astrbot
"${COMPOSE[@]}" pull vikunja

# Stop only our AstrBot while replacing its plugin code. Config and plugin_data are separate.
"${COMPOSE[@]}" stop astrbot
mkdir -p runtime/astrbot/plugins
for plugin in astrbot_plugin_char astrbot_plugin_course astrbot_plugin_vikunja; do
    rsync -a --delete --exclude='.git' --exclude='__pycache__' --exclude='.ruff_cache' \
        --exclude='.pytest_cache' --exclude='.env*' --exclude='data' --exclude='cache' \
        --exclude='avatars' --exclude='background_images*' --exclude='images_tmp' \
        --exclude='*.db*' --exclude='*.sqlite*' \
        "data/plugins/$plugin/" "runtime/astrbot/plugins/$plugin/"
done
"${COMPOSE[@]}" up -d --wait --wait-timeout 240
READY=false
for attempt in {1..30}; do
    if curl -fsS --max-time 5 http://127.0.0.1:3456/api/v1/info >/dev/null; then
        READY=true
        break
    fi
    sleep 2
done
if [[ $READY != true ]]; then
    echo "Vikunja did not become ready. Inspect: sudo docker compose logs vikunja" >&2
    exit 1
fi
# Recheck immediately before configuring Serve; never reset or overwrite other routes.
python3 scripts/deploy_server.py check >/dev/null
tailscale serve --bg --https=443 http://127.0.0.1:3456
tailscale serve --bg --https=8443 http://127.0.0.1:6185
tailscale serve --bg --https=8444 http://127.0.0.1:7835
"${COMPOSE[@]}" ps
tailscale serve status
printf '\nDeployment completed. Private URLs:\nVikunja: https://%s/\nDashboard: https://%s:8443/\nMemory panel: https://%s:8444/\n' "$DNS_NAME" "$DNS_NAME" "$DNS_NAME"
echo "First use: approve client devices and restrict access; change the Dashboard password; create the Vikunja account and API token; configure the model and scan WeChat."
echo "The memory panel starts on /记忆面板 and requires the token returned to an AstrBot administrator."
echo "Full instructions: docs/zh/deploy/astrbot/personal-server.md"
