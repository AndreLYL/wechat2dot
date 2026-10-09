FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy WECHAT2DOT_CONFIG=/data/bridge.json
WORKDIR /app
RUN python -m pip install --no-cache-dir uv==0.12.24
COPY pyproject.toml uv.lock README.md LICENSE NOTICE.txt MANIFEST.in ./
COPY licenses/ licenses/
COPY wechat2dot/ wechat2dot/
COPY wechat_dot_bridge/ wechat_dot_bridge/
RUN uv sync --frozen --no-dev --extra setup \
    && useradd --uid 10001 --create-home bridge \
    && mkdir -m 700 /data \
    && chown bridge:bridge /data
USER 10001:10001
ENTRYPOINT ["/app/.venv/bin/wechat2dot"]
CMD ["doctor"]
