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

目标系统是 Ubuntu 22.04、64 位 amd64 或 arm64。建议至少 2 核、4 GB 内存及 15 GB 可用磁盘用于首次源码构建；本地执行验证的架构见本文末尾。服务器安装 Git，使用具有 sudo 权限的普通账号。脚本安装缺少的 Docker、Compose、Buildx、Tailscale、Python 3 和 rsync，启用 Docker 与 Tailscale 开机启动。使用 Docker Engine 28+，因为旧版本的 localhost 端口发布存在同网段访问问题，详见 [Docker 端口发布说明](https://docs.docker.com/engine/network/port-publishing/)。

先通过云控制台建立服务器，再从自己的电脑登录，替换账号与 IP：

```bash
ssh ubuntu@YOUR_SERVER_IP
sudo apt-get update
sudo apt-get install -y git
```

云安全组保留 SSH 登录所需规则，不开放 TCP 3456、6185、7835、443、8443、8444。Tailscale 使用自己的网络入口，Serve 的 HTTPS 端口只对 tailnet 提供服务。脚本不会调整安全组、防火墙或销毁已有服务器。重建前如需旧服务数据，请先完成备份。

### 沿用已有服务器

已有 Ubuntu 22.04、Docker Engine 28+、Compose 和正常登录的 Tailscale 时，直接复用环境。无需为了本项目重新购买或重装服务器。约 4 GB 内存且没有 swap 时，首次构建前先停止旧业务服务；构建内存仍不足时可增加 swap。

如果此前运行的是 `personal-assistant-astrbot` 旧方案，并且已决定废弃旧服务，在**服务器上**执行以下命令。先核对容器名与路由用途；这会停止并删除两个旧业务容器，旧网页与机器人随即停止服务：

```bash
sudo docker rm -f \
  personal-assistant-astrbot-astrbot-1 \
  personal-assistant-astrbot-assistant-api-1
sudo tailscale serve --https=443 off
sudo docker ps
sudo tailscale serve status
```

旧 `443 → 127.0.0.1:8080` 路由需要释放，新部署会设置为 Vikunja 的 `443 → 127.0.0.1:3456`。已有的 `8443 → 127.0.0.1:6185` 可以保留复用。按端口关闭旧入口即可，避免 `tailscale serve reset` 清除其他路由。[Serve 命令说明](https://tailscale.com/docs/reference/tailscale-cli/serve)

这些命令保留系统、SSH、Docker、Tailscale 登录与设备信息，也保留旧容器挂载在宿主机的目录；不清理其他容器或 Docker 数据卷。旧目录里的模型密钥、微信登录态和数据库不会自动导入新的 `runtime/`。后续从本仓库部署并重新配置账号，不要再次启动旧 Compose 项目。Tailscale 设备审批与访问策略仍需按第 4 节核对。

## 2. clone 私有仓库

服务器已有能访问该仓库的 SSH 密钥时，直接使用。否则创建只用于读取仓库的密钥：

```bash
ssh-keygen -t ed25519 -f ~/.ssh/astrbot_repo -C astrbot-server
cat ~/.ssh/astrbot_repo.pub
```

在 GitHub 仓库的 Settings → Deploy keys 中添加公钥，保持只读。私钥只留在服务器，不能复制到仓库或聊天中。编辑 `~/.ssh/config` 添加以下 Host；已有配置保留：

```sshconfig
Host github-astrbot
    HostName github.com
    User git
    IdentityFile ~/.ssh/astrbot_repo
    IdentitiesOnly yes
```

```bash
chmod 600 ~/.ssh/config
git clone git@github-astrbot:YOUR_ACCOUNT/YOUR_REPO.git ~/Astrbot_dev
cd ~/Astrbot_dev
```

替换 `YOUR_ACCOUNT/YOUR_REPO`。首次 SSH 连接按 [GitHub 官方指纹](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)核对服务器指纹。

## 3. 一个命令部署

在仓库根目录执行：

```bash
sudo bash scripts/deploy-server.sh
```

首次安装可能需要较长时间下载依赖、Chromium 和构建 Dashboard。脚本使用官方软件源，复用已有可用的 Docker/Tailscale；不会自动卸载冲突的 Docker 软件包。若现有 Docker 低于 28，先按[官方 Ubuntu 安装/升级步骤](https://docs.docker.com/engine/install/ubuntu/)升级。

首次 Tailscale 登录时，在浏览器打开终端给出的登录链接。使用自己的 Tailscale 账号，并在管理控制台批准服务器。命令等待最多 60 秒；超时后完成登录、审批，再运行同一个部署命令即可。服务器显示离线或等待审批时也先在控制台处理。

在 [Tailscale DNS 控制台](https://login.tailscale.com/admin/dns)启用 MagicDNS 和 HTTPS Certificates。Serve 首次运行可能给出启用 HTTPS 的确认链接，按提示完成后重跑。HTTPS 证书域名会进入公开证书透明度日志，启用前确认服务器名与 tailnet 名称不包含敏感个人信息。[证书说明](https://tailscale.com/docs/how-to/set-up-https-certificates)

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

4. 在 Access controls 设置按设备放行的策略。下方仅用于本项目专用的新 tailnet，所有示例 IP 必须替换为实际地址；第三台设备不需要时删除对应项。

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
sudo bash scripts/deploy-server.sh
```

本地修改源码时先保存处理，不能直接清除。三个自维护插件以仓库为准，在服务器运行目录改源码或通过 WebUI 替换源码，会在下一次部署被同步覆盖；配置、记忆、课表和其他运行数据保留。

恢复时先使用备份对应的源码版本，将原运行目录移入被 Git 忽略的备份目录，替换实际备份文件名：

```bash
sudo docker compose stop
sudo mkdir -p backups
sudo mv runtime "backups/runtime-before-restore-$(date +%Y%m%d-%H%M%S)"
sudo cp .env .env.before-restore
sudo tar -xzf backups/astrbot-YYYYMMDD-HHMMSS.tar.gz
sudo bash scripts/deploy-server.sh
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
| 安装或构建失败 | 根据终端失败位置检查官方软件源、镜像仓库和网络；修复后重跑，不会重置已有数据 |
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

本部署不依赖 Windows/macOS 客户端执行服务器命令。镜像支持 amd64/arm64，不强制 x86 模拟。2026-10-14 已在本地 amd64 完成真实 Docker 镜像构建与两个容器启动，验证三个插件加载、Dashboard 资源与核心版本一致、首次改密、内部 Vikunja API 和私有 HTTPS 链接、Token 权限、看板初始化、任务与提醒字段读写、Chromium 渲染，以及容器重启后的账号、Token、任务和签名密钥持久化。记忆面板无效 Token 的读写返回 403，有效 Token 可读写真实 SQLite 存储。运行目录重复初始化保留数据，包括 AstrBot 保存的带 BOM 配置；构建镜像不含开发运行数据、登录态或 `.env`。部署与微信等相关 pytest 56 项、Vikunja 插件 unittest 134 项通过，Ruff 检查通过。

全新 Ubuntu 的软件包安装步骤、ARM64 实际运行、Tailscale 指定设备连接与撤权、公网不可达，以及微信扫码、聊天和提醒实际投递，仍需在目标服务器和你的设备上按第 6 节验收；本地容器测试不能替代这些实机结果。
