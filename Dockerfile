FROM node:22-bookworm-slim AS dashboard-build
ARG NPM_REGISTRY=https://registry.npmjs.org
ENV NODE_OPTIONS=--max-old-space-size=2048
WORKDIR /build/dashboard
RUN npm install --global pnpm@10.20.0 --registry="$NPM_REGISTRY" \
    --fetch-retries=3 --fetch-timeout=60000
COPY dashboard/package.json dashboard/pnpm-lock.yaml dashboard/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile --registry="$NPM_REGISTRY" \
    --network-concurrency=1 --child-concurrency=1 \
    --fetch-retries=3 --fetch-timeout=60000
COPY dashboard/ ./
COPY astrbot/core/utils/t2i/template/shiki_runtime.iife.js /build/astrbot/core/utils/t2i/template/shiki_runtime.iife.js
RUN pnpm build

FROM python:3.12-slim-bookworm
ARG PYPI_INDEX_URL=https://pypi.org/simple
ARG DEBIAN_MIRROR=https://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=https://deb.debian.org/debian-security
ARG PLAYWRIGHT_DOWNLOAD_HOST
ARG PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000
WORKDIR /AstrBot
ENV PATH="/AstrBot/.venv/bin:$PATH" \
    UV_LINK_MODE=copy \
    UV_CONCURRENT_DOWNLOADS=2 \
    UV_CONCURRENT_INSTALLS=1 \
    UV_CONCURRENT_BUILDS=1 \
    UV_HTTP_TIMEOUT=120 \
    UV_HTTP_RETRIES=3 \
    UV_PYTHON_DOWNLOADS=never \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PYTHONUNBUFFERED=1

# This dependency keeps the two memory-intensive build stages sequential.
COPY --from=dashboard-build /usr/local/bin/node /usr/local/bin/node
COPY --from=dashboard-build /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN sed -i \
    -e "s|https\?://deb.debian.org/debian$|$DEBIAN_MIRROR|" \
    -e "s|https\?://deb.debian.org/debian-security$|$DEBIAN_SECURITY_MIRROR|" \
    /etc/apt/sources.list.d/debian.sources \
    && ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=60 update \
    && apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=60 install -y --no-install-recommends \
    build-essential libffi-dev libssl-dev ca-certificates bash ffmpeg \
    libavcodec-extra fonts-noto-cjk curl git ripgrep \
    && /usr/local/bin/python -m pip install --no-cache-dir --index-url="$PYPI_INDEX_URL" \
    --retries=3 --timeout=120 uv==0.12.14 \
    && rm -rf /var/lib/apt/lists/*

COPY . /AstrBot/
COPY --from=dashboard-build /build/dashboard/dist /AstrBot/astrbot/dashboard/dist
RUN python -c "from pathlib import Path; from astrbot import __version__; Path('astrbot/dashboard/dist/assets/version').write_text('v' + __version__, encoding='utf-8')" \
    && uv export --frozen --no-dev --group plugins --no-emit-project > /tmp/astrbot-requirements.txt \
    && uv venv --python /usr/local/bin/python .venv \
    && uv pip sync --python .venv/bin/python --require-hashes \
    --index-url="$PYPI_INDEX_URL" /tmp/astrbot-requirements.txt \
    && uv pip install --python .venv/bin/python --no-deps --index-url="$PYPI_INDEX_URL" . \
    && PLAYWRIGHT_DOWNLOAD_HOST="$PLAYWRIGHT_DOWNLOAD_HOST" \
    PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT="$PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT" \
    .venv/bin/python -m playwright install --with-deps chromium \
    && touch /AstrBot/.astrbot \
    && rm -rf /root/.cache/uv /var/lib/apt/lists/* /tmp/astrbot-requirements.txt

EXPOSE 6185
CMD ["python", "main.py"]
