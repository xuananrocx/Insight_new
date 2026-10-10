# Dependency runtime is prepared separately and must be pinned by digest.
# It must supply Python 3.11, CPU torch, libmagic, libgomp and locked dependencies.
ARG RUNTIME_IMAGE
FROM ${RUNTIME_IMAGE}
ARG REVISION
ARG APP_VERSION=0.1.1
USER root
WORKDIR /app
RUN rm -rf /app && mkdir /app
COPY src /app/src
COPY config.yaml /app/config.yaml
COPY frontend/dist /app/frontend/dist
COPY requirements.txt /app/requirements.txt
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AMD_DATA_DIR=/data HF_HOME=/models/huggingface HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TIKTOKEN_CACHE_DIR=/models/tiktoken ANONYMIZED_TELEMETRY=False
LABEL org.opencontainers.image.revision=${REVISION} org.opencontainers.image.version=${APP_VERSION}
USER 1002:1002
EXPOSE 8888
CMD ["python", "-m", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8888", "--workers", "1"]
