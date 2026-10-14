FROM node:22-bookworm-slim AS dashboard-build
WORKDIR /build/dashboard
RUN npm install --global pnpm@10.20.0
COPY dashboard/package.json dashboard/pnpm-lock.yaml dashboard/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY dashboard/ ./
COPY astrbot/core/utils/t2i/template/shiki_runtime.iife.js /build/astrbot/core/utils/t2i/template/shiki_runtime.iife.js
RUN pnpm build

FROM python:3.12-slim-bookworm
WORKDIR /AstrBot
ENV PATH="/AstrBot/.venv/bin:$PATH" \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:0.12.14 /uv /uvx /usr/local/bin/
COPY --from=dashboard-build /usr/local/bin/node /usr/local/bin/node
COPY --from=dashboard-build /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev libssl-dev ca-certificates bash ffmpeg \
    libavcodec-extra fonts-noto-cjk curl git ripgrep \
    && rm -rf /var/lib/apt/lists/*

COPY . /AstrBot/
COPY --from=dashboard-build /build/dashboard/dist /AstrBot/astrbot/dashboard/dist
RUN python -c "from pathlib import Path; from astrbot import __version__; Path('astrbot/dashboard/dist/assets/version').write_text('v' + __version__, encoding='utf-8')" \
    && uv sync --frozen --no-dev --group plugins \
    && .venv/bin/python -m playwright install --with-deps chromium \
    && touch /AstrBot/.astrbot \
    && rm -rf /root/.cache/uv /var/lib/apt/lists/*

EXPOSE 6185
CMD ["python", "main.py"]
