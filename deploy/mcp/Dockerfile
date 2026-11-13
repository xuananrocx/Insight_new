# Build on a disposable builder, never on the deployment server.
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
ARG REVISION
ARG APP_VERSION
WORKDIR /app
COPY deploy/mcp/requirements.lock.txt /tmp/mcp-requirements.lock
RUN python -m pip install --no-cache-dir -r /tmp/mcp-requirements.lock && python -m pip check
# Deliberately exclude application storage, configuration and model modules.
COPY src/__init__.py /app/src/__init__.py
COPY src/mcp/__init__.py src/mcp/gateway.py src/mcp/contracts.py /app/src/mcp/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 INSIGHT_MCP_BIND=0.0.0.0 INSIGHT_MCP_PORT=8001
LABEL org.opencontainers.image.revision=${REVISION} org.opencontainers.image.version=${APP_VERSION} io.insight.component=mcp
USER 1002:1002
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=6s --start-period=20s --retries=12 CMD ["python", "-c", "import json,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=5)); assert r['status']=='ok' and r['component']=='insight-mcp'"]
CMD ["python", "-m", "src.mcp.gateway"]
