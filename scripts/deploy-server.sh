#!/usr/bin/env bash
# Deploy this checkout on Ubuntu 22.04 without exposing application ports publicly.
set -Eeuo pipefail
umask 077
STAGE=preflight
trap 'code=$?; printf "Deployment failed during %s at line %s (exit %s). Fix the error and rerun the same command.\n" "$STAGE" "$LINENO" "$code" >&2; exit "$code"' ERR

CN=false
INSTALL_ONLY=false
for argument in "$@"; do
    case "$argument" in
        --cn) CN=true ;;
        --install-only) INSTALL_ONLY=true ;;
        --help)
            echo "Usage: sudo bash scripts/deploy-server.sh [--cn] [--install-only]"
            echo "--cn: use documented Chinese package mirrors; Docker Hub acceleration is configured separately."
            echo "--install-only: install Docker/Compose/Tailscale without logging in or starting applications."
            exit 0
            ;;
        *) echo "Unknown option: $argument. Use --help." >&2; exit 2 ;;
    esac
done

DOCKER_REPO=https://download.docker.com
TAILSCALE_REPO=https://pkgs.tailscale.com/stable
if [[ $CN == true ]]; then
    DOCKER_REPO=https://mirrors.tuna.tsinghua.edu.cn/docker-ce
    TAILSCALE_REPO=https://mirrors.ustc.edu.cn/tailscale
    export PYPI_INDEX_URL=${PYPI_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}
    export NPM_REGISTRY=${NPM_REGISTRY:-https://registry.npmmirror.com}
    export DEBIAN_MIRROR=${DEBIAN_MIRROR:-https://mirrors.tuna.tsinghua.edu.cn/debian}
    export DEBIAN_SECURITY_MIRROR=${DEBIAN_SECURITY_MIRROR:-https://mirrors.tuna.tsinghua.edu.cn/debian-security}
fi
APT_GET=(apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 -o Acquire::http::Timeout=30 -o DPkg::Lock::Timeout=120)

# Repository keys remain official; the small downloads have bounded retries.
fetch() {
    curl -fSL --connect-timeout 10 --max-time 120 --retry 2 --retry-delay 2 "$1" -o "$2"
}

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

STAGE="host packages"
echo "[1/6] Installing missing host dependencies; keeping the existing Ubuntu package sources."
if ! command -v curl >/dev/null || ! command -v rsync >/dev/null || ! command -v python3 >/dev/null; then
    "${APT_GET[@]}" update
    "${APT_GET[@]}" install -y ca-certificates curl rsync python3
fi
# Preserve an existing working Docker installation; install official packages only if absent.
if ! command -v docker >/dev/null; then
    if (dpkg-query -W -f='${Status}' docker.io podman-docker containerd runc 2>/dev/null || true) | grep -q 'install ok installed'; then
        echo "Conflicting Docker packages found. Follow Docker's migration instructions first." >&2
        exit 1
    fi
    "${APT_GET[@]}" update
    "${APT_GET[@]}" install -y ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    fetch https://download.docker.com/linux/ubuntu/gpg /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] %s/linux/ubuntu jammy stable\n' "$ARCH" "$DOCKER_REPO" > /etc/apt/sources.list.d/docker.list
    chmod 0644 /etc/apt/sources.list.d/docker.list
    "${APT_GET[@]}" update
    "${APT_GET[@]}" install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
elif ! docker compose version >/dev/null 2>&1 || ! docker buildx version >/dev/null 2>&1; then
    install -m 0755 -d /etc/apt/keyrings
    fetch https://download.docker.com/linux/ubuntu/gpg /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] %s/linux/ubuntu jammy stable\n' "$ARCH" "$DOCKER_REPO" > /etc/apt/sources.list.d/docker.list
    chmod 0644 /etc/apt/sources.list.d/docker.list
    "${APT_GET[@]}" update
    "${APT_GET[@]}" install -y docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
timeout --foreground 30s docker info >/dev/null
docker compose version
DOCKER_MAJOR=$(timeout --foreground 30s docker version --format '{{.Server.Version}}' | cut -d . -f 1)
if [[ ! $DOCKER_MAJOR =~ ^[0-9]+$ || $DOCKER_MAJOR -lt 28 ]]; then
    echo "Docker Engine 28+ is required for the localhost port isolation fix. Upgrade Docker and rerun." >&2
    exit 1
fi

if ! command -v tailscale >/dev/null; then
    install -m 0755 -d /usr/share/keyrings
    fetch https://pkgs.tailscale.com/stable/ubuntu/jammy.noarmor.gpg /usr/share/keyrings/tailscale-archive-keyring.gpg
    printf 'deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] %s/ubuntu jammy main\n' "$TAILSCALE_REPO" > /etc/apt/sources.list.d/tailscale.list
    chmod 0644 /usr/share/keyrings/tailscale-archive-keyring.gpg /etc/apt/sources.list.d/tailscale.list
    "${APT_GET[@]}" update
    "${APT_GET[@]}" install -y tailscale
fi
systemctl enable --now tailscaled
if [[ $INSTALL_ONLY == true ]]; then
    echo "Host installation completed. Configure Docker Hub acceleration and Tailscale, then rerun without --install-only."
    exit 0
fi

STAGE="Tailscale and runtime preparation"
echo "[2/6] Checking Tailscale, private routes, and runtime configuration."
TS_STATE=$(timeout --foreground 30s tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState", ""))') || TS_STATE=NeedsLogin
if [[ $TS_STATE != Running ]]; then
    echo "Open the Tailscale login URL below and approve the server in the admin console."
    timeout --foreground 75s tailscale up --timeout=60s
fi
DNS_NAME=$(python3 scripts/deploy_server.py check)
echo "Enable MagicDNS, HTTPS certificates, device approval, and the device-specific access policy in the Tailscale console."
echo "Required private HTTPS ports: 443, 8443, 8444. See docs/zh/deploy/astrbot/personal-server.md."
python3 scripts/deploy_server.py prepare --dns-name "$DNS_NAME"
chown 1000:1000 runtime/vikunja/db runtime/vikunja/files runtime/secrets/vikunja_secret
COMPOSE=(docker compose --project-name astrbot-personal --project-directory "$ROOT" --env-file "$ROOT/.env" --file "$ROOT/compose.yml")
"${COMPOSE[@]}" config --quiet
STAGE="AstrBot image build"
echo "[3/6] Building AstrBot and Dashboard (60-minute limit); download and build progress follows."
timeout --foreground 60m "${COMPOSE[@]}" --progress plain build astrbot
STAGE="Vikunja image pull"
echo "[4/6] Pulling Vikunja 2.7.0 (15-minute limit)."
timeout --foreground 15m "${COMPOSE[@]}" pull vikunja

STAGE="container startup"
echo "[5/6] Synchronizing plugins and starting both containers."
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
STAGE="private HTTPS configuration"
echo "[6/6] Configuring Tailscale Serve; each command has a 120-second limit."
timeout --foreground 120s tailscale serve --bg --https=443 http://127.0.0.1:3456
timeout --foreground 120s tailscale serve --bg --https=8443 http://127.0.0.1:6185
timeout --foreground 120s tailscale serve --bg --https=8444 http://127.0.0.1:7835
"${COMPOSE[@]}" ps
tailscale serve status
printf '\nDeployment completed. Private URLs:\nVikunja: https://%s/\nDashboard: https://%s:8443/\nMemory panel: https://%s:8444/\n' "$DNS_NAME" "$DNS_NAME" "$DNS_NAME"
echo "First use: approve client devices and restrict access; change the Dashboard password; create the Vikunja account and API token; configure the model and scan WeChat."
echo "The memory panel starts on /记忆面板 and requires the token returned to an AstrBot administrator."
echo "Full instructions: docs/zh/deploy/astrbot/personal-server.md"
