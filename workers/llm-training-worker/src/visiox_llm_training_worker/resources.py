from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
from threading import Event, Thread
from typing import Any, Callable

import psutil


_GPU_QUERY = (
    "index,uuid,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"
)


def collect_resource_sample(*, step: int = 0) -> dict[str, Any]:
    disk = psutil.disk_usage("/")
    network = psutil.net_io_counters()
    sample: dict[str, Any] = {
        "step": step,
        "timestamp": datetime.now(UTC).timestamp(),
        "system.cpu_percent": float(psutil.cpu_percent(interval=None)),
        "system.memory_percent": float(psutil.virtual_memory().percent),
        "system.disk_percent": float(disk.percent),
        "system.network_bytes_sent": float(network.bytes_sent),
        "system.network_bytes_received": float(network.bytes_recv),
        "gpus": [],
    }
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--query-gpu={_GPU_QUERY}", "--format=csv,noheader,nounits"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        sample["gpus"] = [_parse_gpu_row(line) for line in result.stdout.splitlines() if line.strip()]
    except (OSError, ValueError, subprocess.SubprocessError):
        sample["gpus"] = []
    return sample


class ResourceSampler:
    def __init__(
        self,
        output_dir: Path,
        *,
        interval_seconds: float = 5,
        sample_factory: Callable[..., dict[str, Any]] = collect_resource_sample,
    ) -> None:
        self.path = output_dir / "resource_metrics.jsonl"
        self.interval_seconds = interval_seconds
        self.sample_factory = sample_factory
        self._stop = Event()
        self._thread: Thread | None = None
        self.latest: dict[str, Any] = {}
        self.step = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = Thread(target=self._run, name="visiox-resource-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds + 1))

    def _run(self) -> None:
        while not self._stop.is_set():
            sample = self.sample_factory(step=self.step)
            self.latest = sample
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(sample, ensure_ascii=True, separators=(",", ":")) + "\n")
            self._stop.wait(self.interval_seconds)


def _parse_gpu_row(line: str) -> dict[str, Any]:
    values = [item.strip() for item in line.split(",")]
    if len(values) != 7:
        raise ValueError("nvidia-smi returned an invalid row")
    index, uuid, utilization, used, total, temperature, power = values
    return {
        "index": int(index),
        "uuid": uuid,
        "utilization_percent": float(utilization),
        "memory_used_mb": float(used),
        "memory_total_mb": float(total),
        "temperature_celsius": float(temperature),
        "power_watts": float(power),
    }
