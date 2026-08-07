# PaddleX Object Detection Runbook

## Supported Models

The initial PaddleX object-detection catalog contains two official references:

| Product model | Runtime model id | Config path | Weight format | Revision |
| --- | --- | --- | --- | --- |
| PP-YOLOE-S | `PP-YOLOE_plus-S` | `paddlex/configs/modules/object_detection/PP-YOLOE_plus-S.yaml` | `pdparams` | `paddlex-model-zoo/3.0.3/PP-YOLOE_plus-S` |
| RT-DETR-L | `RT-DETR-L` | `paddlex/configs/modules/object_detection/RT-DETR-L.yaml` | `pdparams` | `paddlex-model-zoo/3.0.3/RT-DETR-L` |

The API seeds only these identifiers, configuration paths, format metadata, and
revisions. It does not download model weights or create fake weight files.
PaddleX resolves and caches official assets on the authorized edge runtime at
execution time.

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

Check framework availability through the API. A missing or tag-only runtime
reference must be fixed before creating a production job:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/frameworks | ConvertTo-Json -Depth 8
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

## Incident and Rollback

If a PaddleX runtime cannot pull, inspect the exact persisted digest and edge
node Docker logs before changing a job. Verify registry reachability, the
manifest digest, driver/CUDA compatibility, free disk, and GPU allocation. Do
not replace a job's persisted image reference with a mutable tag.

For a bad release, restore the prior known-good pinned digest, run Compose
configuration validation, recreate the API and edge executor, and submit a new
validation job. Preserve completed-job records, runtime digests, artifact
checksums, and model revisions as audit evidence.
