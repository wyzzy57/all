from importlib import resources
from pathlib import Path


TRAINING_REMOTE_SCRIPTS = frozenset(
    {"stage_training.sh", "launch_rank.sh", "stop_training.sh"}
)


_PACKAGED_SCRIPTS = frozenset(
    {
        "bootstrap_user.sh",
        "deploy_inference.sh",
        "inspect_deployment.sh",
        "inspect_runtime.sh",
        "launch_rank.sh",
        "probe_inventory.sh",
        "stage_training.sh",
        "stop_deployment.sh",
        "start_deployment.sh",
        "stop_training.sh",
    }
) | TRAINING_REMOTE_SCRIPTS


def load_packaged_script(name: str) -> bytes:
    if name not in _PACKAGED_SCRIPTS:
        raise ValueError("Unknown packaged edge script")
    # Prefer the checked-out remote scripts when the worker runs in the dev
    # compose setup, so mounted script fixes are effective without stale
    # package resources shadowing them.
    source_script = Path(__file__).parents[2] / "remote" / name
    if source_script.is_file():
        return source_script.read_bytes()
    packaged = resources.files("visiox_edge_executor_worker").joinpath("remote", name)
    if packaged.is_file():
        return packaged.read_bytes()
    return source_script.read_bytes()
