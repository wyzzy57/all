from __future__ import annotations

import base64
import importlib
import json
from io import BytesIO
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visiox_yolo26_inference.config import InferenceConfig, InferenceConfigError, validate_model_task_match
from visiox_yolo26_inference.main import create_app
from visiox_yolo26_inference.optimize import OptimizationRequest, optimize_model
from visiox_yolo26_inference.predict import DeterministicPredictor, load_predictor


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


def test_production_config_accepts_engine_for_detect_http_image_only():
    config = InferenceConfig(
        production=True,
        task="detect",
        model_path="/models/model.engine",
        model_format="engine",
        device="cuda:0",
        input={"type": "http", "shape": [1, 3, 640, 640]},
    )

    with TestClient(create_app(config, predictor=DeterministicPredictor(config))) as client:
        paths = {route.path for route in client.app.routes}
        health = client.get("/health")
        prediction = client.post(
            "/predict/image",
            files={"file": ("frame.png", png_bytes(), "image/png")},
        )

    assert config.model_format == "engine"
    assert health.status_code == 200
    assert prediction.status_code == 200
    assert "/predict/image" in paths
    assert "/predict/video-frame" not in paths
    assert "/runtime/reload" not in paths


@pytest.mark.parametrize(
    "payload",
    [
        {
            "production": True,
            "task": "segment",
            "model_path": "/models/model.engine",
            "model_format": "engine",
            "input": {"type": "http"},
        },
        {
            "production": True,
            "task": "detect",
            "model_path": "/models/model.engine",
            "model_format": "engine",
            "input": {"type": "video"},
        },
    ],
)
def test_production_config_rejects_non_detect_or_non_http_input(payload):
    with pytest.raises(ValueError, match="production inference"):
        InferenceConfig.model_validate(payload)


def test_inference_dockerfile_is_immutable_non_root_and_exec_form():
    dockerfile = (
        Path(__file__).parents[2] / "apps" / "yolo26-inference" / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert dockerfile.splitlines()[0].startswith("FROM python:3.12-slim-bookworm@sha256:")
    assert "USER visiox" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "CMD [" in dockerfile
    assert 'CMD ["sh", "-c"' not in dockerfile
    assert "tensorrt-cu12" in dockerfile
    assert "COPY apps/yolo26-inference/src/visiox_yolo26_inference ./visiox_yolo26_inference" in dockerfile
    assert "COPY workers" not in dockerfile
    assert "COPY packages" not in dockerfile
    assert "--mount=type=cache,target=/root/.cache/pip" in dockerfile
    assert "https://download.pytorch.org/whl/cu126" in dockerfile
    assert "ARG TORCH_VERSION=" in dockerfile
    assert "ARG ULTRALYTICS_VERSION=" in dockerfile
    assert '"mlflow' not in dockerfile
    assert '"cleanvision' not in dockerfile


def test_production_predictor_loads_engine_and_serializes_real_detect_boxes(
    monkeypatch,
):
    calls: dict[str, object] = {}

    class Values:
        def __init__(self, values):
            self.values = values

        def tolist(self):
            return self.values

    class FakeYolo:
        def __init__(self, path, *, task):
            calls["load"] = (path, task)
            self.names = {0: "pepper"}

        def predict(self, **kwargs):
            calls["predict"] = kwargs
            return [
                SimpleNamespace(
                    boxes=SimpleNamespace(
                        xyxy=Values([[1.0, 2.0, 30.0, 40.0]]),
                        conf=Values([0.91]),
                        cls=Values([0.0]),
                    )
                )
            ]

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYolo))
    config = InferenceConfig(
        production=True,
        task="detect",
        model_path="/models/model.engine",
        model_format="engine",
        device="cuda:0",
        confidence=0.3,
        iou=0.5,
        input={"type": "http", "shape": [1, 3, 640, 640]},
    )

    predictor = load_predictor(config)
    result = predictor.predict_image(png_bytes(), {"filename": "frame.png"})

    assert calls["load"] == (str(config.model_path), "detect")
    assert calls["predict"]["conf"] == 0.3  # type: ignore[index]
    assert calls["predict"]["iou"] == 0.5  # type: ignore[index]
    assert calls["predict"]["device"] == "cuda:0"  # type: ignore[index]
    assert result.predictions == [
        {
            "class_id": 0,
            "label": "pepper",
            "confidence": 0.91,
            "bbox": {"x1": 1.0, "y1": 2.0, "x2": 30.0, "y2": 40.0},
        }
    ]
    assert result.image["width"] == 64


def test_optimizer_exports_engine_in_writable_workspace_and_copies_exact_output(
    tmp_path,
):
    source = tmp_path / "source.pt"
    source.write_bytes(b"pt-model")
    output = tmp_path / "cache" / "model.engine"
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeYolo:
        def __init__(self, path):
            self.path = Path(path)

        def export(self, **kwargs):
            calls.append((str(self.path), kwargs))
            produced = self.path.with_suffix(".engine")
            produced.write_bytes(b"tensorrt-engine")
            return str(produced)

    request = OptimizationRequest(
        source=source,
        source_format="pt",
        target_format="engine",
        precision="fp16",
        input_shape=(1, 3, 640, 640),
        output=output,
    )

    result = optimize_model(request, yolo_factory=FakeYolo)

    assert result == output
    assert output.read_bytes() == b"tensorrt-engine"
    assert calls[0][1] == {
        "format": "engine",
        "imgsz": (640, 640),
        "half": True,
        "int8": False,
        "device": 0,
        "batch": 1,
    }


def test_optimizer_requires_calibration_for_int8(tmp_path):
    source = tmp_path / "source.pt"
    source.write_bytes(b"pt-model")

    with pytest.raises(ValueError, match="calibration"):
        OptimizationRequest(
            source=source,
            source_format="pt",
            target_format="engine",
            precision="int8",
            input_shape=(1, 3, 640, 640),
            output=tmp_path / "model.engine",
        )
