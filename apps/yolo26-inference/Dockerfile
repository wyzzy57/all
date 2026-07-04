FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VISIOX_INFERENCE_HOST=0.0.0.0 \
    VISIOX_INFERENCE_PORT=8080

WORKDIR /app

COPY pyproject.toml ./
COPY apps ./apps
COPY packages ./packages
COPY workers ./workers

RUN pip install --no-cache-dir .

EXPOSE 8080

CMD ["sh", "-c", "uvicorn visiox_yolo26_inference.main:app --host ${VISIOX_INFERENCE_HOST} --port ${VISIOX_INFERENCE_PORT}"]
