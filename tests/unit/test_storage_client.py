from __future__ import annotations

import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest

from visiox_storage.client import MinioObjectStorageClient


class FakeMinio:
    instances: list["FakeMinio"] = []

    def __init__(self, endpoint: str, *, access_key: str, secret_key: str, secure: bool) -> None:
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.secure = secure
        self.instances.append(self)

    def presigned_get_object(self, bucket: str, object_name: str, *, expires: timedelta) -> str:
        del expires
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}/{bucket}/{object_name}?signed=get"

    def presigned_put_object(self, bucket: str, object_name: str, *, expires: timedelta) -> str:
        del expires
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}/{bucket}/{object_name}?signed=put"


@pytest.fixture(autouse=True)
def fake_minio(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeMinio.instances.clear()
    monkeypatch.setitem(sys.modules, "minio", SimpleNamespace(Minio=FakeMinio))


def test_public_minio_origin_is_used_only_for_presigned_urls() -> None:
    storage = MinioObjectStorageClient(
        endpoint="minio:9000",
        access_key="access",
        secret_key="secret",
        public_url="http://10.10.40.2:9000",
    )

    url = storage.presigned_get_url(
        "minio://models/trained/model-1/best.pt",
        expires=timedelta(minutes=10),
    )

    assert [client.endpoint for client in FakeMinio.instances] == [
        "minio:9000",
        "10.10.40.2:9000",
    ]
    assert url.startswith("http://10.10.40.2:9000/models/")


@pytest.mark.parametrize(
    "public_url",
    [
        "10.10.40.2:9000",
        "ftp://10.10.40.2:9000",
        "http://user:password@10.10.40.2:9000",
        "http://10.10.40.2:9000/minio",
        "http://10.10.40.2:9000?token=secret",
    ],
)
def test_public_minio_origin_rejects_ambiguous_or_sensitive_urls(public_url: str) -> None:
    with pytest.raises(ValueError, match="public URL"):
        MinioObjectStorageClient(
            endpoint="minio:9000",
            access_key="access",
            secret_key="secret",
            public_url=public_url,
        )
