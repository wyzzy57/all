#!/bin/bash
set -eu

export LC_ALL=C

exec python3 - <<'PY'
import json
import os
from pathlib import Path
import re
import subprocess


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def run(arguments):
    try:
        result = subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def os_release():
    values = {}
    for line in (read_text("/etc/os-release") or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.lower()] = value.strip().strip('"')
    return {
        "id": values.get("id"),
        "version_id": values.get("version_id"),
        "pretty_name": values.get("pretty_name"),
    }


def memory_total_kib():
    for line in (read_text("/proc/meminfo") or "").splitlines():
        match = re.fullmatch(r"MemTotal:\s+(\d+)\s+kB", line)
        if match:
            return int(match.group(1))
    return None


def docker_inventory():
    version = run(["docker", "version", "--format", "{{.Server.Version}}"])
    runtime_json = run(["docker", "info", "--format", "{{json .Runtimes}}"])
    default_runtime = run(["docker", "info", "--format", "{{.DefaultRuntime}}"])
    runtimes = []
    if runtime_json:
        try:
            decoded = json.loads(runtime_json)
            if isinstance(decoded, dict):
                runtimes = sorted(str(name) for name in decoded)
        except json.JSONDecodeError:
            pass
    return {
        "available": version is not None,
        "version": version,
        "runtimes": runtimes,
        "default_runtime": default_runtime,
    }


def nvidia_inventory():
    summary = run(["nvidia-smi"])
    query = run([
        "nvidia-smi",
        "--query-gpu=name,uuid,memory.total,compute_cap,driver_version",
        "--format=csv,noheader,nounits",
    ])
    gpus = []
    driver_version = None
    for line in (query or "").splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 5:
            continue
        try:
            memory_mib = int(fields[2])
        except ValueError:
            memory_mib = None
        driver_version = driver_version or fields[4]
        gpus.append({
            "name": fields[0],
            "uuid": fields[1],
            "memory_total_mib": memory_mib,
            "compute_capability": fields[3],
        })
    cuda_match = re.search(r"CUDA Version:\s*([0-9.]+)", summary or "")
    return {
        "driver_version": driver_version,
        "cuda_version": cuda_match.group(1) if cuda_match else None,
        "gpus": gpus,
    }


def package_versions():
    packages = {}
    names = (
        "cuda-cudart-*",
        "cuda-toolkit-*",
        "libcudnn*",
        "libnvinfer*",
        "nvidia-jetpack",
        "nvidia-l4t-core",
        "tensorrt*",
    )
    for name in names:
        output = run(["dpkg-query", "-W", "-f=${Package}\t${Version}\n", name])
        for line in (output or "").splitlines():
            fields = line.split("\t", 1)
            if len(fields) == 2:
                packages[fields[0]] = fields[1]
    return packages


def device_tree_value(path):
    value = read_text(path)
    return value.replace("\x00", ",").strip(",") if value else None


inventory = {
    "os_release": os_release(),
    "uname": {
        "architecture": run(["uname", "-m"]),
        "kernel_release": run(["uname", "-r"]),
    },
    "cpu": {"logical_cores": os.cpu_count()},
    "memory": {"total_kib": memory_total_kib()},
    "docker": docker_inventory(),
    "nvidia": nvidia_inventory(),
    "jetson": {
        "model": device_tree_value("/proc/device-tree/model"),
        "compatible": device_tree_value("/proc/device-tree/compatible"),
        "nv_tegra_release": read_text("/etc/nv_tegra_release"),
        "packages": package_versions(),
    },
}
print(json.dumps(inventory, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
PY
