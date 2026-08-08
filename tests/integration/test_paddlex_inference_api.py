from __future__ import annotations

import base64
from io import BytesIO
import re
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from pydantic import ValidationError

from visiox_api.services.pipeline_inference import DockerPaddleXInferenceRuntime
from visiox_api.services.pipeline_inference import PaddleXInferenceRuntimeRequest
from visiox_paddlex_inference.config import InferenceConfig
from visiox_paddlex_inference.main import create_app
from visiox_paddlex_inference.predict import PredictionResult
from visiox_paddlex_inference.predict import load_predictor


class FakePredictor:
    runtime_metadata = {
        "resolved_backend": "paddlex_hpi_tensorrt",
        "resolved_precision": "fp16",
    }

    def predict_image(self, image_bytes: bytes, metadata=None) -> PredictionResult:
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
        return PredictionResult(
            task="detect",
            predictions=[
                {
                    "class_id": 0,
                    "label": "pepper",
                    "confidence": 0.91,
                    "bbox": {"x1": 1.0, "y1": 2.0, "x2": 20.0, "y2": 30.0},
                }
            ],
            latency_ms=4.25,
            image={
                "width": width,
                "height": height,
                "mode": "RGB",
                "metadata": metadata or {},
            },
        )


def _image_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (64, 48), "white").save(stream, format="PNG")
    return stream.getvalue()


def test_paddlex_inference_matches_deployed_service_http_contract(tmp_path) -> None:
    config = InferenceConfig(
        production=True,
        model_dir=tmp_path,
        device="gpu:0",
        backend="paddlex_hpi_tensorrt",
        precision="fp16",
        input_size=(640, 640),
        optimization="auto",
        class_names=["pepper"],
    )
    with TestClient(create_app(config, predictor=FakePredictor())) as client:
        health = client.get("/health")
        metadata = client.get("/metadata")
        prediction = client.post(
            "/predict/image",
            files={"file": ("pepper.png", _image_bytes(), "image/png")},
        )

    assert health.status_code == 200
    assert health.json() == {
        "service": "paddlex-inference",
        "status": "ok",
        "task": "detect",
        "model_loaded": True,
    }
    assert metadata.status_code == 200
    assert metadata.json()["resolved_backend"] == "paddlex_hpi_tensorrt"
    assert metadata.json()["resolved_precision"] == "fp16"
    assert metadata.json()["model_format"] == "paddle_inference_bundle"
    assert prediction.status_code == 200
    assert prediction.json()["task"] == "detect"
    assert prediction.json()["predictions"][0]["label"] == "pepper"
    assert prediction.json()["latency_ms"] == 4.25


def test_paddlex_inference_rejects_non_image_and_oversized_upload(tmp_path) -> None:
    app = create_app(InferenceConfig(model_dir=tmp_path), predictor=FakePredictor())
    with TestClient(app) as client:
        unsupported = client.post(
            "/predict/image", files={"file": ("bad.txt", b"x", "text/plain")}
        )
        oversized = client.post(
            "/predict/image",
            files={"file": ("huge.png", b"x" * (10 * 1024 * 1024 + 1), "image/png")},
        )

    assert unsupported.status_code == 415
    assert oversized.status_code == 413


@pytest.mark.parametrize(
    (
        "device",
        "backend",
        "precision",
        "input_size",
        "expected_create_kwargs",
        "expected_metadata",
    ),
    [
        (
            "cpu",
            "paddle_inference",
            "fp32",
            (640, 640),
            {
                "device": "cpu",
                "img_size": (640, 640),
                "use_hpip": False,
            },
            {
                "resolved_backend": "paddle_inference",
                "resolved_precision": "fp32",
            },
        ),
        (
            "gpu:1",
            "paddlex_hpi_tensorrt",
            "fp16",
            (640, 384),
            {
                "device": "gpu:1",
                "img_size": (640, 384),
                "use_hpip": True,
                "hpi_params": {
                    "selected_backends": {"gpu": "tensorrt"},
                    "backend_config": {
                        "tensorrt": {
                            "precision": "FP16",
                            "dynamic_shapes": {
                                "x": [
                                    [1, 3, 320, 192],
                                    [1, 3, 640, 384],
                                    [1, 3, 1280, 768],
                                ]
                            },
                        }
                    },
                },
            },
            {
                "resolved_backend": "paddlex_hpi_tensorrt",
                "resolved_precision": "fp16",
            },
        ),
    ],
)
def test_paddlex_runtime_initializes_official_cpu_and_gpu_hpi_options(
    tmp_path,
    monkeypatch,
    device,
    backend,
    precision,
    input_size,
    expected_create_kwargs,
    expected_metadata,
) -> None:
    calls: dict[str, object] = {}

    class FakeResult:
        json = {
            "res": {
                "boxes": [
                    {
                        "cls_id": 2,
                        "label": "pepper",
                        "score": 0.875,
                        "coordinate": [1, 2, 20, 30],
                    }
                ]
            }
        }
        img = Image.new("RGB", (64, 48), "red")

    class FakeModel:
        def predict(self, **kwargs):
            image = kwargs["input"]
            calls["predict_image_size"] = image.size
            calls["predict_kwargs"] = kwargs
            return [FakeResult()]

    def create_model(**kwargs):
        calls["create_kwargs"] = kwargs
        return FakeModel()

    monkeypatch.setitem(
        sys.modules, "paddlex", SimpleNamespace(create_model=create_model)
    )
    config = InferenceConfig(
        production=True,
        model_dir=tmp_path,
        device=device,
        backend=backend,
        precision=precision,
        input_size=input_size,
        optimization="auto",
    )

    predictor = load_predictor(config)
    result = predictor.predict_image(_image_bytes())

    assert calls["create_kwargs"] == {
        "model_dir": str(tmp_path.resolve()),
        **expected_create_kwargs,
    }
    assert calls["predict_image_size"] == (64, 48)
    assert set(calls["predict_kwargs"]) == {"input", "threshold"}
    assert calls["predict_kwargs"]["threshold"] == 0.25
    assert calls["predict_kwargs"]["input"].size == (64, 48)
    assert predictor.runtime_metadata == expected_metadata
    assert result.predictions == [
        {
            "class_id": 2,
            "label": "pepper",
            "confidence": 0.875,
            "bbox": {"x1": 1.0, "y1": 2.0, "x2": 20.0, "y2": 30.0},
        }
    ]
    assert result.result_image is not None
    header, encoded = result.result_image.split(",", 1)
    assert header == "data:image/png;base64"
    with Image.open(BytesIO(base64.b64decode(encoded))) as rendered:
        assert rendered.size == (64, 48)


def test_paddlex_inference_rejects_invalid_image_and_unbounded_results(
    tmp_path,
) -> None:
    class InvalidImagePredictor:
        runtime_metadata = {
            "resolved_backend": "paddle_inference",
            "resolved_precision": "fp32",
        }

        def predict_image(self, image_bytes: bytes, metadata=None) -> PredictionResult:
            raise ValueError("invalid image")

    with TestClient(
        create_app(
            InferenceConfig(model_dir=tmp_path), predictor=InvalidImagePredictor()
        )
    ) as client:
        invalid = client.post(
            "/predict/image",
            files={"file": ("bad.png", b"not-an-image", "image/png")},
        )

    assert invalid.status_code == 422


def test_paddlex_inference_rejects_image_with_too_many_pixels(
    tmp_path,
) -> None:
    stream = BytesIO()
    Image.new("1", (7000, 6000), 1).save(stream, format="PNG")
    with TestClient(
        create_app(InferenceConfig(model_dir=tmp_path), predictor=FakePredictor())
    ) as client:
        response = client.post(
            "/predict/image",
            files={"file": ("large.png", stream.getvalue(), "image/png")},
        )

    assert response.status_code == 413


def test_paddlex_runtime_rejects_invalid_or_excessive_predictions(
    tmp_path, monkeypatch
) -> None:
    boxes = [
        {
            "cls_id": 0,
            "label": "pepper",
            "score": 0.9,
            "coordinate": [1, 2, 20, 30],
        }
        for _ in range(1001)
    ]

    class FakeModel:
        def predict(self, **kwargs):
            return [SimpleNamespace(json={"res": {"boxes": boxes}})]

    monkeypatch.setitem(
        sys.modules,
        "paddlex",
        SimpleNamespace(create_model=lambda **kwargs: FakeModel()),
    )
    predictor = load_predictor(
        InferenceConfig(production=True, model_dir=tmp_path, device="cpu")
    )

    with pytest.raises(ValueError, match="too many predictions"):
        predictor.predict_image(_image_bytes())

    boxes[:] = [
        {
            "cls_id": 0,
            "label": "pepper",
            "score": float("nan"),
            "coordinate": [1, 2, 20, 30],
        }
    ]
    with pytest.raises(ValueError, match="finite"):
        predictor.predict_image(_image_bytes())

    boxes[:] = [
        {
            "cls_id": 0,
            "label": "x" * 257,
            "score": 0.9,
            "coordinate": [1, 2, 20, 30],
        }
    ]
    with pytest.raises(ValueError, match="label is too long"):
        predictor.predict_image(_image_bytes())


def test_paddlex_dockerfile_uses_a_pinned_build_arg_base_image() -> None:
    dockerfile = (
        __import__("pathlib")
        .Path("apps/paddlex-inference/Dockerfile")
        .read_text(encoding="utf-8")
    )

    expected_image = (
        "nvidia/cuda:11.8.0-base-ubuntu22.04"
        "@sha256:79e5b2cf878ee9006f5b3738caeea34fdc7708a32db53fe3e80db0b48bd286a0"
    )
    paddle_wheel = (
        "https://paddle-whl.bj.bcebos.com/stable/cu118/paddlepaddle-gpu/"
        "paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl"
    )
    assert f"ARG CUDA_BASE_IMAGE={expected_image}" in dockerfile
    assert "FROM ${CUDA_BASE_IMAGE}" in dockerfile
    assert "cudnn8-runtime" not in dockerfile
    assert f"ARG PADDLE_WHEEL_URL={paddle_wheel}" in dockerfile
    assert "ARG UBUNTU_MIRROR=https://mirrors.aliyun.com/ubuntu" in dockerfile
    assert "Acquire::Retries=3" in dockerfile
    assert re.search(
        r"^ARG PADDLE_WHEEL_SHA256=[a-f0-9]{64}$",
        dockerfile,
        re.MULTILINE,
    )
    assert "sha256sum -c -" in dockerfile
    assert "--output /tmp/paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl" in dockerfile
    assert "pip install --no-cache-dir /tmp/paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl" in dockerfile
    assert (
        "from importlib.metadata import version; "
        "assert version('paddlepaddle-gpu') == '3.0.0'"
    ) in dockerfile
    assert 'import paddle; assert paddle.__version__' not in dockerfile
    assert "python3.10 -m venv" in dockerfile
    assert "paddlepaddle/paddle" not in dockerfile
    assert "tensorrt" not in dockerfile.casefold()
    assert "@sha256:" in dockerfile.splitlines()[0]
    assert "latest" not in dockerfile.casefold()
    assert (
        "COPY apps/paddlex-inference/requirements.lock /app/requirements.lock"
        in dockerfile
    )
    assert "pip install --no-cache-dir -r /app/requirements.lock" in dockerfile


def test_paddlex_dockerfile_uses_exported_bundle_without_training_plugin() -> None:
    dockerfile = (
        __import__("pathlib")
        .Path("apps/paddlex-inference/Dockerfile")
        .read_text(encoding="utf-8")
    )

    assert "paddlex --install" not in dockerfile
    assert "PaddleDetection.git" not in dockerfile
    assert "USER visiox" in dockerfile


def test_api_service_can_launch_the_isolated_paddlex_runtime() -> None:
    from pathlib import Path

    import yaml

    dockerfile = Path("apps/api-service/Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load(
        Path("infra/compose/docker-compose.yml").read_text(encoding="utf-8")
    )

    assert "docker-cli" in dockerfile
    assert "docker.io" not in dockerfile
    assert "pip install --no-cache-dir --retries 8 --timeout 120 ." in dockerfile
    socket_mount = "/var/run/docker.sock:/var/run/docker.sock"
    assert socket_mount in compose["services"]["api-service"]["volumes"]
    for service_name in (
        "edge-executor-worker",
        "label-sync-worker",
        "training-worker",
    ):
        assert socket_mount not in compose["services"][service_name].get("volumes", [])


def test_api_paddlex_runtime_copies_files_without_host_bind_paths(
    tmp_path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    model_dir = workspace / "model"
    input_dir = workspace / "input"
    output_dir = workspace / "output"
    model_dir.mkdir(parents=True)
    input_dir.mkdir()
    output_dir.mkdir()
    (model_dir / "inference.json").write_text("{}", encoding="utf-8")
    image_path = input_dir / "image.jpg"
    image_path.write_bytes(b"image")
    result_path = output_dir / "inference_result.json"
    calls: list[tuple[str, ...]] = []

    def fake_run(command, **_kwargs):
        call = tuple(command)
        calls.append(call)
        if call[:3] == ("docker", "volume", "create"):
            return SimpleNamespace(returncode=0, stdout="output-volume\n", stderr="")
        if call[1] == "create":
            return SimpleNamespace(returncode=0, stdout="container-123\n", stderr="")
        if call[1] == "cp" and call[2] == "container-123:/workspace/io/output/.":
            result_path.write_text('{"boxes": [], "annotated_image": "result.png"}')
            (output_dir / "result.png").write_bytes(b"png")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("visiox_api.services.pipeline_inference.subprocess.run", fake_run)
    request = PaddleXInferenceRuntimeRequest(
        image_digest="registry.example/paddlex@sha256:" + "a" * 64,
        workspace=workspace,
        model_dir=model_dir,
        image_path=image_path,
        output_dir=output_dir,
        result_path=result_path,
        environment="cpu",
    )

    DockerPaddleXInferenceRuntime().run(request)

    create = next(call for call in calls if call[1] == "create")
    assert create[:2] == ("docker", "create")
    assert "--read-only" in create
    assert "type=volume,source=output-volume,destination=/workspace/io" in create
    assert "-v" not in create
    assert all(str(tmp_path) not in part for part in create)
    assert (
        "docker",
        "cp",
        str(workspace / "paddlex_predict.py"),
        "container-123:/workspace/io/runner.py",
    ) in calls
    assert ("docker", "start", "--attach", "container-123") in calls
    assert ("docker", "rm", "--force", "--volumes", "container-123") in calls
    assert calls[-1] == ("docker", "volume", "rm", "--force", "output-volume")


def test_paddlex_inference_documents_static_bundle_runtime() -> None:
    readme = (
        __import__("pathlib")
        .Path("apps/paddlex-inference/README.md")
        .read_text(encoding="utf-8")
    )

    assert "exported static inference bundle" in readme
    assert "Paddle Inference" in readme
    assert "PaddleDetection" in readme


def test_paddlex_inference_dependency_lock_is_complete_and_pinned() -> None:
    lock_path = __import__("pathlib").Path("apps/paddlex-inference/requirements.lock")
    shared_lock_path = __import__("pathlib").Path(
        "packages/visiox-paddlex/requirements.runtime.lock"
    )
    assert shared_lock_path.is_file()
    requirements = {
        line.strip()
        for line in lock_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    shared_requirements = {
        line.strip()
        for line in shared_lock_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert "paddlex[cv]==3.0.3" in shared_requirements
    assert all(not item.startswith("paddlex") for item in requirements)
    assert any(item.startswith("fastapi==") for item in requirements)
    assert any(item.startswith("pillow==") for item in requirements)
    assert any(item.startswith("python-multipart==") for item in requirements)
    assert any(item.startswith("uvicorn[standard]==") for item in requirements)
    assert all("==" in item for item in requirements)


def test_paddlex_inference_installs_shared_runtime_before_app_dependencies() -> None:
    dockerfile = (
        __import__("pathlib")
        .Path("apps/paddlex-inference/Dockerfile")
        .read_text(encoding="utf-8")
    )
    shared_copy = (
        "COPY packages/visiox-paddlex/requirements.runtime.lock "
        "/tmp/paddlex-runtime-requirements.lock"
    )
    shared_install = (
        "pip install --no-cache-dir -r /tmp/paddlex-runtime-requirements.lock"
    )

    assert shared_copy in dockerfile
    assert shared_install in dockerfile
    assert dockerfile.index(shared_copy) < dockerfile.index(
        "COPY apps/paddlex-inference/requirements.lock"
    )


def test_production_model_bundle_path_must_be_absolute(tmp_path, monkeypatch) -> None:
    (tmp_path / "bundle").mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError, match="absolute"):
        InferenceConfig(production=True, model_dir="bundle")
