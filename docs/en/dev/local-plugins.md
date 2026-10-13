# Local plugin development and migration

This fork tracks two plugins and the existing timetable for local Ubuntu development and deployment of the same code and selected data to a server.

## Tracked files

| Path | Purpose |
| --- | --- |
| `data/plugins/astrbot_plugin_course/` | Course plugin, including local attachment imports and WeChat text schedule replies |
| `data/plugins/astrbot_plugin_char/` | Combined assistant plugin source and static templates, fonts, and assets |
| `data/plugin_data/astrbot_plugin_course/bindings.json` | Existing WeChat timetable binding and reminder settings |
| `data/plugin_data/astrbot_plugin_course/ics/*.ics` | Complete imported timetable |
| `pyproject.toml`, `uv.lock` | Core and `plugins` dependency group declarations and locked versions |

The binding stores a relative ICS path. Always start from the repository root locally and on the server. The tracked timetable includes personal courses, teachers, rooms, and the WeChat binding identifier.

Runtime configuration, API keys, WeChat credentials, cookies, databases, chat memories, logs, caches, and virtual environments remain ignored. Transfer runtime state separately when needed.

## Local Ubuntu development

Install Git and uv. The repository selects Python 3.12 through `.python-version`; uv creates the project environment.

Start from the existing workspace:

```bash
cd /home/dingyj/Astrbot_dev
uv sync --locked --group plugins
uv run --locked --group plugins main.py
```

The first launch prepares the WebUI and needs network access. Open `http://localhost:6185` and use the random initial password in the startup log. Stop the foreground process with `Ctrl+C`.

## Transfer runtime data with the repository

Plugin source and the timetable already travel through Git. To retain the existing WeChat account, model configuration, and chat memories, stop AstrBot locally and on the server, then privately copy these files to the same relative paths on the server:

- `data/cmd_config.json`: Dashboard, model, and WeChat connection configuration.
- `data/config/`: Plugin configuration.
- `data/data_v4.db`: Core conversations and scheduled tasks; include any remaining `-wal` and `-shm` files.
- `data/plugin_data/astrbot_all_char/`: Combined plugin memories, reminders, bookkeeping, and other state.
- If used, also transfer feature-specific state directories such as `data/plugin_data/astrbot_stock/` and `data/plugin_data/astrbot_all_char_jrys/`.

These files contain credentials or private state. Keep them out of Git and do not force-add them. Recreate `.venv/` on the server because the existing environment contains local paths.

You can also transfer the current project directory and runtime data together over SSH. After stopping the service, run this template from the repository root, replacing `user@server` and the destination directory:

```bash
rsync -a --exclude='.git/' --exclude='.venv*/' --exclude='node_modules/' \
  --exclude='__pycache__/' --exclude='data/logs/' --exclude='data/temp/' \
  ./ user@server:/opt/astrbot/
```

This SSH transfer includes the ignored configuration and databases, plus existing WebUI assets. Install dependencies and start from `/opt/astrbot` on the server after the copy completes.

The saved course binding uses platform instance ID `weixin_test`. Preserve that ID and the same WeChat account when restoring or recreating the connection. Verify with `/今日课表`. Rebind the calendar if the account or instance ID changes. Run only one inbound AstrBot instance for the same WeChat account.

Edit the plugin files under `data/plugins/`. Reload the edited plugin in the WebUI, or stop and restart the process. WeChat course queries return text so they do not require the attachment CDN to send images.

Keep `--group plugins` in subsequent uv commands. Some optional combined-plugin features require additional packages such as `akshare`; add dependencies to the `plugins` group when enabling those features.

The imported plugins retain their existing style and are excluded from bulk core Ruff and pre-commit checks. Check edited plugin files explicitly, for example:

```bash
uv run --locked --group plugins ruff check data/plugins/astrbot_plugin_course/main.py
uv run --locked --group plugins ruff format data/plugins/astrbot_plugin_course/main.py
```

## Commit and deploy to Ubuntu

After testing changes locally:

```bash
git add data/plugins/astrbot_plugin_course data/plugins/astrbot_plugin_char
git add data/plugin_data/astrbot_plugin_course
git commit -m "feat: improve local plugins"
git push origin master
```

Course reminder settings are also tracked. Review the diff before committing changed bindings or a replacement timetable. Other newly installed plugins remain ignored until explicitly allowed in `.gitignore`. Marketplace updates overwrite local plugin edits, so maintain these two copies through Git.

For the first deployment through Git, clone the same repository and privately restore configuration and runtime state as described above:

```bash
git clone https://github.com/Ding141/Astrbot_dev.git
cd Astrbot_dev
uv sync --locked --no-dev --group plugins
uv run --locked --no-dev --group plugins main.py
```

If the entire project was copied with `rsync`, enter its destination directory and run only the two uv commands above; no clone is needed.

For later updates, stop the service and back up its runtime data before running:

```bash
git pull --ff-only origin master
uv sync --locked --no-dev --group plugins
uv run --locked --no-dev --group plugins main.py
```

Preserve server-side edits to tracked timetable files before pulling. Do not discard them with `git reset --hard`. A service manager should use the repository root as its working directory and send `SIGINT` for shutdown.

This workflow runs the fork's source checkout. The included `compose.yml` uses an official prebuilt image and does not include this fork's core source modifications.
