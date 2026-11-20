# Stage 1: build the on-site UI (Egress Proof, Raw viewer, Buffer) from the shared dashboard code
FROM node:24-alpine AS localui
WORKDIR /ui
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build:local

# Stage 2: the collector
FROM python:3.12-slim
ARG WITH_NER=0
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WITH_NER=${WITH_NER} CONFIG_DIR=/config DATA_DIR=/data
WORKDIR /app
COPY schema /src/schema
RUN pip install /src/schema
COPY collector/requirements.txt .
RUN pip install -r requirements.txt && \
    if [ "$WITH_NER" = "1" ]; then pip install presidio-analyzer==2.2.366 spacy==3.8.18 \
      https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl; fi
COPY collector/collector ./collector
COPY --from=localui /ui/dist-local ./static
EXPOSE 8081 5514/udp 9090
CMD ["python", "-m", "collector.main"]
