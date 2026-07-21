#!/usr/bin/env bash
set -euo pipefail

exec python3 - "$@" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile


IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
IMAGE_DIGEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,430}@sha256:[a-f0-9]{64}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
ARTIFACT_NAMES = {"model", "dataset", "checkpoint"}


def invalid():
    raise ValueError("invalid training staging request")


def valid_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password and not parsed.fragment


def validate(request):
    if not isinstance(request, dict) or set(request) != {"run_id", "attempt", "image_digest", "artifacts"}:
        invalid()
    if not isinstance(request["run_id"], str) or not IDENTIFIER.fullmatch(request["run_id"]):
        invalid()
    if not isinstance(request["attempt"], int) or isinstance(request["attempt"], bool) or request["attempt"] < 1:
        invalid()
    if not isinstance(request["image_digest"], str) or not IMAGE_DIGEST.fullmatch(request["image_digest"]):
        invalid()
    artifacts = request["artifacts"]
    if not isinstance(artifacts, list) or not 2 <= len(artifacts) <= 3:
        invalid()
    names = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"name", "download_url", "checksum", "filename"}:
            invalid()
        name = artifact["name"]
        filename = artifact["filename"]
        if name not in ARTIFACT_NAMES or name in names or not valid_url(artifact["download_url"]):
            invalid()
        if not isinstance(artifact["checksum"], str) or not SHA256.fullmatch(artifact["checksum"]):
            invalid()
        if not isinstance(filename, str) or Path(filename).name != filename or not IDENTIFIER.fullmatch(filename):
            invalid()
        names.add(name)
    if not {"model", "dataset"}.issubset(names):
        invalid()
    return request


def download(url, destination, checksum):
    temporary = destination.with_suffix(destination.suffix + ".partial")
    digest = hashlib.sha256()
    request = Request(url, headers={"User-Agent": "visiox-edge-executor/1"})
    with urlopen(request, timeout=300) as response, temporary.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != checksum:
        temporary.unlink(missing_ok=True)
        raise ValueError("training artifact checksum mismatch")
    os.replace(temporary, destination)


def safe_target(root, member):
    candidate = (root / member).resolve()
    if root.resolve() not in candidate.parents and candidate != root.resolve():
        raise ValueError("unsafe dataset archive member")
    return candidate


def unpack_dataset(archive, destination):
    destination.mkdir(mode=0o750, parents=True, exist_ok=True)
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive, "r:*") as source:
            for member in source.getmembers():
                safe_target(destination, member.name)
                if member.issym() or member.islnk() or member.isdev():
                    raise ValueError("unsafe dataset archive member")
            source.extractall(destination, filter="data")
        return
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                safe_target(destination, member.filename)
            source.extractall(destination)
        return
    raise ValueError("dataset artifact must be a tar or zip archive")


def main():
    if len(sys.argv) != 2:
        return 2
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as source:
            request = validate(json.load(source))
        subprocess.run(["docker", "pull", request["image_digest"]], check=True, capture_output=True, text=True, timeout=1800)
        root = Path("/var/lib/visiox/training") / request["run_id"] / str(request["attempt"])
        artifacts_dir = root / "artifacts"
        output_dir = root / "output"
        artifacts_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
        output_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
        paths = {}
        for artifact in request["artifacts"]:
            destination = artifacts_dir / artifact["filename"]
            download(artifact["download_url"], destination, artifact["checksum"])
            paths[artifact["name"]] = str(destination)
        dataset_dir = root / "dataset"
        unpack_dataset(Path(paths["dataset"]), dataset_dir)
        paths["dataset"] = str(dataset_dir)
        paths["output"] = str(output_dir)
        print(json.dumps({"root": str(root), "paths": paths}, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
        return 0
    except Exception:
        print("training artifact staging failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
PY
