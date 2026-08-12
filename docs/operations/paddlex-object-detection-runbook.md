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

## Task 16 Production Acceptance Record (2026-08-11)

Status: accepted for the production object-detection loop on the authorized
x86 NVIDIA node. PP-YOLOE-S, RT-DETR-L, deployment, live inference,
stop/start, restart reconciliation, cross-framework comparison, and
observability fallback were exercised with real workloads.
Values in this section are measured production evidence, not examples.

Local regression on the final acceptance changes:

- Backend: 1,276 passed, 6 skipped, 4 warnings in 1,629.28 seconds.
- Frontend: 307 Vitest tests passed; typecheck and production build exited 0.
- Ruff passed for every changed Python file; `git diff --check` passed.
- Compose configuration resolved successfully and the rebuilt edge executor ran
  the committed MLflow fallback and dataset-mount code.

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

Authentication was restored with an in-memory short-lived token for existing
active administrator `db301bee-e768-4ac7-a516-6d88e71c74dc`; no user or refresh
session was created. Actual responses were HTTP 200 for `/auth/me`, framework
capabilities, validated datasets, and the published versions endpoint.

Final immutable runtime images in `10.10.40.209:5000`:

- Training linux/amd64:
  `visiox/paddlex-training@sha256:f207f9f8b886a7393e534b170d6a41c43235075c03f97e5d7feafc715a04b318`.
- Inference linux/amd64:
  `visiox/paddlex-inference@sha256:4b04842bd3b0963d84301fe851dd21eda501ca9c01998f6a42e19ea2707d7465`.
- Immutable CUDA base:
  `nvidia/cuda:11.8.0-base-ubuntu22.04@sha256:79e5b2cf878ee9006f5b3738caeea34fdc7708a32db53fe3e80db0b48bd286a0`.

The images install the exact official PaddlePaddle GPU 3.0.0 cp310 cu118
wheel. The inference image uses Paddle fallback; TensorRT was not installed or
claimed. Authenticated capabilities report PaddleX operations implemented and
available only when both immutable digests and actual operation registrations
are present.

Verified PP-YOLOE-S production run:

- Pipeline `812f65e7-9021-4838-940d-6488bbf65cfe`.
- Job `9df01d23-1427-4045-8d73-9f08f05f6945`, status `success`.
- Attempt `0f6bafba-d3f9-4cd0-bd29-f3af2135caea`; remote container
  `5db9f31b024f`, exit 0, not OOM-killed.
- Trained model `dba8fa92-fae4-46a1-a683-19366977b1da`, status `ready`.
- Launch-spec SHA-256
  `13843b04b7bf4eb8e854efbd997abda0e21b0a65f852499bb2a91173e318da17`.
- Static inference JSON SHA-256
  `805baa6fb80c1a32828c487d388408d5257b2ec7f03b02bdda172a277b9b3fac`.
- Full artifact-manifest SHA-256
  `356b7ce3ee6324832e26d5177f514b9d6b25416ed4a1f07517e64e8dc92c7163`.

The successful run completed two epochs, both evaluations, final export,
artifact upload, and observability capture. Its terminal metrics were bbox mAP
0.0 and loss 39.052879; acceptance verifies the production path, not model
quality. A final-image GPU export probe also returned exit 0.

Verified restart-safe RT-DETR-L production run:

- Pipeline `40b0080b-9d61-406e-ac03-d859e902151f`.
- Job `e4986683-124a-4fbd-bef7-a4fc69ca0b21`; run
  `efbe4c8c-d538-4976-a5a4-5ddd9adb0e9a`; remote execution
  `3c026e75-16bb-4dce-8123-0ba26cc7e5ae`.
- Trained model `430cdb3c-759b-4b6e-8b82-aed49660a996`.
- The active remote container remained `bb6ca5...` across the control-plane
  restart. Job identity, logs, metrics, and artifacts reconciled successfully.
- Terminal metrics: bbox mAP 0.735, AP50 0.894, AP75 0.859, AR 0.877.
- MLflow, TensorBoard, VisualDL, and GPU telemetry were present.

Verified deployment and HTTP inference:

- Service `6929d02d-f079-45b1-aab8-0d805572b514`; instance
  `289a3cf5...`; container `2f4edbadacd07c884e40007760c470c752ae76387e58aa07b0cb5af1948bde21`.
- Endpoint `http://10.10.13.20:18083` returned a real image prediction with
  51 detections, labels `0` and `2`, and 226 ms reported latency.
- Stop/start completed successfully. Restart reconciliation retained the same
  container and reported phase `reconciled_running` with a healthy endpoint.

Verified PP-YOLOE-S deployment lifecycle:

- Service `23f0ef27-de2b-42fc-a6b9-b5522fa27ec2`; instance
  `a6330a44-5b67-441e-974f-0fb13fa1ecf7`; endpoint
  `http://10.10.13.20:18084`.
- Model bundle SHA-256
  `41eb24e0d460f1471e14ebbdde8ef9243c80eabcbcba7871775b9435f10db376`.
- Initial deploy execution `412d28b7-2e60-4499-a10b-3748df9433a8`, stop
  `6e79877e-3af0-43cb-86ea-485a03237d99`, and start
  `aa9304f3-165f-40c3-b9ca-41818426988a` all succeeded. Start reconciliation
  retained the same full container identity and a healthy endpoint.
- A real image request returned HTTP success, a rendered result image, and
  184.643 ms reported latency. This low-quality two-epoch acceptance model
  returned zero detections for the sample; the API and image post-processing
  path completed normally.
- Upgrade execution `ac364eaf-9602-4256-9e7f-73495d004b18` advanced both
  service and instance to revision 3 with container
  `55b57a67319573f6f7f5fb45943130b6eb9b264ade49c1f734c968a93440e1a0`.
- Rollback execution `6760da45-e0a7-432c-8fe5-6e53a41b86ba` restored container
  `09014fa955123eafb73cef40b0a6f5f86e703e5a3ad7cfa024fde368019d0863`;
  service and instance both converged to revision 2, phase
  `reconciled_running`, and health `healthy`.
- Acceptance exposed and fixed two production defects: PaddleX static-input
  models must preserve the exported input shape, and rollback comparison must
  use full Docker container identities. The control plane now also persists
  the target rollback revision instead of the queued revision.

Verified Ultralytics comparison on the same DatasetVersion:

- The original PaddleX pipeline rejected a framework mutation with HTTP 409
  and `Pipeline framework identity is locked; clone the pipeline to change it`.
- Clone pipeline `353f47fd-73da-4f76-9d74-5b5556ea9278`; job
  `a0cef819-c6f0-4a00-b678-2e5296ff2f0a`; run
  `2b0b00d1-1129-4f03-a166-b84d1a5e7b3a`; execution
  `3a881369-eb2d-402d-8aeb-89e3d575c0df`; model
  `f96453c8-1616-4c09-9baa-6cb8a4067cbe`.
- The two-epoch job produced `best.pt`, `last.pt`, epoch samples, GPU metrics,
  and the full visualization set.
- Canonical comparison metrics: precision 0.403486, recall 0.612153, mAP50
  0.504386, and mAP50-95 0.316008. Framework-private losses were not compared.

Verified MLflow outage and fallback:

- Pipeline `dacca306-272c-4b8c-b5f8-ec5b4dc83ef5`; job
  `3bc3f128-fd3c-4b75-ba03-1f27bc4d77e5`; run
  `1debd5a7-a512-4acc-b2c7-907a699158d6`; execution
  `6bf8be19-e9cc-4032-a97b-bcfb975029e2`.
- The two-epoch PaddleX job completed while the configured MLflow endpoint was
  unavailable. Its immutable run snapshot records MLflow unavailable with the
  connection timeout reason while VisualDL, JSONL progress, resource telemetry,
  and artifacts remained available.
- Terminal metrics: bbox mAP 0.746, AP50 0.912, AP75 0.873, AR 0.852; observed
  GPU memory 10,567/12,288 MiB.
- `best_model.pdparams` model `2285e214-c8ad-4034-8dee-3be19399ba1d`
  and `model_final.pdparams` model `45d3765b-77e8-43cd-8d8a-04efd750d9c0`
  both have SHA-256
  `37df693f0bd1459e8876505659e12ef919b5bf6be023b08f49c4213fae3fcc47`.
  Static `inference.json` model `7b122750-a027-4d39-8953-1aa1dacb7da1`
  has SHA-256
  `bc5a85c0bf535ab3ef1578e78e99cf1bf23bd0ffbe22f696447eddc41914caef`.
- Validation ran inside the training attempt and did not create a separate
  `PipelineEvaluation` row, so there is no standalone evaluation ID for this
  run. The evaluation metrics above remain attached to the immutable run.
- After MLflow was restored, its HTTP endpoint returned 200 and the authenticated
  observability summary reported MLflow available again. The historical run
  snapshot correctly remained a record of the outage experienced during training.

During acceptance, a failed run exposed edge disk exhaustion rather than a
PaddleX or MLflow failure. Cleanup removed only stopped, run-labelled
containers and their exact resolved workspaces after durable failure or success
was confirmed. Two unreferenced Ultralytics images were removed only after
verifying that no container used them; their immutable registry manifests were
preserved. No running container, volume, dataset, model cache, database record,
or object-storage artifact was deleted.

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

The final PaddleX export has the same repository cwd. Keep PaddleDetection
config `save_dir` synchronized with PaddleX's absolute `Global.output` before
the export subprocess starts; `--output_dir` alone is applied too late to stop
trainer initialization from creating a relative `output` directory.
