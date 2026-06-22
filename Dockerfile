FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

ARG DEBIAN_APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
ARG UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

RUN sed -i \
    -e "s|http://deb.debian.org/debian|https://${DEBIAN_APT_MIRROR}/debian|g" \
    -e "s|http://deb.debian.org/debian-security|https://${DEBIAN_APT_MIRROR}/debian-security|g" \
    /etc/apt/sources.list.d/debian.sources

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_INDEX_URL=${UV_INDEX_URL} \
    PIP_INDEX_URL=${UV_INDEX_URL}

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY start-api.sh start-worker.sh ./

RUN mkdir -p /app/storage/objects

EXPOSE 8100

CMD ["/app/start-api.sh"]
