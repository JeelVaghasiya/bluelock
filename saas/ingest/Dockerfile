FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY schema /src/schema
RUN pip install /src/schema
COPY saas/common /src/common
RUN pip install /src/common
COPY saas/ingest/requirements.txt .
RUN pip install -r requirements.txt
COPY saas/ingest/ingest ./ingest
CMD ["uvicorn", "ingest.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
