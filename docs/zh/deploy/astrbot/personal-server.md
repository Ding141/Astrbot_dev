# 个人服务器部署：AstrBot、Vikunja 与 Tailscale

本指南用于这个私有分支：Ubuntu 22.04 上从源码构建 AstrBot 和 Dashboard，运行 char、course、Vikunja 三个插件，保留仓库中的课表。模型、微信和 Vikunja 账号在服务器重新配置。手机微信通过现有 `weixin_oc` 长轮询接口聊天，无需打开 Tailscale，也不需要公网 Webhook。

## 1. 部署结果与服务器准备

架构为两个 Docker 容器和宿主机 Tailscale。Vikunja 使用 SQLite；记忆面板由 char 插件提供。

| 网页 | 实际 HTTPS 地址 | 宿主机转发目标 | 认证 |
| --- | --- | --- | --- |
| Vikunja | `https://服务器名.tailnet.ts.net/` | `127.0.0.1:3456` | Vikunja 账号密码，可启用 TOTP |
| Dashboard | `https://服务器名.tailnet.ts.net:8443/` | `127.0.0.1:6185` | AstrBot 账号密码，可启用 TOTP |
| 记忆面板 | `https://服务器名.tailnet.ts.net:8444/` | `127.0.0.1:7835` | `/记忆面板` 返回的临时 Token |

域名以脚本输出为准，不能照抄示例。网页访问时电脑或手机需要连接同一 tailnet；微信聊天不受客户端 Tailscale 状态影响。记忆面板默认按需启动，首次部署后须在微信发送 `/记忆面板`，直接访问固定网址但没有 Token 会被拒绝。

目标系统是 Ubuntu 22.04、64 位 amd64 或 arm64；本次全新安装优先选择 Ubuntu 22.04 x86_64 的普通系统镜像。建议至少 2 核、4 GB 内存及 15 GB 可用磁盘用于首次源码构建；本地执行验证的架构见本文末尾。使用具有 sudo 权限的普通账号。脚本安装缺少的 Docker、Compose、Buildx、Tailscale、Python 3 和 rsync，启用 Docker 与 Tailscale 开机启动。使用 Docker Engine 28+，因为旧版本的 localhost 端口发布存在同网段访问问题，详见 [Docker 端口发布说明](https://docs.docker.com/engine/network/port-publishing/)。

### 重装后首次登录

在云控制台重装系统会清除系统盘上的旧业务数据、密钥和配置；这是手动云端操作，部署脚本不会替你重装。重装完成后，重新核对公网 IP 和登录账号。云安全组保留 SSH 登录所需规则，不开放 TCP 3456、6185、7835、443、8443、8444；保留正常出站联网。Serve 的 HTTPS 入口只对 tailnet 提供服务。

重装可能改变 SSH 主机密钥。如果自己的电脑提示 `REMOTE HOST IDENTIFICATION HAS CHANGED`，先从云控制台的 VNC/串口终端查看新指纹：

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

确认是刚重装的实例后，在**自己的电脑**移除这个 IP 的旧记录，再登录并核对提示中的新指纹；不能直接关闭主机密钥校验：

```bash
ssh-keygen -R YOUR_SERVER_IP
ssh ubuntu@YOUR_SERVER_IP
```

接下来的命令都在**服务器的普通 SSH 终端**执行。首次构建期间先不使用 VS Code Remote SSH，也不要同时启动其他构建任务；目前没有足够日志确定此前卡死的原因。

腾讯云系统镜像已有的云厂商 Ubuntu APT 源优先沿用。先安装少量基础工具，给下载设置有限重试和超时：

```bash
sudo apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 -o Acquire::http::Timeout=30 update
sudo apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 -o Acquire::http::Timeout=30 install -y git tmux ca-certificates
tmux new -s astrbot-deploy
```

后面的 clone、安装和构建在这个 tmux 会话中进行。SSH 意外断开后重新登录，执行 `tmux attach -t astrbot-deploy` 回到原进程，不要同时重跑部署。tmux 能保留断线时的进程，但不能防止内存不足或系统故障。

若基础 APT 下载失败，再按[清华 Ubuntu 镜像说明](https://mirrors.tuna.tsinghua.edu.cn/help/ubuntu/)替换 Ubuntu 源；amd64 使用 `ubuntu/`，arm64 使用 [ubuntu-ports](https://mirrors.tuna.tsinghua.edu.cn/help/ubuntu-ports/)。只在**全新 Ubuntu 22.04** 上使用下面的备用命令，它会备份并替换传统 `sources.list`；安全更新仍保留官方源，APT 继续验证 Ubuntu 签名：

```bash
sudo bash <<'SH'
set -euo pipefail
. /etc/os-release
test "$ID" = ubuntu && test "$VERSION_ID" = 22.04
case "$(dpkg --print-architecture)" in
  amd64) task_ubuntu_mirror=https://mirrors.tuna.tsinghua.edu.cn/ubuntu; task_security_mirror=http://security.ubuntu.com/ubuntu ;;
  arm64) task_ubuntu_mirror=https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports; task_security_mirror=http://ports.ubuntu.com/ubuntu-ports ;;
  *) exit 1 ;;
esac
cp -a /etc/apt/sources.list "/etc/apt/sources.list.before-astrbot-$(date +%Y%m%d-%H%M%S)"
cat > /etc/apt/sources.list <<EOF
deb $task_ubuntu_mirror jammy main restricted universe multiverse
deb $task_ubuntu_mirror jammy-updates main restricted universe multiverse
deb $task_ubuntu_mirror jammy-backports main restricted universe multiverse
deb $task_security_mirror jammy-security main restricted universe multiverse
EOF
apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 -o Acquire::http::Timeout=30 update
apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 -o Acquire::http::Timeout=30 install -y git tmux ca-certificates
SH
```

约 4 GB 内存且没有 swap 时，可在构建前手动增加 4 GB swap。先看 `free -h`、`swapon --show` 和 `df -h /`；只有尚无 swap、`/swapfile` 不存在且磁盘空间足够时再执行。已有 swap 文件时此命令会停止，不覆盖它：

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

部署脚本不会自动配置 swap，也不会更改宿主机 Docker daemon。已有正常服务器仍可复用，但旧端口和 Serve 路由需先处理；本节重装流程不导入旧服务数据。

## 2. clone 私有仓库

服务器已有能访问该仓库的 SSH 密钥时，直接使用。否则创建只用于读取仓库的密钥：

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keygen -t ed25519 -f ~/.ssh/astrbot_repo -C astrbot-server
cat ~/.ssh/astrbot_repo.pub
```

在 GitHub 仓库的 Settings → Deploy keys 中添加公钥，保持只读。私钥只留在服务器，不能复制到仓库或聊天中。向 `~/.ssh/config` 添加以下 Host；已有同名项时编辑该项，不重复添加：

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

```bash
chmod 600 ~/.ssh/config
git clone git@github-astrbot:YOUR_ACCOUNT/YOUR_REPO.git ~/Astrbot_dev
cd ~/Astrbot_dev
```

替换 `YOUR_ACCOUNT/YOUR_REPO`。这里使用 [GitHub 官方 SSH 443 入口](https://docs.github.com/en/authentication/troubleshooting-ssh/using-ssh-over-the-https-port)，首次连接按 [GitHub 官方指纹](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)核对提示。443 不通时可改为 `HostName github.com`、`Port 22` 再测试。私有仓库不要交给来源不明的 GitHub 下载代理，也不要把 Token 或私钥写入 clone 地址。

## 3. 分阶段安装与部署

在仓库根目录，先只安装基础环境，便于在拉取应用镜像前配置网络：

```bash
sudo bash scripts/deploy-server.sh --cn --install-only
```

`--install-only` 安装并检查 Docker、Compose、Buildx 和 Tailscale后退出，不登录 Tailscale、不构建或启动应用。`--cn` 为这次执行选择国内包镜像；复用已有可用环境，不自动卸载冲突的软件包。若现有 Docker 低于 28，先按[官方 Ubuntu 安装/升级步骤](https://docs.docker.com/engine/install/ubuntu/)升级。

构建进度输出需要 Compose 2.19 或更高版本（包括 5.x）；本流程新安装的官方 Compose 插件符合要求。

| 下载内容 | `--cn` 使用的来源 |
| --- | --- |
| Docker CE 安装包 | [清华 Docker CE 镜像](https://mirrors.tuna.tsinghua.edu.cn/help/docker-ce/)；签名密钥仍从 Docker 官方取得 |
| Tailscale 安装包 | [USTC Tailscale 镜像](https://mirrors.ustc.edu.cn/help/tailscale.html)；签名密钥仍从 Tailscale 官方取得 |
| 镜像内 Debian 系统包 | 清华 Debian 镜像，继续验证 Debian 签名 |
| Python 依赖和 uv | [清华 PyPI](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)，保持锁定版本及依赖文件散列校验 |
| pnpm 和 Dashboard 依赖 | [npmmirror](https://npmmirror.com/)，保持锁文件和完整性校验 |
| Chromium | Playwright 官方下载源，设置连接超时；没有默认配置第三方浏览器镜像 |
| Node/Python/Vikunja 容器镜像 | Docker Hub，按下面说明单独配置宿主机加速 |

这些镜像不保证在每个网络上都更快；脚本报告下载失败时先确认具体来源，不会通过关闭 TLS、APT 签名或散列校验来绕过问题。省略 `--cn` 使用默认官方来源。构建降低包下载并发和 Node 内存上限，分阶段构建 Dashboard 与 Python 依赖；有限超时和重试不等于固定的整体部署时限。

### 腾讯云 Docker Hub 加速

腾讯云[官方文档](https://cloud.tencent.com/document/product/213/8623)提供 `https://mirror.ccs.tencentyun.com`，仅支持腾讯云内网访问，不需要假定控制台存在“加速器”入口。它不保证包含所有镜像或最新标签。其他云厂商只使用自己账号可用的官方镜像服务；本仓库不预置随机公共代理。

在刚重装、尚未启动其他业务的实例上，下面命令备份已有 Docker 配置并合并这一项，保留其他 JSON 字段，然后检查并重启 Docker：

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

只有这几次实际拉取成功才能确认该服务器的容器镜像下载可用；`docker info` 列出地址并不代表镜像可下载。[Docker registry mirror](https://docs.docker.com/docker-hub/image-library/mirror/)只处理 Docker Hub，不能加速任意 registry。加速失败时不要连续更换未知代理：检查网络，必要时从 JSON 中移除刚添加的这一项、校验后重启，再测试官方下载。不要清空其他 Docker 配置，也不要执行 Docker prune。

### 重新加入 Tailscale

系统重装后本机 Tailscale 登录态不存在，需重新加入原 tailnet。先在 [Machines 控制台](https://login.tailscale.com/admin/machines)核对并移除已被重装销毁的旧服务器节点，保留电脑和手机；再在新服务器登录，使用不包含敏感信息的机器名：

```bash
sudo tailscale up --hostname=astrbot-server --timeout=60s
```

在自己的电脑浏览器打开终端给出的登录链接，登录同一账号，并在控制台批准新服务器。60 秒超时不代表需要重装；完成登录和审批后检查 `sudo tailscale status`。在 [Device management](https://login.tailscale.com/admin/settings/device-management)启用 Device approval，核对并批准新节点。[设备审批说明](https://tailscale.com/docs/features/access-control/device-management/device-approval)

在 [DNS 控制台](https://login.tailscale.com/admin/dns)启用 MagicDNS 和 HTTPS Certificates。HTTPS 域名会进入公开证书透明度日志，启用前确认机器名与 tailnet 名称不含敏感信息。[证书说明](https://tailscale.com/docs/how-to/set-up-https-certificates)

**重装后的 Tailscale IP 和完整域名以新设备为准**，不能直接沿用旧地址。先按第 4 节更新设备策略，再回到仓库执行完整部署：

```bash
sudo tailscale status
tailscale ip -4
sudo bash scripts/deploy-server.sh --cn
```

脚本使用实际 MagicDNS 名称生成配置，通过 `tailscale serve --bg` 保存三个 HTTPS 入口。首次 Serve 若要求 HTTPS 确认，完成提示的链接后重跑即可；不需要单独运行 `tailscale cert` 或开启 Funnel。部署后不要随意改机器名：域名变化时还需同步应用配置。

终端会按阶段显示环境安装、Tailscale、构建、镜像拉取、启动与 HTTPS 配置。AstrBot 构建最多等待 60 分钟，Vikunja 拉取最多 15 分钟，每个 Serve 配置命令最多 120 秒；超时会失败退出，保留失败阶段。脚本不生成新的日志文件，tmux 中的原输出用于定位；修复后重新执行同一个命令，不会清空已有运行数据。

脚本成功后输出三个实际网址以及容器和 Serve 状态。配置保存在：

| 位置 | 内容 |
| --- | --- |
| `.env` | 时区、Vikunja 浏览器地址 |
| `runtime/astrbot/` | 核心配置、微信登录态、插件配置、数据库和插件运行数据 |
| `runtime/astrbot/plugins/` | 部署时同步的三个插件源码与静态资源 |
| `runtime/vikunja/db/` | Vikunja SQLite 数据库 |
| `runtime/vikunja/files/` | Vikunja 附件 |
| `runtime/secrets/vikunja_secret` | 首次生成的持久签名密钥 |

`.env`、`runtime/`、`backups/` 都被 Git 忽略。密钥通过 Compose secret 文件供 Vikunja 读取；数据库、附件与密钥文件归 UID 1000 所有。运行目录限制访问权限，需要使用 sudo 查看。Docker 构建采用输入白名单，开发环境 `data/`、课表、运行数据库、`.env` 和微信登录态不会进入镜像。

重复运行会保留已有配置、密钥、课表和数据库。插件源码重新同步；运行课表只在首次创建对应目录时导入，之后不会复活你已经删除的绑定。固定端口或 Serve 路由被其他服务占用时，脚本失败并报告冲突。它只接受已有的同地址、同目标路由，保留其他端口的 Serve 配置。

## 4. 只允许指定设备访问

部署完成仅表示服务已准备好，完成下面的策略后才能认为“指定设备访问”配置完成。

1. 在自己的电脑安装 [Tailscale 客户端](https://tailscale.com/download)，登录同一 tailnet；需要在手机浏览器管理网页时也安装手机客户端。
2. 在管理控制台 Device management 启用 Device approval，核对并批准服务器与自己的设备。
3. 在 Machines 列表记录每台设备的 Tailscale IP。服务器也可执行：

```bash
tailscale ip -4
sudo tailscale serve status
```

4. 在 [Access controls](https://login.tailscale.com/admin/acls) 设置按设备放行的策略。下方仅用于本项目专用的新 tailnet，所有示例 IP 必须替换为实际地址；第三台设备不需要时删除对应项。`hosts` 是策略内的地址别名，不会自动查找同名设备；重装后必须把 `astrbot-server` 改为新服务器的 Tailscale IP，不能填写公网 IP。

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
  "grants": [
    {
      "src": ["my-laptop", "my-desktop", "my-phone"],
      "dst": ["astrbot-server"],
      "ip": ["tcp:443", "tcp:8443", "tcp:8444"]
    }
  ],
  "tests": [
    {
      "src": "100.85.23.18",
      "accept": ["astrbot-server:443", "astrbot-server:8443", "astrbot-server:8444"],
      "deny": ["astrbot-server:6185", "astrbot-server:7835", "astrbot-server:3456"]
    },
    {
      "src": "100.85.23.21",
      "deny": ["astrbot-server:443", "astrbot-server:8443", "astrbot-server:8444"]
    }
  ]
}
```

`unallowed-device` 是用于验证拒绝访问的已加入设备；没有此设备时删掉这个 host 和对应测试，之后另用未授权设备做实机验证。策略不会影响当前通过服务器公网 IP 进行的普通 SSH 登录。若要通过 Tailscale SSH/普通 SSH 访问服务器，还需为具体管理设备另行授权 TCP 22。

已有 tailnet 上先保留原策略，合并本项目规则并核对其他服务。**允许规则叠加**，窄规则不能抵消 `* → *:*` 的旧 ACL 或 `src/dst/ip` 全为 `*` 的 grant；必须移除或收紧覆盖本服务器的广泛放行规则，并通过控制台校验。[Grants 说明](https://tailscale.com/docs/reference/syntax/grants)

设备审批控制加入网络，grants 控制访问端口。用账号邮箱作为 source 通常会放行该账号的所有设备，本指南使用具体设备 IP。设备移除后重新加入，需要核对地址并更新规则。

保存前检查控制台的策略测试全部通过。完整部署后在服务器执行 `sudo tailscale serve status`，应该看到三条 `tailnet only` 入口以及表格中的回环目标；在已授权设备浏览器访问完整域名，在未授权设备测试同样地址。再移除一台测试设备的授权并建立新连接复测。`tailscale ping` 成功不能代替 HTTPS 端口授权验证。

授权设备可开机自动连接 Tailscale，并收藏实际 HTTPS 地址。Tailscale 账号启用 MFA 或 Passkey。设备丢失时，在控制台撤销该设备并检查应用会话；关闭客户端连接只是暂时离线，不能代替撤权。管理网页使用 Serve；Funnel 会发布到公网，不适合本部署。

## 5. 初始化账号、模型和微信

### AstrBot Dashboard

打开脚本输出的 `:8443` 地址。默认用户名是 `astrbot`，随机初始密码在首次启动日志中：

```bash
sudo docker compose logs astrbot
```

首次登录按提示设置自己的强密码。日志包含初始密码，不要整体公开或推送到 Git。在 Dashboard 配置中启用现有 TOTP 两步验证，保存恢复码；可信设备功能可减少重复验证。首次部署开启 `dashboard.trust_proxy_headers`，让服务识别 Serve 转发的 HTTPS 请求；只有服务器回环地址和 Compose 网络能连接这个后端。

添加模型提供商，填入自己的 API 地址和密钥。为微信会话启用支持 Tool Calling 的模型，Vikunja 自然语言工具才会写入任务。

### Vikunja

网页注册默认关闭。在仓库根目录创建个人账号，替换示例邮箱；密码在交互提示中输入，不写在命令参数或历史中：

```bash
sudo docker compose exec vikunja /app/vikunja/vikunja user create --username dingyj --email you@example.com
```

登录脚本输出的 Vikunja HTTPS 地址。在设置里启用 TOTP并保存恢复信息，然后创建 API Token。授权插件需要的项目、任务、标签、过滤器以及相关视图、评论、关系等读写权限，按当前网页列出的权限选择，不授予账号管理等无关权限。若 `/todo setup` 报权限不足，检查失败接口对应的 Token 权限，再重新生成 Token。

在 AstrBot 插件配置页填写：

| 配置 | 值 |
| --- | --- |
| `vikunja_url` | `http://vikunja:3456`，部署已填写 |
| `vikunja_web_url` | 脚本输出的 Vikunja HTTPS 根地址，部署已填写 |
| `api_token` | 网页刚生成的 API Token |
| `timezone` | `Asia/Shanghai` |

保存并重载插件。Token 留在服务器插件配置中，不能通过微信发送或进入 Git。内部 API 地址保证微信提醒不依赖 Tailscale Serve；网页链接则使用客户端能打开的 HTTPS 地址。镜像固定 `vikunja/vikunja:2.7.0`，更新需明确修改版本并验证插件。

### 微信和个人权限

在 Dashboard 的机器人页面添加 `个人微信`，扫码、确认登录、保存。现有课表绑定使用机器人实例 ID **`weixin_test`**，保持该 ID 和原微信账号可复用绑定；换账号或 ID 时，重新使用 `/绑定课表` 上传仓库里的 `.ics`。同一个微信账号只运行一个接收消息的 AstrBot 实例。

从自己的微信私聊发送 `/sid`，记录返回的 UID 和 UMO：

- 在机器人使用的配置文件中，将 **UID** 添加到“访问控制 → 管理员 → 管理员 ID”。
- 将 **UMO** 添加到“访问控制 → 白名单 → 白名单 ID 列表”，限制这个私人助手的使用入口。
- 记忆面板认可核心管理员；无需额外填 `memory_admin_qq_ids`。该可选列表填写 UID，不能填 UMO。

Vikunja 插件的微信入口本身不检查 QQ 白名单，因此需配置核心会话白名单来限制个人使用。修改配置后按页面提示重载或重启。

## 6. 首次验收

从自己的微信依次发送：

```text
/今日课表
/本周课表
/todo diag
/todo setup
/todo board
/todo add 部署验收任务
/记忆面板
```

`/todo setup` 创建现有插件的标签和看板，可重复运行；需要预设项目骨架时使用插件已有的 `/todo setup full`。检查新增任务在 Vikunja 网页出现，网页修改后微信查询能看到；用一条近期任务验证提醒实际到达。记忆面板必须使用微信返回的带 Token 链接，浏览器打开前先连接 Tailscale。重启 AstrBot 后 Token 会变化，重新执行 `/记忆面板` 获取链接。

完成以下实机检查：

- 授权设备可登录三个网页；未加入 tailnet 的设备不能访问。
- 已加入但未被 grant 授权的设备不能访问三个 HTTPS 端口；移除授权后再次验证。
- 记忆面板缺少 Token 或使用错误 Token 时拒绝访问，有效 Token 可查看和编辑。
- 从公网尝试服务器的应用端口，连接失败。
- 重启容器后课表、任务、配置和记忆保留，微信登录态有效时无需重新扫码。
- 重启服务器后 Docker 和 Tailscale 启动，后台 Serve 路由恢复；记忆面板按需再次启动。

服务器检查命令：

```bash
sudo docker compose ps
curl -fsS http://127.0.0.1:3456/api/v1/info
curl -fsS http://127.0.0.1:6185/api/auth/setup-status
sudo tailscale serve status
```

## 7. 日常维护、备份与恢复

所有 Compose 命令在仓库根目录执行；root 所有的 `.env` 需要 sudo 读取。

```bash
sudo docker compose logs --tail=100 astrbot
sudo docker compose logs --tail=100 vikunja
sudo docker compose restart astrbot
sudo docker compose stop
sudo docker compose up -d --wait
```

更新前先备份。下面停止两个应用，打包 `.env` 与完整运行目录；即使备份失败，退出时也尝试重新启动服务：

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

另行记录源码提交号。备份含密码散列、Token、微信登录态和私人记忆，权限应保持受限，通过私人渠道保存异机副本，不能发到公开网盘或提交 Git；需要云存储时先用你已有的加密工具加密。

更新源码并重新部署：

```bash
git status --short
git pull --ff-only
sudo bash scripts/deploy-server.sh --cn
```

本地修改源码时先保存处理，不能直接清除。三个自维护插件以仓库为准，在服务器运行目录改源码或通过 WebUI 替换源码，会在下一次部署被同步覆盖；配置、记忆、课表和其他运行数据保留。

恢复时先使用备份对应的源码版本，将原运行目录移入被 Git 忽略的备份目录，替换实际备份文件名：

```bash
sudo docker compose stop
sudo mkdir -p backups
sudo mv runtime "backups/runtime-before-restore-$(date +%Y%m%d-%H%M%S)"
sudo cp .env .env.before-restore
sudo tar -xzf backups/astrbot-YYYYMMDD-HHMMSS.tar.gz
sudo bash scripts/deploy-server.sh --cn
```

恢复到另一台服务器时先让新服务器加入 Tailscale、批准并更新设备策略，再将 `.env` 的 `VIKUNJA_PUBLIC_URL`、Vikunja 插件的 `vikunja_web_url`、char 插件的 `memory.memory_panel_public_base_url` 改为新域名。完整备份恢复保留签名密钥；已有数据库而密钥缺失时，脚本会拒绝生成新密钥，要求恢复原文件。

停止网页远程入口而保留其他 Serve 配置：

```bash
sudo tailscale serve --https=443 off
sudo tailscale serve --https=8443 off
sudo tailscale serve --https=8444 off
```

再次运行部署脚本即可恢复本项目入口。避免 `tailscale serve reset`，它会清除该节点的其他路由。

## 8. 常见问题与验证范围

| 现象 | 处理 |
| --- | --- |
| SSH 断线，部署是否还在运行不明 | 重新登录后先 `tmux attach -t astrbot-deploy`，检查原进程；不要同时执行第二份部署 |
| SSH 无响应或系统卡死 | 从云控制台观察实例状态；恢复后看 `free -h`、`df -h /`、`sudo journalctl -k -b --no-pager \| tail -n 80`，若发生重启再查看 `-b -1`。有 OOM 日志才据此判断内存不足，不直接归因 VS Code 或下载源 |
| 安装或构建失败 | 保留终端中具体下载地址与失败阶段；APT 锁等待需先检查正在运行的系统更新，不能直接删除锁文件。镜像不可达时修复网络/换回官方来源后重跑，不会重置已有数据 |
| Docker CE 已装好但容器镜像下载超时 | 安装包镜像与 Docker Hub 是两件事；检查 `docker info` 与三次实际 pull。腾讯内网镜像只在适用网络使用，不能保证所有标签可用 |
| `--install-only` 结束但没有网页 | 这是预期的安装阶段；完成 Docker Hub 测试、Tailscale 登录/审批和策略后，再运行 `sudo bash scripts/deploy-server.sh --cn` |
| 重装后旧 HTTPS 地址或 grant 失效 | 新节点的 Tailscale IP、域名可能变化；更新 hosts/策略和收藏地址，首次完整部署前确定机器名 |
| 脚本报告端口或 Serve 冲突 | 查看占用服务和 `sudo tailscale serve status`，确认用途后处理冲突；脚本不会替换其他服务 |
| 同名 Compose 项目来自另一目录 | 从原目录维护，或先安排迁移；固定项目名避免第二份 clone 接管已有实例 |
| Docker 已存在但版本低于 28 | 先按 Docker 官方步骤升级，脚本不会自动迁移旧引擎 |
| HTTPS 未启用或需要确认 | 在 DNS 控制台启用 HTTPS Certificates，完成 CLI 提示的确认链接后重跑 |
| `http://100.x.x.x:6185` 打不开 | 应用只绑定宿主机回环地址；使用 Serve 输出的完整 HTTPS URL |
| 客户端 `serve status` 没有配置 | Serve 配置在服务器上；在 Ubuntu 上执行状态命令，从客户端访问其输出地址 |
| 设备能 ping 但打不开网页 | 核对服务器 Serve 路由、设备审批、grants 和客户端代理规则 |
| 开启 Clash/Mihomo TUN 后失效 | 先关闭 TUN 验证；确认后为 `100.64.0.0/10` 配置 DIRECT，并让 Tailscale DNS 正常解析 |
| 记忆网页返回 502 | 默认按需启动，先从管理员微信发送 `/记忆面板` |
| 记忆网页 API 返回 403 | 使用当前带 Token 链接，重启后获取新链接；确认发送者 UID 是管理员。静态页面能打开不代表获得数据访问权限 |
| Vikunja 启动权限错误 | 检查数据库、附件目录与密钥文件的 UID 1000 和权限；重跑脚本修正顶层目录与密钥归属 |
| `/todo` 报权限不足 | 在 Vikunja Token 页面补齐对应资源操作权限，更新插件配置并重载 |
| Vikunja 日志出现 `database is locked` | SQLite 在极快连续写入与后台订阅写入重叠时可能锁冲突；先检查任务实际状态，再重试未完成操作。本方案面向个人使用，验收包含正常交互节奏下的读写，不承诺密集并发写入 |
| 微信登录过期 | 在 Dashboard 重新扫码并保存，网络访问策略与微信登录态是两件事 |
| 提醒没有到达 | 先让微信产生会话，检查插件提醒开关、时间与日志；微信接口的实际投递仍需实机确认 |
| 课表能查但推送失败 | 核对微信账号、实例 ID `weixin_test` 和绑定 UMO，必要时重新绑定并设置推送 |

本部署不依赖 Windows/macOS 客户端执行服务器命令。镜像支持 amd64/arm64，不强制 x86 模拟。2026-10-14 已在本地 amd64 完成新版国内镜像参数的完整 Docker 构建与两个容器启动：npmmirror 安装 692 个冻结锁 Node 包，清华 PyPI 安装 159 个运行依赖并校验原锁文件散列，清华 Debian 镜像提供系统包，Playwright 官方源提供 Chromium。Dashboard 构建限制 Node 堆为 2048 MB，实测进程峰值约 2.32 GiB；服务器实际占用可能不同。

验收包含三个插件加载、Dashboard 与核心版本一致、首次改密、内部 Vikunja API 和私有 HTTPS 链接、Token 权限、看板初始化、任务与提醒字段读写、Chromium 渲染，以及容器重启后的账号、任务和签名密钥持久化。记忆面板无效 Token 的读写返回 403，有效 Token 可读写真实 SQLite 存储。重复初始化保留运行数据，包括 AstrBot 保存的带 BOM 配置；镜像不含开发运行数据、登录态或 `.env`。相关 pytest 62 项、Vikunja 插件 unittest 134 项通过，Ruff 检查通过。腾讯内网 Docker Hub 加速的可用性仍须在目标服务器实际 pull 验证。

全新 Ubuntu 的软件包安装步骤、ARM64 实际运行、Tailscale 指定设备连接与撤权、公网不可达，以及微信扫码、聊天和提醒实际投递，仍需在目标服务器和你的设备上按第 6 节验收；本地容器测试不能替代这些实机结果。
