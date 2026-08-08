ARG CUDA_BASE_IMAGE=nvidia/cuda:11.8.0-base-ubuntu22.04@sha256:79e5b2cf878ee9006f5b3738caeea34fdc7708a32db53fe3e80db0b48bd286a0
FROM ${CUDA_BASE_IMAGE}

ARG PADDLE_WHEEL_URL=https://paddle-whl.bj.bcebos.com/stable/cu118/paddlepaddle-gpu/paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl
ARG PADDLE_WHEEL_SHA256=6e262ce3a18220a4e066e40656777753fd6dcdd8637c9f397dd449079d12ce9c
ARG PADDLEX_FONT_URL=https://paddle-model-ecology.bj.bcebos.com/paddlex/PaddleX3.0/fonts/PingFang-SC-Regular.ttf
ARG PADDLEX_FONT_SHA256=7c31780a74b296162818aa5891c395d6ecdfcf1e71d9e1e4c3326869ad901ab6
ARG UBUNTU_MIRROR=https://mirrors.aliyun.com/ubuntu
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PADDLE_PDX_LOCAL_FONT_FILE_PATH=/opt/paddlex/fonts/PingFang-SC-Regular.ttf \
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
RUN install -d -m 0755 /opt/paddlex/fonts \
    && curl --fail --location --retry 8 --retry-all-errors \
        --connect-timeout 30 --output /opt/paddlex/fonts/PingFang-SC-Regular.ttf "$PADDLEX_FONT_URL" \
    && echo "$PADDLEX_FONT_SHA256  /opt/paddlex/fonts/PingFang-SC-Regular.ttf" | sha256sum -c -
RUN python3.10 -m venv "$VIRTUAL_ENV" \
    && curl --fail --location --retry 8 --retry-all-errors \
        --connect-timeout 30 --output /tmp/paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl "$PADDLE_WHEEL_URL" \
    && echo "$PADDLE_WHEEL_SHA256  /tmp/paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl" | sha256sum -c - \
    && python -m pip install --no-cache-dir /tmp/paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl \
    && python -c "from importlib.metadata import version; assert version('paddlepaddle-gpu') == '3.0.0'" \
    && rm -f /tmp/paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl
COPY packages/visiox-paddlex/requirements.runtime.lock /tmp/paddlex-runtime-requirements.lock
RUN pip install --no-cache-dir -r /tmp/paddlex-runtime-requirements.lock \
    && rm -f /tmp/paddlex-runtime-requirements.lock

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
