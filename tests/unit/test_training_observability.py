import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from visiox_training_worker import train_entrypoint
from visiox_training_worker.train_entrypoint import (
    capture_gradient_sample,
    log_epoch_observability,
    parse_overrides,
    should_capture_epoch,
    write_progress_snapshot,
)


class FakeTensor:
    def __init__(self, values: range | list[float]) -> None:
        self.values = list(values)
        self.detached = False

    def detach(self):
        detached = FakeTensor(self.values)
        detached.detached = True
        return detached

    def float(self):
        return self

    def flatten(self):
        return self

    def numel(self) -> int:
        return len(self.values)

    def __getitem__(self, item):
        sliced = FakeTensor(self.values[item])
        sliced.detached = self.detached
        return sliced

    def cpu(self):
        return self

    def clone(self):
        clone = FakeTensor(self.values)
        clone.detached = self.detached
        return clone


class FakeParameter:
    def __init__(self, values: range | list[float], *, grad: FakeTensor | None = None) -> None:
        self.values = FakeTensor(values)
        self.grad = grad
        self.requires_grad = True

    def detach(self):
        return self.values.detach()


class FakeModel:
    def __init__(self, parameters: dict[str, FakeParameter]) -> None:
        self.parameters = parameters

    def named_parameters(self):
        return self.parameters.items()


class FakeWriter:
    def __init__(self) -> None:
        self.histograms: list[tuple[str, FakeTensor, int]] = []
        self.scalars: list[tuple[str, float, int]] = []

    def add_histogram(self, tag: str, values: FakeTensor, step: int) -> None:
        self.histograms.append((tag, values, step))

    def add_scalar(self, tag: str, value: float, step: int) -> None:
        self.scalars.append((tag, value, step))


class FakeOptimizer:
    def __init__(self, model: FakeModel) -> None:
        self.model = model
        self.sample_present_before_clear: list[bool] = []

    def zero_grad(self) -> None:
        self.sample_present_before_clear.append(hasattr(self.trainer, "_visiox_gradient_samples"))
        for parameter in self.model.parameters.values():
            parameter.grad = None


def install_tensorboard_writer(monkeypatch: pytest.MonkeyPatch, writer: FakeWriter | None) -> None:
    ultralytics = ModuleType("ultralytics")
    utils = ModuleType("ultralytics.utils")
    callbacks = ModuleType("ultralytics.utils.callbacks")
    tensorboard = ModuleType("ultralytics.utils.callbacks.tensorboard")
    tensorboard.WRITER = writer
    callbacks.tensorboard = tensorboard
    utils.callbacks = callbacks
    ultralytics.utils = utils
    monkeypatch.setitem(sys.modules, "ultralytics", ultralytics)
    monkeypatch.setitem(sys.modules, "ultralytics.utils", utils)
    monkeypatch.setitem(sys.modules, "ultralytics.utils.callbacks", callbacks)
    monkeypatch.setitem(sys.modules, "ultralytics.utils.callbacks.tensorboard", tensorboard)


def install_fake_psutil(monkeypatch: pytest.MonkeyPatch) -> None:
    psutil = ModuleType("psutil")
    psutil.cpu_percent = lambda *, interval: 37.5
    psutil.virtual_memory = lambda: SimpleNamespace(percent=62.5)
    psutil.Process = lambda: SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=3 * 1024**3))
    monkeypatch.setitem(sys.modules, "psutil", psutil)


def test_parse_training_overrides_supports_ultralytics_types():
    assert parse_overrides(
        [
            "model=/models/best.pt",
            "epochs=40",
            "pretrained=True",
            "classes=[0,1]",
            "lr0=0.0001",
            "weight_decay=5e-04",
        ]
    ) == {
        "model": "/models/best.pt",
        "epochs": 40,
        "pretrained": True,
        "classes": [0, 1],
        "lr0": 0.0001,
        "weight_decay": 0.0005,
    }


def test_parse_training_overrides_rejects_non_key_value_arguments():
    with pytest.raises(ValueError, match="Invalid training argument"):
        parse_overrides(["train"])


@pytest.mark.parametrize(
    ("epoch", "expected"),
    [(1, True), (4, False), (5, True), (40, True)],
)
def test_should_capture_first_interval_and_final_epochs(epoch: int, expected: bool) -> None:
    assert should_capture_epoch(epoch, 40, 5) is expected


def test_write_progress_snapshot_is_atomic_and_contains_training_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(train_entrypoint.time, "time", lambda: 160.0)
    trainer = SimpleNamespace(
        save_dir=tmp_path,
        epoch=1,
        epochs=4,
        train_time_start=100.0,
        device="cuda:0",
        metrics={"metrics/mAP50(B)": 0.51, "ignored": object()},
    )

    path = write_progress_snapshot(trainer)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path == tmp_path / "visiox-progress.json"
    assert not path.with_suffix(".json.tmp").exists()
    assert payload["progress"] == {"current_epoch": 2, "total_epochs": 4, "percent": 50.0}
    assert payload["timing"]["elapsed_seconds"] == 60.0
    assert payload["timing"]["eta_seconds"] == 60.0
    assert payload["timing"]["updated_at"].endswith("+00:00")
    assert payload["environment"] == {"device": "cuda:0"}
    assert payload["latest_metrics"] == {"metrics/mAP50(B)": 0.51}
    assert payload["metric_samples"] == []


def test_supported_batch_callbacks_identify_last_batch_without_ultralytics_batch_i() -> None:
    gradient = FakeTensor(range(150_003))
    trainer = SimpleNamespace(
        epoch=4,
        epochs=40,
        train_loader=[object(), object(), object()],
        model=FakeModel({"layer.weight": FakeParameter([1.0], grad=gradient)}),
    )

    train_entrypoint.reset_training_batch_index(trainer)
    assert not hasattr(trainer, "batch_i")
    for _ in range(2):
        train_entrypoint.track_training_batch_start(trainer)
        capture_gradient_sample(trainer)
        assert not hasattr(trainer, "_visiox_gradient_samples")

    train_entrypoint.track_training_batch_start(trainer)
    capture_gradient_sample(trainer)

    sample = trainer._visiox_gradient_samples["layer.weight"]
    assert sample.detached is True
    assert sample is not gradient
    assert sample.numel() == 100_000


def test_pre_zero_bridge_captures_last_batch_before_clear_under_accumulation() -> None:
    parameter = FakeParameter([1.0], grad=FakeTensor([1.0]))
    model = FakeModel({"layer.weight": parameter})
    optimizer = FakeOptimizer(model)
    trainer = SimpleNamespace(
        epoch=4,
        epochs=40,
        train_loader=[object(), object(), object(), object()],
        model=model,
        optimizer=optimizer,
    )
    optimizer.trainer = trainer

    train_entrypoint.install_pre_zero_gradient_capture(trainer)
    train_entrypoint.reset_training_batch_index(trainer)
    train_entrypoint.track_training_batch_start(trainer)
    train_entrypoint.track_training_batch_start(trainer)
    optimizer.zero_grad()
    assert optimizer.sample_present_before_clear == [False]

    parameter.grad = FakeTensor(range(150_003))
    train_entrypoint.track_training_batch_start(trainer)
    train_entrypoint.track_training_batch_start(trainer)
    optimizer.zero_grad()

    assert optimizer.sample_present_before_clear == [False, True]
    assert parameter.grad is None
    assert trainer._visiox_gradient_samples["layer.weight"].numel() == 100_000


def test_pre_zero_bridge_skips_startup_clear_before_epoch_exists() -> None:
    parameter = FakeParameter([1.0], grad=FakeTensor([1.0]))
    model = FakeModel({"layer.weight": parameter})
    optimizer = FakeOptimizer(model)
    trainer = SimpleNamespace(
        epochs=40,
        train_loader=[object()],
        model=model,
        optimizer=optimizer,
    )
    optimizer.trainer = trainer
    train_entrypoint.install_pre_zero_gradient_capture(trainer)

    optimizer.zero_grad()

    assert optimizer.sample_present_before_clear == [False]
    assert parameter.grad is None
    assert not hasattr(trainer, "_visiox_gradient_samples")


def test_pre_zero_bridge_always_calls_original_zero_grad_when_capture_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(train_entrypoint.logger, "disabled", False)
    parameter = FakeParameter([1.0], grad=FakeTensor([1.0]))
    model = FakeModel({"layer.weight": parameter})
    optimizer = FakeOptimizer(model)
    trainer = SimpleNamespace(model=model, optimizer=optimizer)
    optimizer.trainer = trainer
    monkeypatch.setattr(
        train_entrypoint,
        "capture_gradient_sample",
        lambda _: (_ for _ in ()).throw(RuntimeError("tensor copy failed")),
    )
    train_entrypoint.install_pre_zero_gradient_capture(trainer)

    optimizer.zero_grad()

    assert optimizer.sample_present_before_clear == [False]
    assert parameter.grad is None
    assert "tensor copy failed" in caplog.text


def test_gradient_tensor_copy_failure_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(train_entrypoint.logger, "disabled", False)
    class ExplodingCopyTensor(FakeTensor):
        def detach(self):
            self.detached = True
            return self

        def clone(self):
            raise RuntimeError("copy unavailable")

    gradient = ExplodingCopyTensor([1.0])
    trainer = SimpleNamespace(
        epoch=0,
        epochs=1,
        train_loader=[object()],
        model=FakeModel({"layer.weight": FakeParameter([1.0], grad=gradient)}),
    )
    train_entrypoint.reset_training_batch_index(trainer)
    train_entrypoint.track_training_batch_start(trainer)

    capture_gradient_sample(trainer)

    assert not hasattr(trainer, "_visiox_gradient_samples")
    assert "copy unavailable" in caplog.text


def test_pre_zero_bridge_rewraps_replacement_optimizer_on_oom_retry() -> None:
    parameter = FakeParameter([1.0], grad=FakeTensor(range(150_003)))
    model = FakeModel({"layer.weight": parameter})
    initial_optimizer = FakeOptimizer(model)
    trainer = SimpleNamespace(
        epoch=4,
        epochs=40,
        train_loader=[object(), object()],
        model=model,
        optimizer=initial_optimizer,
    )
    initial_optimizer.trainer = trainer
    train_entrypoint.install_pre_zero_gradient_capture(trainer)

    replacement_optimizer = FakeOptimizer(model)
    replacement_optimizer.trainer = trainer
    trainer.optimizer = replacement_optimizer
    train_entrypoint.reset_training_batch_index(trainer)
    assert replacement_optimizer._visiox_zero_grad_wrapped is True
    wrapped_zero_grad = replacement_optimizer.zero_grad
    for _ in trainer.train_loader:
        train_entrypoint.track_training_batch_start(trainer)

    assert replacement_optimizer.zero_grad is wrapped_zero_grad
    replacement_optimizer.zero_grad()

    assert replacement_optimizer.sample_present_before_clear == [True]
    assert parameter.grad is None
    assert trainer._visiox_gradient_samples["layer.weight"].numel() == 100_000


def test_final_accumulated_batch_captures_at_batch_end_when_no_optimizer_step_occurs() -> None:
    parameter = FakeParameter([1.0], grad=FakeTensor([1.0]))
    trainer = SimpleNamespace(
        epoch=4,
        epochs=40,
        train_loader=[object(), object(), object()],
        model=FakeModel({"layer.weight": parameter}),
    )

    train_entrypoint.reset_training_batch_index(trainer)
    for _ in trainer.train_loader:
        train_entrypoint.track_training_batch_start(trainer)
    capture_gradient_sample(trainer)

    assert trainer._visiox_gradient_samples["layer.weight"].numel() == 1


def test_log_epoch_observability_writes_fixed_resources_and_sampled_histograms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = FakeWriter()
    install_tensorboard_writer(monkeypatch, writer)
    install_fake_psutil(monkeypatch)
    monkeypatch.setattr(train_entrypoint.time, "time", lambda: 130.0)
    trainer = SimpleNamespace(
        save_dir=tmp_path,
        epoch=4,
        epochs=40,
        train_time_start=100.0,
        epoch_time=None,
        epoch_time_start=128.0,
        train_loader=SimpleNamespace(dataset=list(range(16))),
        device="cpu",
        metrics={"train/box_loss": 0.9},
        model=FakeModel({"layer.weight": FakeParameter(range(150_003))}),
        _visiox_gradient_samples={"layer.weight": FakeTensor(range(150_003))},
    )

    log_epoch_observability(trainer)

    histogram_by_tag = {tag: values for tag, values, step in writer.histograms if step == 5}
    assert set(histogram_by_tag) == {"weights/layer.weight", "gradients/layer.weight"}
    assert all(values.numel() == 100_000 for values in histogram_by_tag.values())
    assert trainer._visiox_gradient_samples == {}

    scalar_by_tag = {tag: value for tag, value, step in writer.scalars if step == 5}
    assert scalar_by_tag == {
        "system.cpu_percent": 37.5,
        "system.memory_percent": 62.5,
        "system.memory_used_gb": 3.0,
        "train.images_per_second": 8.0,
    }
    payload = json.loads((tmp_path / "visiox-progress.json").read_text(encoding="utf-8"))
    assert payload["resources"] == [
        {
            "step": 5,
            "timestamp": 130.0,
            "system.cpu_percent": 37.5,
            "system.memory_percent": 62.5,
            "system.memory_used_gb": 3.0,
            "train.images_per_second": 8.0,
        }
    ]


def test_log_epoch_observability_adds_available_cuda_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = FakeWriter()
    install_tensorboard_writer(monkeypatch, writer)
    install_fake_psutil(monkeypatch)
    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(
        is_available=lambda: True,
        utilization=lambda: 73.0,
        memory_allocated=lambda: 2 * 1024**3,
        memory_reserved=lambda: 5 * 1024**3,
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    trainer = SimpleNamespace(
        save_dir=tmp_path,
        epoch=0,
        epochs=1,
        train_time_start=0.0,
        device="cuda:0",
        metrics={},
        model=FakeModel({}),
        train_loader=[],
    )

    log_epoch_observability(trainer)

    scalar_by_tag = {tag: value for tag, value, _ in writer.scalars}
    assert scalar_by_tag["system.gpu_utilization_percent"] == 73.0
    assert scalar_by_tag["system.gpu_memory_used_gb"] == 2.0
    assert scalar_by_tag["system.gpu_memory_reserved_gb"] == 5.0


def test_log_epoch_observability_omits_gpu_utilization_when_nvml_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = FakeWriter()
    install_tensorboard_writer(monkeypatch, writer)
    install_fake_psutil(monkeypatch)

    def missing_nvml() -> float:
        raise ModuleNotFoundError("No module named 'pynvml'")

    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(
        is_available=lambda: True,
        utilization=missing_nvml,
        memory_allocated=lambda: 2 * 1024**3,
        memory_reserved=lambda: 5 * 1024**3,
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    trainer = SimpleNamespace(
        save_dir=tmp_path,
        epoch=0,
        epochs=1,
        train_time_start=0.0,
        device="cuda:0",
        metrics={},
        model=FakeModel({}),
        train_loader=[],
    )

    log_epoch_observability(trainer)

    scalar_by_tag = {tag: value for tag, value, _ in writer.scalars}
    assert "system.gpu_utilization_percent" not in scalar_by_tag
    assert scalar_by_tag["system.gpu_memory_used_gb"] == 2.0
    assert scalar_by_tag["system.gpu_memory_reserved_gb"] == 5.0
    assert (tmp_path / "visiox-progress.json").exists()


def _failure_injection_trainer(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        save_dir=tmp_path,
        epoch=0,
        epochs=1,
        train_time_start=0.0,
        device="cpu",
        metrics={},
        model=FakeModel({"layer.weight": FakeParameter([1.0])}),
        train_loader=[],
        _visiox_gradient_samples={"layer.weight": FakeTensor([1.0])},
    )


def test_resource_collection_failure_does_not_abort_epoch_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(train_entrypoint.logger, "disabled", False)
    install_tensorboard_writer(monkeypatch, None)
    monkeypatch.setattr(
        train_entrypoint,
        "_collect_resource_metrics",
        lambda _: (_ for _ in ()).throw(RuntimeError("resource reader failed")),
    )
    trainer = _failure_injection_trainer(tmp_path)

    log_epoch_observability(trainer)

    assert trainer._visiox_gradient_samples == {}
    assert (tmp_path / "visiox-progress.json").exists()
    assert "resource reader failed" in caplog.text


def test_tensorboard_writer_failure_does_not_abort_epoch_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(train_entrypoint.logger, "disabled", False)
    install_fake_psutil(monkeypatch)
    writer = FakeWriter()
    monkeypatch.setattr(
        writer,
        "add_scalar",
        lambda *_: (_ for _ in ()).throw(RuntimeError("writer failed")),
    )
    install_tensorboard_writer(monkeypatch, writer)
    trainer = _failure_injection_trainer(tmp_path)

    log_epoch_observability(trainer)

    assert trainer._visiox_gradient_samples == {}
    assert (tmp_path / "visiox-progress.json").exists()
    assert "writer failed" in caplog.text


def test_snapshot_failure_does_not_abort_epoch_or_final_callbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(train_entrypoint.logger, "disabled", False)
    install_fake_psutil(monkeypatch)
    install_tensorboard_writer(monkeypatch, None)
    monkeypatch.setattr(
        train_entrypoint,
        "write_progress_snapshot",
        lambda _: (_ for _ in ()).throw(OSError("snapshot read-only")),
    )
    trainer = _failure_injection_trainer(tmp_path)

    log_epoch_observability(trainer)
    result = train_entrypoint.write_final_progress_snapshot(trainer)

    assert result is None
    assert trainer._visiox_gradient_samples == {}
    assert "snapshot read-only" in caplog.text


def test_main_registers_native_callbacks_and_keeps_native_integrations_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback_names: dict[str, object] = {}
    train_calls: list[dict[str, object]] = []
    setting_updates: list[dict[str, bool]] = []

    class FakeYolo:
        def __init__(self, model_path: str, *, task: str | None) -> None:
            assert model_path == "model.pt"
            assert task == "detect"

        def add_callback(self, name: str, callback: object) -> None:
            callback_names[name] = callback

        def train(self, **overrides: object) -> None:
            train_calls.append(overrides)

    ultralytics = ModuleType("ultralytics")
    ultralytics.YOLO = FakeYolo
    ultralytics.settings = SimpleNamespace(update=setting_updates.append)
    monkeypatch.setitem(sys.modules, "ultralytics", ultralytics)
    monkeypatch.setattr(sys, "argv", ["train_entrypoint.py", "model=model.pt", "task=detect", "epochs=40"])

    train_entrypoint.main()

    assert callback_names == {
        "on_pretrain_routine_end": train_entrypoint.install_pre_zero_gradient_capture,
        "on_train_epoch_start": train_entrypoint.reset_training_batch_index,
        "on_train_batch_start": train_entrypoint.track_training_batch_start,
        "on_before_zero_grad": capture_gradient_sample,
        "on_train_batch_end": capture_gradient_sample,
        "on_train_epoch_end": log_epoch_observability,
        "on_train_end": train_entrypoint.write_final_progress_snapshot,
    }
    assert setting_updates == [{"mlflow": True, "tensorboard": True}]
    assert train_calls == [{"epochs": 40}]


def test_distributed_process_group_uses_nccl_and_local_rank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    distributed = SimpleNamespace(
        is_initialized=lambda: False,
        init_process_group=lambda **kwargs: calls.append(("init", kwargs)),
    )
    cuda = SimpleNamespace(
        is_available=lambda: True,
        set_device=lambda rank: calls.append(("device", rank)),
    )
    torch = ModuleType("torch")
    torch.cuda = cuda
    torch.distributed = distributed
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "torch.distributed", distributed)
    monkeypatch.setenv("RANK", "2")
    monkeypatch.setenv("LOCAL_RANK", "1")

    assert train_entrypoint.initialize_distributed_process_group() is True
    assert calls == [
        ("device", 1),
        ("init", {"backend": "nccl", "init_method": "env://"}),
    ]
