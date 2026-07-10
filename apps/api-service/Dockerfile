FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgl1 \
        libxcb1 \
        libx11-6 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml ./
COPY alembic.ini ./
COPY apps/api-service ./apps/api-service
COPY apps/yolo26-inference ./apps/yolo26-inference
COPY infra/seed ./infra/seed
COPY infra/migrations ./infra/migrations
COPY packages ./packages
COPY workers ./workers

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "visiox_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
