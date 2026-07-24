FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=600 \
    PIP_RETRIES=10 \
    YOLO_CONFIG_DIR=/tmp/ultralytics \
    VISIOX_INFERENCE_HOST=0.0.0.0 \
    VISIOX_INFERENCE_PORT=8080

WORKDIR /app

ARG TORCH_VERSION=2.12.1
ARG TORCHVISION_VERSION=0.27.1
ARG TENSORRT_VERSION=10.13.3.9.post1
ARG ULTRALYTICS_VERSION=8.4.103
ARG ONNXRUNTIME_GPU_VERSION=1.27.0

RUN groupadd --gid 10001 visiox \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin visiox

COPY apps/yolo26-inference/src/visiox_yolo26_inference ./visiox_yolo26_inference

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
        --index-url https://download.pytorch.org/whl/cu126 \
        "torch==${TORCH_VERSION}" \
        "torchvision==${TORCHVISION_VERSION}" \
    && pip install \
        "fastapi>=0.115,<1.0" \
        "onnx>=1.16,<2.0" \
        "onnxruntime-gpu==${ONNXRUNTIME_GPU_VERSION}" \
        "onnxslim>=0.1,<1.0" \
        "pillow>=10.0,<12.0" \
        "python-multipart>=0.0.9,<1.0" \
        "ultralytics==${ULTRALYTICS_VERSION}" \
        "uvicorn[standard]>=0.30,<1.0" \
    && pip install --extra-index-url https://pypi.nvidia.com "tensorrt-cu12==${TENSORRT_VERSION}" \
    && mkdir -p /tmp/ultralytics \
    && chown -R visiox:visiox /tmp/ultralytics

RUN --mount=type=cache,target=/root/.cache/pip \
    pip uninstall --yes opencv-python \
    && pip install "opencv-python-headless==5.0.0.93"

USER visiox

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read()"]

CMD ["uvicorn", "visiox_yolo26_inference.main:app", "--host", "0.0.0.0", "--port", "8080"]
