from __future__ import annotations

import json
from types import SimpleNamespace

import visiox_llm_training_worker.resources as resources


def test_resource_sample_keeps_each_gpu_as_a_separate_uuid_series(monkeypatch) -> None:
    monkeypatch.setattr(resources.psutil, "cpu_percent", lambda interval=None: 25.0)
    monkeypatch.setattr(resources.psutil, "virtual_memory", lambda: SimpleNamespace(percent=50.0))
    monkeypatch.setattr(resources.psutil, "disk_usage", lambda _: SimpleNamespace(percent=30.0))
    monkeypatch.setattr(
        resources.psutil,
        "net_io_counters",
        lambda: SimpleNamespace(bytes_sent=100, bytes_recv=200),
    )
    monkeypatch.setattr(
        resources.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="0, GPU-a, 80, 1024, 8192, 65, 120\n1, GPU-b, 40, 2048, 8192, 55, 90\n"
        ),
    )

    sample = resources.collect_resource_sample(step=7)

    assert sample["step"] == 7
    assert [gpu["uuid"] for gpu in sample["gpus"]] == ["GPU-a", "GPU-b"]
    assert sample["gpus"][0]["utilization_percent"] == 80
    assert sample["gpus"][1]["memory_used_mb"] == 2048


def test_resource_sampler_writes_jsonl_and_stops(tmp_path) -> None:
    sampler = resources.ResourceSampler(
        tmp_path,
        interval_seconds=0.01,
        sample_factory=lambda *, step: {"step": step, "timestamp": 1.0, "gpus": []},
    )

    sampler.start()
    sampler.stop()

    rows = [json.loads(line) for line in sampler.path.read_text(encoding="utf-8").splitlines()]
    assert rows
    assert rows[0]["gpus"] == []
