from __future__ import annotations

import base64
import importlib
import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visiox_yolo26_inference.config import InferenceConfig, InferenceConfigError, validate_model_task_match
from visiox_yolo26_inference.main import create_app


YOLO26_TASKS = ["detect", "segment", "semantic", "pose", "obb", "classify"]


def png_bytes(width: int = 64, height: int = 48) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color=(12, 34, 56)).save(buffer, format="PNG")
    return buffer.getvalue()


def config_for(task: str) -> InferenceConfig:
    return InferenceConfig(
        task=task,
        model_path=f"/app/model/{task}.onnx",
        model_format="onnx",
        device="cpu",
        confidence=0.25,
        iou=0.7,
        class_names=["ok", "defect"],
        input={"type": "http"},
    )


@pytest.mark.parametrize("task", YOLO26_TASKS)
def test_inference_service_loads_all_yolo26_tasks(task: str):
    with TestClient(create_app(config_for(task))) as client:
        health = client.get("/health")
        info = client.get("/model/info")

    assert health.status_code == 200
    assert health.json()["task"] == task
    assert info.json()["task"] == task
    assert info.json()["model_format"] == "onnx"


def test_predict_image_returns_stable_response_and_metrics():
    with TestClient(create_app(config_for("detect"))) as client:
        response = client.post(
            "/predict/image",
            files={"file": ("frame.png", png_bytes(), "image/png")},
        )
        metrics = client.get("/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["task"] == "detect"
    assert body["image"]["width"] == 64
    assert body["image"]["height"] == 48
    assert isinstance(body["predictions"], list)
    assert metrics.json()["prediction_requests"] == 1
    assert metrics.json()["prediction_errors"] == 0
    assert metrics.json()["last_latency_ms"] is not None


def test_predict_video_frame_accepts_base64_image():
    payload = {
        "image_base64": base64.b64encode(png_bytes(32, 24)).decode("ascii"),
        "camera_id": "cam-1",
        "timestamp_ms": 123,
    }
    with TestClient(create_app(config_for("classify"))) as client:
        response = client.post("/predict/video-frame", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["task"] == "classify"
    assert body["image"]["width"] == 32
    assert body["image"]["height"] == 24
    assert body["predictions"][0]["label"] in {"ok", "defect"}


def test_runtime_reload_updates_model_info_and_metrics():
    with TestClient(create_app(config_for("detect"))) as client:
        reload_response = client.post(
            "/runtime/reload",
            json={"task": "segment", "model_path": "/app/model/segment.onnx", "class_names": ["scratch"]},
        )
        info = client.get("/model/info")
        metrics = client.get("/metrics")

    assert reload_response.status_code == 200
    assert reload_response.json()["task"] == "segment"
    assert info.json()["task"] == "segment"
    assert info.json()["class_names"] == ["scratch"]
    assert metrics.json()["reload_count"] == 1


def test_invalid_base64_increments_error_metrics():
    with TestClient(create_app(config_for("detect"))) as client:
        response = client.post("/predict/video-frame", json={"image_base64": "not-base64"})
        metrics = client.get("/metrics")

    assert response.status_code == 422
    assert metrics.json()["prediction_requests"] == 1
    assert metrics.json()["prediction_errors"] == 1


def test_model_task_mismatch_fails_fast():
    config = InferenceConfig(
        task="detect",
        model_path="/app/model/segment.onnx",
        model_format="onnx",
        class_names=["defect"],
    )

    with pytest.raises(InferenceConfigError, match="model task"):
        validate_model_task_match(config)

    with TestClient(create_app(config_for("detect"))) as client:
        response = client.post("/runtime/reload", json={"model_path": "/app/model/pose.onnx"})

    assert response.status_code == 422
    assert "model task" in response.json()["detail"]


def test_runtime_reload_failure_keeps_existing_runtime():
    with TestClient(create_app(config_for("detect"))) as client:
        response = client.post("/runtime/reload", json={"task": "pose", "model_path": "/app/model/segment.onnx"})
        info = client.get("/model/info")
        metrics = client.get("/metrics")

    assert response.status_code == 422
    assert info.json()["task"] == "detect"
    assert metrics.json()["reload_count"] == 0
    assert metrics.json()["reload_errors"] == 1


def test_load_config_wraps_invalid_field_errors(tmp_path):
    config_path = tmp_path / "bad.json"
    config_path.write_text(json.dumps({"task": "bad", "model_format": "onnx"}), encoding="utf-8")

    with pytest.raises(InferenceConfigError, match="unsupported YOLO26 task"):
        from visiox_yolo26_inference.config import load_config

        load_config(config_path)


def test_package_import_does_not_load_runtime_config(monkeypatch, tmp_path):
    bad_path = tmp_path / "missing.json"
    monkeypatch.setenv("VISIOX_INFERENCE_CONFIG", str(bad_path))

    import visiox_yolo26_inference

    importlib.reload(visiox_yolo26_inference)


def test_predict_image_rejects_non_image_content_type():
    with TestClient(create_app(config_for("detect"))) as client:
        response = client.post(
            "/predict/image",
            files={"file": ("frame.txt", b"not image", "text/plain")},
        )
        metrics = client.get("/metrics")

    assert response.status_code == 415
    assert metrics.json()["prediction_requests"] == 1
    assert metrics.json()["prediction_errors"] == 1


def test_ambiguous_model_task_hint_fails_fast():
    config = InferenceConfig(
        task="detect",
        model_path="/app/model/not-detect-segment.onnx",
        model_format="onnx",
    )

    with pytest.raises(InferenceConfigError, match="ambiguous"):
        validate_model_task_match(config)
