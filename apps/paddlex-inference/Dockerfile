ARG PADDLEX_BASE_IMAGE=paddlepaddle/paddle:3.0.0-gpu-cuda11.8-cudnn8.9-trt8.6@sha256:2bd8830dafd258501e7313b320fa1bcc946c70318b3081647b90bc70182e7360
FROM ${PADDLEX_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VISIOX_INFERENCE_HOST=0.0.0.0 \
    VISIOX_INFERENCE_PORT=8080

WORKDIR /app
RUN groupadd --gid 10001 visiox \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin visiox

COPY apps/paddlex-inference/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock

COPY apps/paddlex-inference/src/visiox_paddlex_inference ./visiox_paddlex_inference

USER visiox
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read()"]
CMD ["uvicorn", "visiox_paddlex_inference.main:app", "--host", "0.0.0.0", "--port", "8080"]
