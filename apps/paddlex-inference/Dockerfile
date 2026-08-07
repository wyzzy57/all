ARG CUDA_BASE_IMAGE=nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04@sha256:f6913f3c02f297877f6859d12ff330043c0be668fdad86868c29a239a5a82151
FROM ${CUDA_BASE_IMAGE}

ARG PADDLE_WHEEL_URL=https://paddle-whl.bj.bcebos.com/stable/cu118/paddlepaddle-gpu/paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl
ARG PADDLE_WHEEL_SHA256=6e262ce3a18220a4e066e40656777753fd6dcdd8637c9f397dd449079d12ce9c
ARG UBUNTU_MIRROR=https://mirrors.aliyun.com/ubuntu
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN sed -i \
        -e "s|http://archive.ubuntu.com/ubuntu|${UBUNTU_MIRROR}|g" \
        -e "s|http://security.ubuntu.com/ubuntu|${UBUNTU_MIRROR}|g" \
        /etc/apt/sources.list \
    && apt-get -o Acquire::Retries=3 update \
    && DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
        ca-certificates \
        curl \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libsm6 \
        libxext6 \
        libxrender1 \
        python3-pip \
        python3.10 \
        python3.10-venv \
    && rm -rf /var/lib/apt/lists/*
RUN python3.10 -m venv "$VIRTUAL_ENV" \
    && curl --fail --location --retry 8 --retry-all-errors \
        --connect-timeout 30 --output /tmp/paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl "$PADDLE_WHEEL_URL" \
    && echo "$PADDLE_WHEEL_SHA256  /tmp/paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl" | sha256sum -c - \
    && python -m pip install --no-cache-dir /tmp/paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl \
    && python -c "from importlib.metadata import version; assert version('paddlepaddle-gpu') == '3.0.0'" \
    && rm -f /tmp/paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl

ENV VISIOX_INFERENCE_HOST=0.0.0.0 \
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
