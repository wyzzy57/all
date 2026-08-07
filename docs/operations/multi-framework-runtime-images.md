# Multi-Framework Runtime Images

## Scope

The platform control plane, MLflow, and TensorBoard run in Compose. Framework
training and inference images do not: an authorized edge node pulls them only
when it receives a job or deployment request. Every runtime reference must use
an OCI manifest digest (`registry/namespace/image@sha256:<64-hex>`), never a
mutable tag.

| Runtime | Dockerfile | Environment variable | Edge requirement |
| --- | --- | --- | --- |
| Ultralytics training | `workers/training-worker/Dockerfile.edge` | `VISIOX_ULTRALYTICS_TRAINING_IMAGE_DIGEST` | Supported Python/CUDA stack for the selected image |
| PaddleX training | `workers/paddlex-training-worker/Dockerfile.edge` | `VISIOX_PADDLEX_TRAINING_IMAGE_DIGEST` | NVIDIA GPU, compatible driver, Docker GPU runtime, at least 8 GB VRAM |
| PaddleX inference | `apps/paddlex-inference/Dockerfile` | `VISIOX_PADDLEX_INFERENCE_IMAGE_DIGEST` | Docker GPU runtime when GPU inference is requested |
| LLaMA-Factory training | `workers/llm-training-worker/Dockerfile.edge` | `VISIOX_LLM_TRAINING_IMAGE_DIGEST` | GPU, driver, and VRAM suitable for the model and adapter |
| YOLO26 inference | `apps/yolo26-inference/Dockerfile` | `VISIOX_DEPLOYMENT_IMAGE_DIGEST` | Docker GPU runtime for TensorRT/GPU deployments |

## Build, Push, and Pin

Set the registry once, build an image with a human-readable release tag, and
then resolve its immutable manifest digest from the registry. The digest is the
only value copied into Visiox configuration.

```powershell
$registry = "registry.example/visiox"
$tag = "2026-08-07"

docker build -f workers/paddlex-training-worker/Dockerfile.edge -t "$registry/paddlex-training:$tag" .
docker push "$registry/paddlex-training:$tag"

$digest = docker buildx imagetools inspect "$registry/paddlex-training:$tag" --format '{{.Manifest.Digest}}'
if ($digest -notmatch '^sha256:[0-9a-f]{64}$') { throw "Registry did not return a manifest digest" }
$pinned = "$registry/paddlex-training@$digest"
$pinned
```

Repeat the same sequence with `apps/paddlex-inference/Dockerfile`,
`workers/training-worker/Dockerfile.edge`, `workers/llm-training-worker/Dockerfile.edge`,
and `apps/yolo26-inference/Dockerfile`. Inspect the exact reference before
release:

```powershell
docker buildx imagetools inspect $pinned
docker pull $pinned
```

### PaddleX 3.0.3 Runtime Contract

Both PaddleX images share this immutable runtime prefix:

- linux/amd64 CUDA base:
  `nvidia/cuda:11.8.0-base-ubuntu22.04@sha256:79e5b2cf878ee9006f5b3738caeea34fdc7708a32db53fe3e80db0b48bd286a0`.
  This NVIDIA base preserves the `NVIDIA_REQUIRE_CUDA`, device visibility,
  driver capability, and library-path contract used by NVIDIA Container
  Runtime without duplicating the runtime and cuDNN libraries supplied by the
  official Paddle wheel. This exact base manifest has no NVIDIA entrypoint
  script; driver injection is verified with an actual `--gpus all` runtime
  probe on the target node.
- Python 3.10 virtual environment at `/opt/venv`.
- Official PaddlePaddle GPU 3.0.0 cu118 wheel:
  `https://paddle-whl.bj.bcebos.com/stable/cu118/paddlepaddle-gpu/paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl`.
- Wheel length: 1,206,592,273 bytes; SHA-256:
  `6e262ce3a18220a4e066e40656777753fd6dcdd8637c9f397dd449079d12ce9c`.
- The wheel installs the matching CUDA 11.8 userspace packages, including
  `nvidia-cuda-runtime-cu11==11.8.89` and
  `nvidia-cudnn-cu11==8.9.6.50`; the edge NVIDIA runtime supplies the host
  driver and `libcuda.so.1`.
- PaddleX `3.0.3`, installed only after the wheel checksum and installed
  `paddlepaddle-gpu` distribution version are verified. Import Paddle only in
  a GPU-enabled runtime because `libcuda.so.1` is injected by the NVIDIA
  container runtime and is intentionally absent during an ordinary image build.

The training image installs PaddleDetection but omits TensorRT. The inference
image also omits TensorRT and defaults to Paddle Inference FP32. A deployment
must not request `paddlex_hpi_tensorrt` unless a different immutable runtime
explicitly declares TensorRT support and the target-node inventory verifies
the corresponding CUDA, TensorRT, and compute-capability requirements.

The training build defers PaddleDetection's optional rotated-detection custom
operator compilation because it requires a runtime-injected NVIDIA driver.
The standard PP-YOLOE and RT-DETR object-detection paths remain installed;
rotated detection is not a declared capability of this image.

The Dockerfiles default to `https://mirrors.aliyun.com/pypi/simple` for Python
packages. Operators may override `PIP_INDEX_URL` at build time with another
verified mirror without changing the pinned CUDA manifest or Paddle wheel
checksum.

Set the five `VISIOX_*_IMAGE_DIGEST` variables in the deployment `.env` to
these pinned references. Production mTLS Compose refuses to start when any
runtime digest is missing. A tag such as `:latest` or `:2026-08-07` is not an
acceptable replacement.

## Compose and Observability

Validate the default stack before rollout:

```powershell
docker compose -f infra/compose/docker-compose.yml config
docker compose -f infra/compose/docker-compose.yml up -d api-service mlflow tensorboard
```

MLflow and TensorBoard include HTTP health checks. VisualDL is deliberately
optional and exposes no URL by default. To serve VisualDL records for operator
debugging, explicitly enable both the profile and its public URL:

```powershell
$env:VISIOX_VISUALDL_PUBLIC_URL = "https://visualdl.example.internal"
docker compose -f infra/compose/docker-compose.yml --profile visualdl up -d visualdl
```

Remove `VISIOX_VISUALDL_PUBLIC_URL` when the profile is stopped. Do not expose
the VisualDL port from production mTLS Compose unless a protected ingress is
configured.

## Edge Preparation and Cache

Before enabling a runtime on an edge node, confirm Docker access, the NVIDIA
Container Toolkit, a driver compatible with the image CUDA version, free disk
space, and the required GPU memory. Pre-pull the digest during maintenance:

```powershell
ssh edge-node "docker pull $pinned && docker image inspect $pinned --format '{{index .RepoDigests 0}}'"
```

Docker stores image layers in its configured data root (normally
`/var/lib/docker` on Linux). PaddleX downloads its official model assets inside
the runtime container cache on first use; keep the cache on the node's managed
Docker storage and do not seed or download those weights from API startup.

For PP-YOLOE-S and RT-DETR-L, PaddleX publishes fixed pretraining URLs but no
official weight-file SHA-256. The seed contract therefore carries the PaddleX
`v3.0.3` immutable Git revision and SHA-256 of the exact official model config,
not a made-up weight checksum. The edge runtime must record the SHA-256 it
actually downloads in the artifact manifest before later staging or deployment
verification.

## Rollback

Keep the last known-good digest for every runtime. To roll back, replace only
the affected `VISIOX_*_IMAGE_DIGEST` with that prior digest, validate Compose,
and restart the control-plane service that resolves capabilities. Existing jobs
keep their persisted launch-spec digest and must not be rewritten.

```powershell
$env:VISIOX_PADDLEX_TRAINING_IMAGE_DIGEST = "registry.example/visiox/paddlex-training@sha256:<previous-64-hex>"
docker compose -f infra/compose/docker-compose.yml config
docker compose -f infra/compose/docker-compose.yml up -d --force-recreate api-service edge-executor-worker
```
