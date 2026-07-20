from importlib import resources
from pathlib import Path


_PACKAGED_SCRIPTS = frozenset({"bootstrap_user.sh", "probe_inventory.sh"})


def load_packaged_script(name: str) -> bytes:
    if name not in _PACKAGED_SCRIPTS:
        raise ValueError("Unknown packaged edge script")
    packaged = (
        resources.files("visiox_edge_executor_worker")
        .joinpath("remote", name)
    )
    if packaged.is_file():
        return packaged.read_bytes()
    source_script = Path(__file__).parents[2] / "remote" / name
    return source_script.read_bytes()
