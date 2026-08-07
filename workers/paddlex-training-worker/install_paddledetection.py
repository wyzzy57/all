from __future__ import annotations

from importlib.metadata import distribution
from importlib.util import find_spec
from pathlib import Path


def _defer_driver_bound_custom_ops(repo_name: str, repo_root: str) -> None:
    if repo_name != "PaddleDetection":
        raise RuntimeError(f"unexpected PaddleX repository: {repo_name}")
    if not (Path(repo_root) / "ppdet").is_dir():
        raise RuntimeError("PaddleDetection source is incomplete")


def main() -> None:
    from paddlex.repo_manager import core, repo

    # Rotated-detection custom ops require the runtime-injected NVIDIA driver.
    repo.install_external_deps = _defer_driver_bound_custom_ops
    core.setup(repo_names=["PaddleDetection"])

    distribution("paddledet")
    spec = find_spec("ppdet")
    if spec is None or spec.submodule_search_locations is None:
        raise RuntimeError("PaddleDetection module is unavailable")
    repo_root = Path(next(iter(spec.submodule_search_locations))).parent
    if not (repo_root / ".installed").is_file():
        raise RuntimeError("PaddleDetection installation marker is missing")


if __name__ == "__main__":
    main()
