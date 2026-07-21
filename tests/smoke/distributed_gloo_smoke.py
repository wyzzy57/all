from __future__ import annotations

import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def _worker(phase: str, output: Path, inject_failure: bool) -> int:
    import torch
    import torch.distributed as dist

    rank = int(os.environ["RANK"])
    output.mkdir(parents=True, exist_ok=True)
    dist.init_process_group("gloo", timeout=timedelta(seconds=15))
    value = torch.tensor([float(rank + 1)])
    dist.all_reduce(value)
    (output / f"{phase}-rank-{rank}.json").write_text(
        json.dumps(
            {"rank": rank, "phase": phase, "all_reduce": value.item()},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    if inject_failure and rank == 1:
        os._exit(23)
    if inject_failure and rank == 0:
        # The parent must stop this peer after rank 1 fails. Waiting here keeps
        # that behavior deterministic instead of relying on backend timing.
        time.sleep(30)
        return 24
    if rank == 0:
        torch.save(
            {"phase": phase, "world_size": dist.get_world_size(), "sum": value.item()},
            output / "checkpoint.pt",
        )
    dist.barrier()
    dist.destroy_process_group()
    return 0


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _write_torchrun_compatibility_shim(output: Path) -> Path:
    shim_root = output / "torchrun-shim"
    shim_root.mkdir(parents=True, exist_ok=True)
    (shim_root / "sitecustomize.py").write_text(
        """\
import torch.distributed as _dist
import importlib as _importlib

_original_tcp_store = _dist.TCPStore

def _tcp_store_without_libuv(*args, **kwargs):
    kwargs.setdefault("use_libuv", False)
    return _original_tcp_store(*args, **kwargs)

_dist.TCPStore = _tcp_store_without_libuv
for _module_name in (
    "torch.distributed.rendezvous",
    "torch.distributed.elastic.rendezvous.c10d_rendezvous_backend",
    "torch.distributed.elastic.rendezvous.static_tcp_rendezvous",
):
    _module = _importlib.import_module(_module_name)
    _module.TCPStore = _tcp_store_without_libuv
""",
        encoding="utf-8",
    )
    return shim_root


def _rank_command(
    *, rank: int, port: int, phase: str, output: Path, inject_failure: bool
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "torch.distributed.run",
        "--nnodes=2",
        "--nproc-per-node=1",
        f"--node-rank={rank}",
        "--master-addr=127.0.0.1",
        f"--master-port={port}",
        str(Path(__file__).resolve()),
        "--worker",
        "--phase",
        phase,
        "--output",
        str(output),
    ]
    if inject_failure:
        command.append("--inject-failure")
    return command


def _run_phase(
    *, phase: str, output: Path, inject_failure: bool, timeout_seconds: float = 45
) -> dict[str, object]:
    port = _free_port()
    shim_root = _write_torchrun_compatibility_shim(output)
    processes: list[subprocess.Popen[str]] = []
    streams = []
    try:
        for rank in (0, 1):
            stream = (output / f"{phase}-launcher-{rank}.log").open(
                "w", encoding="utf-8"
            )
            streams.append(stream)
            python_path = os.environ.get("PYTHONPATH")
            process = subprocess.Popen(
                _rank_command(
                    rank=rank,
                    port=port,
                    phase=phase,
                    output=output,
                    inject_failure=inject_failure,
                ),
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
                env={
                    **os.environ,
                    "PYTHONUNBUFFERED": "1",
                    # Some supported Windows CPU wheels are built without
                    # libuv. The classic TCPStore remains a real networked
                    # rendezvous and is portable across those wheels.
                    "USE_LIBUV": "0",
                    "PYTHONPATH": str(shim_root)
                    + (os.pathsep + python_path if python_path else ""),
                },
            )
            processes.append(process)

        deadline = time.monotonic() + timeout_seconds
        peer_stop_observed = False
        while time.monotonic() < deadline:
            return_codes = [process.poll() for process in processes]
            if inject_failure:
                failed = [code for code in return_codes if code not in (None, 0)]
                if failed:
                    for process in processes:
                        if process.poll() is None:
                            process.terminate()
                            peer_stop_observed = True
                    break
            elif all(code is not None for code in return_codes):
                break
            time.sleep(0.1)
        else:
            for process in processes:
                if process.poll() is None:
                    process.kill()
            raise TimeoutError(f"{phase} phase timed out")

        return_codes = []
        for process in processes:
            try:
                return_codes.append(process.wait(timeout=10))
            except subprocess.TimeoutExpired:
                process.kill()
                return_codes.append(process.wait(timeout=5))
        return {
            "return_codes": return_codes,
            "peer_stop_observed": peer_stop_observed,
        }
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        for stream in streams:
            stream.close()


def _orchestrate(output: Path) -> int:
    try:
        import torch
        import torch.distributed as dist
    except ImportError:
        print(
            json.dumps(
                {
                    "schema": "visiox.distributed-gloo-smoke.v1",
                    "status": "PENDING",
                    "reason": "PyTorch is not installed",
                },
                sort_keys=True,
            )
        )
        return 2
    if not dist.is_available() or not dist.is_gloo_available():
        print(
            json.dumps(
                {
                    "schema": "visiox.distributed-gloo-smoke.v1",
                    "status": "PENDING",
                    "reason": "PyTorch Gloo backend is unavailable",
                    "torch_version": torch.__version__,
                },
                sort_keys=True,
            )
        )
        return 2

    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    try:
        failure = _run_phase(
            phase="failure", output=output, inject_failure=True
        )
        failure_was_injected = any(
            code not in (0, None) for code in failure["return_codes"]
        )
        peer_stop_observed = bool(failure["peer_stop_observed"])
        recovery = _run_phase(
            phase="recovery", output=output, inject_failure=False
        )
        recovery_succeeded = recovery["return_codes"] == [0, 0]
        rank_records = (
            [
                json.loads(
                    (output / f"recovery-rank-{rank}.json").read_text(
                        encoding="utf-8"
                    )
                )
                for rank in (0, 1)
            ]
            if recovery_succeeded
            else []
        )
        all_reduce_succeeded = recovery_succeeded and all(
            record["all_reduce"] == 3.0 for record in rank_records
        )
        checkpoint_created = (output / "checkpoint.pt").is_file()
        passed = all(
            (
                failure_was_injected,
                peer_stop_observed,
                recovery_succeeded,
                all_reduce_succeeded,
                checkpoint_created,
            )
        )
        payload = {
            "schema": "visiox.distributed-gloo-smoke.v1",
            "status": "PASS" if passed else "FAIL",
            "failure_was_injected": failure_was_injected,
            "peer_stop_observed": peer_stop_observed,
            "recovery_succeeded": recovery_succeeded,
            "all_reduce_sum": 3.0 if all_reduce_succeeded else None,
            "checkpoint_created": checkpoint_created,
            "output": str(output.resolve()),
        }
        print(json.dumps(payload, sort_keys=True))
        return 0 if passed else 1
    except Exception as error:
        print(
            json.dumps(
                {
                    "schema": "visiox.distributed-gloo-smoke.v1",
                    "status": "FAIL",
                    "reason": str(error),
                    "output": str(output.resolve()),
                },
                sort_keys=True,
            )
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--phase", default="recovery")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inject-failure", action="store_true")
    arguments = parser.parse_args()
    if arguments.worker:
        return _worker(arguments.phase, arguments.output, arguments.inject_failure)
    return _orchestrate(arguments.output)


if __name__ == "__main__":
    raise SystemExit(main())
