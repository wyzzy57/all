from pathlib import Path
from typing import Protocol


class ObjectStorageClient(Protocol):
    def put_file(
        self,
        bucket: str,
        object_name: str,
        path: Path,
        content_type: str | None = None,
    ) -> str: ...

    def get_file(self, bucket: str, object_name: str, destination: Path) -> Path: ...

    def delete_file(self, bucket: str, object_name: str) -> None: ...


class MinioObjectStorageClient:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool = False,
    ) -> None:
        from minio import Minio

        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    def put_file(
        self,
        bucket: str,
        object_name: str,
        path: Path,
        content_type: str | None = None,
    ) -> str:
        if not self._client.bucket_exists(bucket):
            try:
                self._client.make_bucket(bucket)
            except Exception as exc:
                if not _is_bucket_exists_race(exc):
                    raise
        self._client.fput_object(bucket, object_name, str(path), content_type=content_type)
        return f"minio://{bucket}/{object_name}"

    def get_file(self, bucket: str, object_name: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._client.fget_object(bucket, object_name, str(destination))
        return destination

    def delete_file(self, bucket: str, object_name: str) -> None:
        try:
            self._client.remove_object(bucket, object_name)
        except Exception as exc:
            if not _is_object_missing(exc):
                raise


class InMemoryObjectStorageClient:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_file(
        self,
        bucket: str,
        object_name: str,
        path: Path,
        content_type: str | None = None,
    ) -> str:
        del content_type
        self.objects[(bucket, object_name)] = path.read_bytes()
        return f"memory://{bucket}/{object_name}"

    def get_file(self, bucket: str, object_name: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.objects[(bucket, object_name)])
        return destination

    def delete_file(self, bucket: str, object_name: str) -> None:
        self.objects.pop((bucket, object_name), None)


def _is_bucket_exists_race(exc: Exception) -> bool:
    try:
        from minio.error import S3Error
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, S3Error) and exc.code in {
        "BucketAlreadyOwnedByYou",
        "BucketAlreadyExists",
    }


def _is_object_missing(exc: Exception) -> bool:
    try:
        from minio.error import S3Error
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, S3Error) and exc.code in {"NoSuchKey", "NoSuchObject"}
