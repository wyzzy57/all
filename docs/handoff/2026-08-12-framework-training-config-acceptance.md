# Framework Training Configuration Acceptance

Date: 2026-08-12 / 2026-08-13

## Scope

This acceptance verifies the incremental framework configuration work built on top of the existing production training path. It does not replace the previously completed framework adapters, remote execution, artifact collection, or observability work.

## Static verification

- Frontend tests: 319 passed.
- Frontend type checking: passed.
- Frontend production build: passed.
- Framework capability, PaddleX configuration, and worker packaging tests: 73 passed.
- Unknown framework parameters are rejected with HTTP 422.
- Platform-managed parameters are rejected with HTTP 422.

## PaddleX real training

- Pipeline: `466ed59f-2bb4-47c9-a915-a7e94f5c3b48`
- Job: `5da3fd3c-e5de-454f-9e0d-55614f7de7df`
- Framework/model: PaddleX / PP-YOLOE-S
- Result: success, one epoch completed on node `eb6b9dfb-4cfe-4bdf-a27f-8e7b686ebea0`.
- Resolved parameters: `epochs=1`, `batch_size=1`, `learning_rate=0.00042`, `image_size=640`, `workers=1`, `amp=true`, `resume=false`.
- Metrics included loss, learning rate, COCO mAP/AR, and resource telemetry.
- Artifacts included `best_model.pdparams`, `model_final.pdparams`, and `inference.json`.

## Environment repairs during acceptance

- Restored the `skyinfor` user's membership in the remote Docker group.
- Published the configured PaddleX immutable image digest to the LAN registry.
- Removed only stopped training containers, dangling images, and other reproducible caches to make room for framework images. Running services and Docker volumes were preserved.

## Ultralytics acceptance

The framework capability catalog and YAML validation are verified. A final edge run was blocked by existing environment configuration before the training container started: the configured Ultralytics deployment image digest points at the historical `10.10.40.2:5000` registry, while the active training node uses `10.10.40.209:5000`. The API correctly rejected the submission because the inference image could not be resolved as an available immutable runtime. This is an environment image publication/configuration issue, not a parameter serialization or worker command issue.

The edge node was also found with a full disk and without Docker socket permission for `skyinfor`; the Docker group membership was restored, reproducible caches were removed, and the Ultralytics training image was pulled successfully. The remaining follow-up is to publish or configure the matching Ultralytics inference digest on the node's reachable registry, then rerun the same one-epoch job.
