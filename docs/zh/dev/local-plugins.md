# 本地插件开发与迁移

这个分支将两个插件及现有课表纳入 Git，支持在 Ubuntu 本地开发，再将同一份代码和所需数据部署到服务器。

## 仓库包含的内容

| 路径 | 用途 |
| --- | --- |
| `data/plugins/astrbot_plugin_course/` | 课程插件，包括本地附件导入、微信文字课表和消息处理修复 |
| `data/plugins/astrbot_plugin_char/` | 综合助手插件的源码及模板、字体等静态资源 |
| `data/plugin_data/astrbot_plugin_course/bindings.json` | 当前微信账号的课表绑定和提醒设置 |
| `data/plugin_data/astrbot_plugin_course/ics/*.ics` | 已导入的完整课表 |
| `pyproject.toml`、`uv.lock` | 核心及 `plugins` 依赖组的版本声明和锁定结果 |

`bindings.json` 使用相对课表路径；本地与服务器都从仓库根目录启动即可读取。现有课表包含个人课程、教师、教室及微信绑定标识，会随本分支的 Git 提交一起迁移。

运行配置、模型 API Key、微信登录信息、Cookie、数据库、聊天记忆、日志、缓存和虚拟环境继续忽略。课程数据之外的运行状态需要单独迁移。

## Ubuntu 本地开发

安装 Git 和 uv。仓库的 `.python-version` 指定 Python 3.12，uv 会创建项目虚拟环境。

在当前工作目录启动：

```bash
cd /home/dingyj/Astrbot_dev
uv sync --locked --group plugins
uv run --locked --group plugins main.py
```

首次启动会准备 WebUI 文件，需要网络连接。访问 `http://localhost:6185`，用启动日志中的随机初始密码登录。关闭服务时，在启动终端按 `Ctrl+C`。

## 随仓库迁移运行数据

课表和插件源码已经随 Git 保存。如果服务器还要继续使用当前微信账号、模型配置和聊天记忆，请先停止本地和服务器上的 AstrBot，再通过私人文件传输把以下内容复制到服务器的同名位置：

- `data/cmd_config.json`：管理账号、模型和微信连接配置。
- `data/config/`：综合插件等插件的配置。
- `data/data_v4.db`：会话、定时任务等核心状态；存在 `-wal`、`-shm` 时一并复制。
- `data/plugin_data/astrbot_all_char/`：综合插件的记忆、提醒、账单等状态。
- 如使用过股票/基金或运势功能，也迁移 `data/plugin_data/astrbot_stock/`、`data/plugin_data/astrbot_all_char_jrys/` 等对应状态目录（存在时）。

这些文件包含凭据或私人状态，不要使用 `git add -f` 纳入 Git。在服务器重新创建 `.venv/`；虚拟环境包含本地路径，不随项目目录复制。

也可以直接通过 SSH 将当前项目目录和运行数据一起复制到服务器。停止服务后，在仓库根目录执行下列模板，将 `user@server` 和目标目录替换为实际值：

```bash
rsync -a --exclude='.git/' --exclude='.venv*/' --exclude='node_modules/' \
  --exclude='__pycache__/' --exclude='data/logs/' --exclude='data/temp/' \
  ./ user@server:/opt/astrbot/
```

此方式会通过 SSH 携带被 Git 忽略的配置和数据库，也会携带已有 WebUI 文件。完成复制后，在服务器的 `/opt/astrbot` 中安装依赖并启动即可。

原课表绑定的平台实例 ID 是 `weixin_test`。恢复配置或重新添加微信机器人时，保持此 ID 和微信账号一致；重新连接后可用 `/今日课表` 验证。改变账号或实例 ID 后，需要重新绑定课表。使用同一微信账号时，只运行一个接收消息的 AstrBot 实例。

直接编辑 `data/plugins/` 中对应插件文件。在 WebUI 的插件页面重载所修改的插件，或按 `Ctrl+C` 后重新执行启动命令。微信的今日、明日、本周、下周查询返回文字，避免课表图片依赖微信附件 CDN。

课程插件的依赖已加入 `plugins` 组，后续继续使用带 `--group plugins` 的命令。综合插件部分可选能力需要额外依赖，例如股票/基金使用的 `akshare`；当前基线没有安装这些可选依赖，开启对应功能时再安装并在 `plugins` 组中记录。

第三方插件保留原有代码格式，核心的 Ruff 和 pre-commit 批量检查排除这两个目录。修改插件后，可对修改的文件显式运行检查，例如：

```bash
uv run --locked --group plugins ruff check data/plugins/astrbot_plugin_course/main.py
uv run --locked --group plugins ruff format data/plugins/astrbot_plugin_course/main.py
```

## 提交代码并部署到 Ubuntu

本地修改完成并验证后提交、推送：

```bash
git add data/plugins/astrbot_plugin_course data/plugins/astrbot_plugin_char
git add data/plugin_data/astrbot_plugin_course
git commit -m "feat: improve local plugins"
git push origin master
```

`bindings.json` 中的提醒设置也是版本化数据；修改提醒设置或重新导入课表后，先查看 `git diff`，再决定是否随代码提交。新安装的其他插件默认继续忽略，需要明确修改 `.gitignore` 才会纳入版本管理。不要在插件市场更新这两个本地维护的插件，以免覆盖代码修改。

通过 Git 部署时，服务器首次克隆同一个仓库，并按上面的私人迁移步骤恢复配置与状态：

```bash
git clone https://github.com/Ding141/Astrbot_dev.git
cd Astrbot_dev
uv sync --locked --no-dev --group plugins
uv run --locked --no-dev --group plugins main.py
```

如果已使用 `rsync` 复制整个项目目录，进入目标目录后只需执行上面的两条 `uv` 命令，无需再克隆仓库。

后续更新时，先停止服务并备份运行数据，再执行：

```bash
git pull --ff-only origin master
uv sync --locked --no-dev --group plugins
uv run --locked --no-dev --group plugins main.py
```

如果服务器上修改了已跟踪的课表或绑定，先保存这些更改，再拉取代码。不要用 `git reset --hard` 清掉运行中的课表设置。长期运行时，可将最后一条启动命令交给现有服务管理器，并将工作目录设置为仓库根目录；停止信号使用 `SIGINT`，让 AstrBot 完成收尾。

本流程部署的是当前仓库源码。仓库自带的 `compose.yml` 使用官方预构建镜像；直接运行它不会包含本分支对核心源码的修改。
