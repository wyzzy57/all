from datetime import timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit


class ObjectStorageClient(Protocol):
    def put_file(
        self,
        bucket: str,
        object_name: str,
        path: Path,
        content_type: str | None = None,
    ) -> str: ...

    def get_file(self, bucket: str, object_name: str, destination: Path) -> Path: ...

    def object_size(self, bucket: str, object_name: str) -> int: ...

    def delete_file(self, bucket: str, object_name: str) -> None: ...

    def presigned_get_url(self, uri: str, *, expires: timedelta) -> str: ...

    def presigned_put_url(self, uri: str, *, expires: timedelta) -> str: ...


class MinioObjectStorageClient:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool = False,
        public_url: str | None = None,
    ) -> None:
        from minio import Minio

        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self._presign_client = self._client
        if public_url is not None:
            parsed = urlsplit(public_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("MinIO public URL must contain only an HTTP(S) origin")
            self._presign_client = Minio(
                parsed.netloc,
                access_key=access_key,
                secret_key=secret_key,
                secure=parsed.scheme == "https",
            )

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

    def object_size(self, bucket: str, object_name: str) -> int:
        return int(self._client.stat_object(bucket, object_name).size)

    def delete_file(self, bucket: str, object_name: str) -> None:
        try:
            self._client.remove_object(bucket, object_name)
        except Exception as exc:
            if not _is_object_missing(exc):
                raise

    def presigned_get_url(self, uri: str, *, expires: timedelta) -> str:
        bucket, object_name = _parse_minio_uri(uri)
        if not timedelta(0) < expires <= timedelta(hours=1):
            raise ValueError("presigned URL expiry must be between zero and one hour")
        return str(
            self._presign_client.presigned_get_object(
                bucket,
                object_name,
                expires=expires,
            )
        )

    def presigned_put_url(self, uri: str, *, expires: timedelta) -> str:
        bucket, object_name = _parse_minio_uri(uri)
        if not timedelta(0) < expires <= timedelta(hours=1):
            raise ValueError("presigned URL expiry must be between zero and one hour")
        return str(
            self._presign_client.presigned_put_object(
                bucket,
                object_name,
                expires=expires,
            )
        )


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

    def object_size(self, bucket: str, object_name: str) -> int:
        return len(self.objects[(bucket, object_name)])

    def delete_file(self, bucket: str, object_name: str) -> None:
        self.objects.pop((bucket, object_name), None)

    def presigned_get_url(self, uri: str, *, expires: timedelta) -> str:
        del uri, expires
        raise ValueError("in-memory objects cannot be presigned")

    def presigned_put_url(self, uri: str, *, expires: timedelta) -> str:
        del uri, expires
        raise ValueError("in-memory objects cannot be presigned")


def _parse_minio_uri(uri: str) -> tuple[str, str]:
    remainder = uri.removeprefix("minio://")
    if (
        remainder == uri
        or "/" not in remainder
        or "?" in remainder
        or "#" in remainder
    ):
        raise ValueError("object URI must be a durable MinIO URI")
    bucket, object_name = remainder.split("/", 1)
    if not bucket or not object_name:
        raise ValueError("object URI must be a durable MinIO URI")
    return bucket, object_name


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
