from hashlib import sha256
from pathlib import Path


class ChecksumMismatchError(ValueError):
    """Raised when an actual SHA-256 checksum differs from the expected value."""


def sha256_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(actual: str, expected: str | None) -> None:
    if expected is not None and actual.lower() != expected.lower():
        raise ChecksumMismatchError(f"checksum mismatch: expected={expected} actual={actual}")
