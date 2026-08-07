# PaddleX Object Detection Runbook

## Supported Models

The initial PaddleX object-detection catalog contains two official references:

| Product model | Runtime model id | Config path | Weight format | Immutable revision |
| --- | --- | --- | --- | --- |
| PP-YOLOE-S | `PP-YOLOE_plus-S` | `paddlex/configs/modules/object_detection/PP-YOLOE_plus-S.yaml` | `pdparams` | PaddleX `v3.0.3` commit `917f10be35f644d01a6bf57cf8c312bdba0a4183` |
| RT-DETR-L | `RT-DETR-L` | `paddlex/configs/modules/object_detection/RT-DETR-L.yaml` | `pdparams` | PaddleX `v3.0.3` commit `917f10be35f644d01a6bf57cf8c312bdba0a4183` |

The API seeds only these identifiers, fixed configuration sources, format
metadata, and immutable revisions. Each seed also records the SHA-256 of the
official configuration content at that commit. PaddleX does not publish a
weight-file SHA-256 for either official pretraining URL, so the seed explicitly
uses the immutable upstream revision and configuration checksum instead of
inventing an artifact checksum. It does not download model weights or create
fake weight files. PaddleX resolves and caches official assets on the
authorized edge runtime at execution time.

The PaddleX 3.0.3 wheel does not package the two registered
`repo_apis/PaddleDetection_api/configs` YAML files. The training image therefore
retrieves those files from the immutable upstream `v3.0.3` tag and verifies
SHA-256 `20ec1fc27f96943026ebf7306ce850ab784be0dd7975c2fc03341f1a776bd0f8`
for PP-YOLOE-S and
`fa18adf6bc279ac628e6775ac33274913a6ebd3a74df78231620cc859bdf179d`
for RT-DETR-L. Its build must instantiate both registered configurations before
the image can be published.

## Preflight

1. Publish and pin `VISIOX_PADDLEX_TRAINING_IMAGE_DIGEST` and
   `VISIOX_PADDLEX_INFERENCE_IMAGE_DIGEST` as documented in
   [the runtime image guide](multi-framework-runtime-images.md).
2. Verify that the edge node has NVIDIA drivers compatible with the pinned
   PaddlePaddle CUDA image, Docker GPU support, 8 GB or more available GPU
   memory, and enough Docker data-root space for the image and model cache.
3. Pre-pull the pinned training and inference images on the edge node.
4. Validate the source dataset in Visiox. PaddleX detection accepts a COCO
   dataset; Label Studio and YOLO inputs must complete the platform conversion.

## Enable and Verify

Start the normal control plane. PaddleX images remain absent from the Compose
service list because jobs launch on an edge node:

```powershell
docker compose -f infra/compose/docker-compose.yml config
docker compose -f infra/compose/docker-compose.yml up -d api-service edge-executor-worker mlflow tensorboard
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health
```

Check framework availability through the authenticated API. Set the operator
credentials in the current shell rather than placing them in this runbook. A
missing or tag-only runtime reference must be fixed before creating a
production job:

```powershell
$apiBaseUrl = "http://127.0.0.1:8000"
$loginBody = @{
  username = $env:VISIOX_OPERATOR_USERNAME
  password = $env:VISIOX_OPERATOR_PASSWORD
} | ConvertTo-Json
$login = Invoke-RestMethod -Method Post -ContentType "application/json" `
  -Body $loginBody "$apiBaseUrl/auth/login"
$headers = @{ Authorization = "Bearer $($login.access_token)" }
Invoke-RestMethod -Headers $headers `
  "$apiBaseUrl/frameworks/capabilities?task_kind=object_detection" |
  ConvertTo-Json -Depth 8
```

For temporary VisualDL troubleshooting only, configure a protected public URL
and enable the optional profile:

```powershell
$env:VISIOX_VISUALDL_PUBLIC_URL = "https://visualdl.example.internal"
docker compose -f infra/compose/docker-compose.yml --profile visualdl up -d visualdl
```

Stop the profile and remove the environment variable after diagnosis. The
standard operator experience is the Visiox UI; MLflow, TensorBoard, and
VisualDL are secondary diagnostics.

## Task 16 Production Acceptance Record (2026-08-07)

Status: blocked before image publication; neither model loop has started.
Values in this section are measured production evidence, not examples.

Local regression at baseline `262b93e` plus the verified acceptance fixes:

- Backend: 1,253 passed, 6 skipped, 2 warnings in 1,624.60 seconds.
- Frontend: 307 Vitest tests passed; typecheck and production build exited 0.
- PaddleX dataset adapter: 25 passed, 1 warning in 62.00 seconds.
- Distributed edge plan: 67 passed in 1.68 seconds.

Authorized edge node:

- Node ID: `eb6b9dfb-4cfe-4bdf-a27f-8e7b686ebea0`.
- SSH target: `skyinfor@10.10.13.20`; host-key fingerprint
  `SHA256:J4g1pAJzxrMrNV6SJG8uUQ4WYDXlwhAGjKKagw2sfr8`.
- GPU: NVIDIA RTX 3060 12 GB, driver 595.84.
- Registry: `http://10.10.40.209:5000/v2/` returned HTTP 200. The former
  `10.10.40.2:5000` endpoint was unreachable.
- MinIO: `http://10.10.40.209:9000/minio/health/live` returned HTTP 200 from
  the edge node. The former `.2:9000` endpoint timed out.

Authorized disk recovery removed only stopped containers
`f9d12d0458a3c65722d1b5648fe3b0c5038043299bc0e06d618e1afc55e3a8f9` and
`bbf98e04e2273044fefb478e8416ca62e9d5c4f3a7b0e51d4414b11b49b4943b`,
then their unreferenced local image. The recoverable registry manifest digest
is `sha256:7aaab84daf3fce162fc181068223a39cd7a94466a79cff70242659cc420c9fd4`.
Available root space increased from 1,288,720,384 to 18,489,417,728 bytes.
No running container, volume, or bind-mounted dataset/output/model-cache path
was deleted.

Published COCO DatasetVersion:

- Source dataset: `bbf3124e-88b6-4700-bf6a-da76cdae8662` (`visio_huajiao`).
- DatasetVersion: `a90b9149-8f22-49be-b656-04715d2962e6`, version 1, format
  `coco`, status `published`.
- Source revision:
  `ff2c7394aaf1f368aaafbc1f8a5841e61d097cd50b616085f59895693aed9a89`.
- Counts: 135 valid images and 4,320 boxes; train 100/3,394, val 35/926,
  test 0/0.
- Adapter manifest checksum:
  `51e7d8f8996e0104bac9303e1e59061875bd324bd4481437525b61ea52c2235b`.
- Manifest-file SHA-256:
  `6f717bade6dd23b9653732efeb8a2b897ee35a69e594b072bfd03b53d976f4c5`.
- COCO archive SHA-256:
  `66e7c29272994a7be4537b039ccb8dfee98de73c7402f7be0f24fa714fb99a0f`;
  317,460,760 bytes.
- Object URI:
  `minio://datasets/bbf3124e-88b6-4700-bf6a-da76cdae8662/versions/1/paddlex-coco-66e7c29272994a7be4537b039ccb8dfee98de73c7402f7be0f24fa714fb99a0f.tar.gz`.
- Manifest URI:
  `minio://datasets/bbf3124e-88b6-4700-bf6a-da76cdae8662/versions/1/dataset-manifest.json`.

Authentication was restored with an in-memory 10-minute token for existing
active administrator `db301bee-e768-4ac7-a516-6d88e71c74dc`; no user or refresh
session was created. Actual responses were HTTP 200 for `/auth/me`, framework
capabilities, validated datasets, and the published versions endpoint. The
capability response still reports every PaddleX operation unimplemented and
has null runtime digests, so that gate did not pass.

Image publication blocker:

- Required base manifest:
  `sha256:2bd8830dafd258501e7313b320fa1bcc946c70318b3081647b90bc70182e7360`.
- Docker Hub repeatedly ended a 2,445,884,909-byte layer with TLS timeout or
  short-read `unexpected EOF`.
- The official Baidu source was manifest-identical, but its one pull failed
  blob `sha256:6d999be01a3b22cdb15a2c18ad0ea3751c2f66cca7b08127f16397fce0d447d5`
  with HTTP 500 and `unexpected EOF` after 335.9 seconds.
- No complete source exists in the local image store, BuildKit cache, or LAN
  registry. Training and inference image digests therefore do not exist.

Uncreated evidence due to that blocker: PP-YOLOE-S job ID, RT-DETR-L job ID,
trained model IDs/checksums, evaluation IDs, deployment service IDs, and HTTP
prediction responses. The unrelated GPU service remained running as directed;
stopping it would not resolve the earlier image/control-plane blockers.

## Incident and Rollback

If a PaddleX runtime cannot pull, inspect the exact persisted digest and edge
node Docker logs before changing a job. Verify registry reachability, the
manifest digest, driver/CUDA compatibility, free disk, and GPU allocation. Do
not replace a job's persisted image reference with a mutable tag.

For a bad release, restore the prior known-good pinned digest, run Compose
configuration validation, recreate the API and edge executor, and submit a new
validation job. Preserve completed-job records, runtime digests, artifact
checksums, and model revisions as audit evidence.

PaddleDetection evaluation runs from the bundled repository rather than the
image `WORKDIR`. Keep the generated `output_eval` config pinned to the writable
training output root; otherwise COCO evaluation attempts to create a relative
`bbox.json` in the read-only repository and exits with `PermissionError`.
