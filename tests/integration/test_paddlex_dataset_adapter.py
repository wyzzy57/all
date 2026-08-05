from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from visiox_db.models import (
    Annotation,
    Dataset,
    DatasetSample,
    DatasetVersion,
    Organization,
    User,
)
from visiox_paddlex.config import (
    PaddleXDetectionConfigParameters,
    build_paddlex_detection_config_overrides,
)
from visiox_paddlex.datasets import (
    PaddleXDatasetError,
    compute_paddlex_detection_source_revision,
    export_paddlex_detection_dataset,
)
from visiox_storage.client import InMemoryObjectStorageClient


DATASET_ID = "paddlex-dataset"
DATASET_VERSION_ID = "paddlex-version-1"
ORGANIZATION_ID = "paddlex-org"
OWNER_USER_ID = "paddlex-owner"
IMAGE_PAYLOADS = {
    "sample-train-multi": b"train-multi-image",
    "sample-train-empty": b"train-empty-image",
    "sample-val": b"val-image",
    "sample-test": b"test-image",
}
SPLITS = {
    "sample-train-multi": "train",
    "sample-train-empty": "train",
    "sample-val": "val",
    "sample-test": "test",
}


@pytest.fixture()
def session_factory(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'paddlex-adapter.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        session.add(
            Organization(
                id=ORGANIZATION_ID,
                name="PaddleX Adapter Tests",
                slug="paddlex-adapter-tests",
                status="active",
            )
        )
        session.add(
            User(
                id=OWNER_USER_ID,
                organization_id=ORGANIZATION_ID,
                username="paddlex-owner",
                display_name="PaddleX Owner",
                email="paddlex@example.test",
                password_hash="test-only",
                role="admin",
                status="active",
                must_change_password=False,
            )
        )
        dataset = Dataset(
            id=DATASET_ID,
            name="paddlex-detection",
            organization_id=ORGANIZATION_ID,
            owner_user_id=OWNER_USER_ID,
            task="detect",
            status="validated",
            format="label_studio",
            class_schema={"names": ["正常", "瑕疵"]},
            sample_count=4,
            annotation_count=4,
            source="label_studio",
        )
        session.add(dataset)
        session.flush()
        samples: dict[str, DatasetSample] = {}
        for sample_id, payload in IMAGE_PAYLOADS.items():
            sample = DatasetSample(
                id=sample_id,
                dataset_id=DATASET_ID,
                file_uri=f"memory://datasets/paddlex/{sample_id}.jpg",
                width=100,
                height=80,
                checksum=hashlib.sha256(payload).hexdigest(),
                split=SPLITS[sample_id],
                annotation_status="labeled",
            )
            session.add(sample)
            samples[sample_id] = sample
        session.flush()
        results = {
            "sample-train-multi": [
                _box("正常", x=0, y=0, width=10, height=20, result_id="box-1"),
                _box("瑕疵", x=50, y=25, width=25, height=50, result_id="box-2"),
            ],
            "sample-train-empty": [],
            "sample-val": [
                _box("瑕疵", x=10, y=10, width=30, height=40, result_id="box-3")
            ],
            "sample-test": [
                _box("正常", x=20, y=30, width=15, height=25, result_id="box-4")
            ],
        }
        annotations: list[Annotation] = []
        for sample_id, sample_results in results.items():
            annotation = Annotation(
                id=f"annotation-{sample_id}",
                dataset_sample_id=samples[sample_id].id,
                source="label_studio",
                internal_payload={
                    "annotations": [
                        {
                            "source_annotation_id": f"source-{sample_id}",
                            "results": sample_results,
                        }
                    ]
                },
                validation_status="valid",
            )
            session.add(annotation)
            annotations.append(annotation)
        session.flush()
        session.add(
            DatasetVersion(
                id=DATASET_VERSION_ID,
                dataset_id=DATASET_ID,
                version=1,
                status="published",
                format="label_studio",
                object_uri="memory://datasets/paddlex/version-1",
                manifest_uri="memory://datasets/paddlex/version-1/manifest.json",
                manifest_checksum="a" * 64,
                source_revision=compute_paddlex_detection_source_revision(
                    dataset,
                    samples.values(),
                    annotations,
                ),
                total_count=4,
                valid_count=4,
                invalid_count=0,
                skipped_count=0,
                size_bytes=sum(map(len, IMAGE_PAYLOADS.values())),
                schema_snapshot={"names": ["正常", "瑕疵"]},
                published_at=datetime.now(UTC),
            )
        )
        session.commit()
    return factory


@pytest.fixture()
def storage() -> InMemoryObjectStorageClient:
    client = InMemoryObjectStorageClient()
    for sample_id, payload in IMAGE_PAYLOADS.items():
        client.objects[("datasets", f"paddlex/{sample_id}.jpg")] = payload
    return client


def _box(
    class_name: str,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    result_id: str,
) -> dict[str, object]:
    return {
        "source_result_id": result_id,
        "class_name": class_name,
        "shape": "rectangle",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }


def _export(
    session_factory,
    storage,
    output_dir: Path,
    runtime_model_id: str = "PP-YOLOE_plus-S",
):
    with session_factory() as session:
        return export_paddlex_detection_dataset(
            session,
            storage,
            DATASET_ID,
            DATASET_VERSION_ID,
            output_dir,
            runtime_model_id=runtime_model_id,
        )


def _refresh_source_revision(session) -> None:
    dataset = session.get(Dataset, DATASET_ID)
    samples = session.scalars(
        select(DatasetSample)
        .where(DatasetSample.dataset_id == DATASET_ID)
        .order_by(DatasetSample.id)
    ).all()
    annotations = session.scalars(
        select(Annotation)
        .join(DatasetSample, Annotation.dataset_sample_id == DatasetSample.id)
        .where(
            DatasetSample.dataset_id == DATASET_ID,
            Annotation.source.in_(("label_studio", "coco", "yolo")),
        )
        .order_by(Annotation.created_at, Annotation.id)
    ).all()
    version = session.get(DatasetVersion, DATASET_VERSION_ID)
    version.source_revision = compute_paddlex_detection_source_revision(
        dataset,
        samples,
        annotations,
    )


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_checksum(payload: dict[str, object]) -> str:
    unsigned = dict(payload)
    unsigned.pop("checksum_sha256", None)
    canonical = json.dumps(
        unsigned,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def test_exports_deterministic_paddlex_coco_with_all_splits_and_empty_images(
    session_factory,
    storage,
    tmp_path: Path,
) -> None:
    output = tmp_path / "export"
    report = _export(session_factory, storage, output)

    train = _read_json(output / "annotations/instance_train.json")
    val = _read_json(output / "annotations/instance_val.json")
    test = _read_json(output / "annotations/instance_test.json")
    assert report.sample_count == 4
    assert report.annotation_count == 4
    assert len(train["images"]) == 2
    assert len(train["annotations"]) == 2
    assert len(val["images"]) == len(val["annotations"]) == 1
    assert len(test["images"]) == len(test["annotations"]) == 1
    empty_image_id = next(
        image["id"]
        for image in train["images"]
        if image["file_name"].endswith("sample-train-empty.jpg")
    )
    assert all(item["image_id"] != empty_image_id for item in train["annotations"])
    assert train["categories"] == [
        {"id": 1, "name": "正常", "supercategory": ""},
        {"id": 2, "name": "瑕疵", "supercategory": ""},
    ]
    assert [item["category_id"] for item in train["annotations"]] == [1, 2]
    assert train["annotations"][1]["bbox"] == [50.0, 20.0, 25.0, 40.0]
    assert train["annotations"][1]["area"] == 1000.0
    assert (
        output / "images/train/sample-train-empty.jpg"
    ).read_bytes() == IMAGE_PAYLOADS["sample-train-empty"]

    second_output = tmp_path / "second-export"
    _export(session_factory, storage, second_output)
    for relative in (
        "annotations/instance_train.json",
        "annotations/instance_val.json",
        "annotations/instance_test.json",
        "dataset-manifest.json",
    ):
        assert (output / relative).read_bytes() == (
            second_output / relative
        ).read_bytes()


def test_dataset_manifest_preserves_source_identity_checksums_and_file_digests(
    session_factory,
    storage,
    tmp_path: Path,
) -> None:
    output = tmp_path / "export"
    report = _export(session_factory, storage, output)
    manifest = _read_json(output / "dataset-manifest.json")
    expected = _read_json(
        Path("tests/fixtures/paddlex_detection/expected_manifest.json")
    )
    with session_factory() as session:
        expected_source_revision = session.get(
            DatasetVersion,
            DATASET_VERSION_ID,
        ).source_revision

    assert manifest["source"] == {
        "dataset_id": DATASET_ID,
        "dataset_version_id": DATASET_VERSION_ID,
        "source_revision": expected_source_revision,
    }
    assert manifest["categories"] == expected["categories"]
    assert manifest["samples"] == expected["samples"]
    assert manifest["splits"] == expected["splits"]
    assert manifest["checksum_sha256"] == _canonical_checksum(manifest)
    assert report.manifest_checksum == manifest["checksum_sha256"]
    for entry in manifest["files"]:
        path = output / entry["path"]
        assert path.is_file()
        assert entry["size_bytes"] == path.stat().st_size
        assert entry["checksum_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_category_ids_are_stable_for_numeric_name_mapping(
    session_factory,
    storage,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        dataset = session.get(Dataset, DATASET_ID)
        dataset.class_schema = {"names": {"1": "瑕疵", "0": "正常"}}
        _refresh_source_revision(session)
        session.commit()

    _export(session_factory, storage, tmp_path / "export")
    train = _read_json(tmp_path / "export/annotations/instance_train.json")

    assert train["categories"] == [
        {"id": 1, "name": "正常", "supercategory": ""},
        {"id": 2, "name": "瑕疵", "supercategory": ""},
    ]


def test_category_ids_accept_integer_name_mapping(
    session_factory,
    storage,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        dataset = session.get(Dataset, DATASET_ID)
        dataset.class_schema = {"names": {1: "瑕疵", 0: "正常"}}
        _refresh_source_revision(session)
        session.commit()

    _export(session_factory, storage, tmp_path / "export")
    train = _read_json(tmp_path / "export/annotations/instance_train.json")

    assert train["categories"] == [
        {"id": 1, "name": "正常", "supercategory": ""},
        {"id": 2, "name": "瑕疵", "supercategory": ""},
    ]


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("missing_annotations", "imported annotation"),
        ("generated_only", "imported annotation"),
        ("invalid_alongside_valid", "invalid imported annotation"),
        ("invalid_record", "invalid imported annotation"),
        ("invalid_box", "invalid box"),
        ("missing_image", "image is unavailable"),
        ("zero_train", "train split"),
        ("zero_val", "val split"),
    ],
)
def test_preflight_rejects_invalid_datasets_before_export(
    case: str,
    message: str,
    session_factory,
    storage,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        if case == "missing_annotations":
            session.execute(delete(Annotation))
        elif case == "generated_only":
            for annotation in session.scalars(select(Annotation)):
                annotation.source = "generated"
        elif case == "invalid_alongside_valid":
            session.add(
                Annotation(
                    id="annotation-invalid-alongside-valid",
                    dataset_sample_id="sample-train-multi",
                    source="label_studio",
                    internal_payload={"issue": "invalid imported record"},
                    validation_status="invalid",
                )
            )
        elif case == "invalid_record":
            annotation = session.get(
                Annotation,
                "annotation-sample-train-multi",
            )
            annotation.internal_payload = {"annotations": ["not-a-record"]}
        elif case == "invalid_box":
            annotation = session.scalar(
                select(Annotation).where(
                    Annotation.id == "annotation-sample-train-multi"
                )
            )
            annotation.internal_payload = {
                "annotations": [
                    {
                        "source_annotation_id": "invalid-box",
                        "results": [
                            _box(
                                "瑕疵",
                                x=90,
                                y=10,
                                width=20,
                                height=20,
                                result_id="invalid-box",
                            )
                        ],
                    }
                ]
            }
        elif case == "missing_image":
            sample = session.get(DatasetSample, "sample-train-multi")
            sample.file_uri = "memory://datasets/paddlex/missing.jpg"
        elif case == "zero_train":
            for sample in session.scalars(
                select(DatasetSample).where(DatasetSample.split == "train")
            ):
                sample.split = "test"
        elif case == "zero_val":
            session.get(DatasetSample, "sample-val").split = "train"
        session.commit()

    output = tmp_path / "rejected-export"
    with pytest.raises(PaddleXDatasetError, match=message):
        _export(session_factory, storage, output)
    assert not output.exists()


@pytest.mark.parametrize("changed_entity", ["dataset", "sample", "annotation"])
def test_preflight_rejects_content_drift_for_the_same_dataset_version(
    changed_entity: str,
    session_factory,
    storage,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        if changed_entity == "dataset":
            dataset = session.get(Dataset, DATASET_ID)
            dataset.class_schema = {"names": ["正常", "瑕疵", "other"]}
        elif changed_entity == "sample":
            sample = session.get(DatasetSample, "sample-test")
            sample.file_uri = "memory://datasets/paddlex/sample-val.jpg"
        else:
            annotation = session.get(Annotation, "annotation-sample-test")
            payload = dict(annotation.internal_payload)
            payload["annotations"] = [dict(payload["annotations"][0])]
            payload["annotations"][0]["results"] = [
                dict(payload["annotations"][0]["results"][0])
            ]
            payload["annotations"][0]["results"][0]["x"] = 21
            annotation.internal_payload = payload
        session.commit()

    output = tmp_path / "drifted-export"
    with pytest.raises(PaddleXDatasetError, match="source revision"):
        _export(session_factory, storage, output)
    assert not output.exists()


def test_export_rejects_image_content_drift_for_the_same_dataset_version(
    session_factory,
    storage,
    tmp_path: Path,
) -> None:
    storage.objects[("datasets", "paddlex/sample-test.jpg")] = b"changed-image"

    output = tmp_path / "drifted-image-export"
    with pytest.raises(PaddleXDatasetError, match="image checksum"):
        _export(session_factory, storage, output)
    assert not output.exists()


@pytest.mark.parametrize("annotation_source", ["coco", "yolo"])
def test_preflight_accepts_supported_imported_annotation_sources(
    annotation_source: str,
    session_factory,
    storage,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        for annotation in session.scalars(select(Annotation)):
            annotation.source = annotation_source
        _refresh_source_revision(session)
        session.commit()

    report = _export(session_factory, storage, tmp_path / annotation_source)

    assert report.sample_count == 4


@pytest.mark.parametrize("runtime_model_id", ["PP-YOLOE_plus-S", "RT-DETR-L"])
def test_export_precheck_accepts_supported_paddlex_runtime_models(
    runtime_model_id: str,
    session_factory,
    storage,
    tmp_path: Path,
) -> None:
    report = _export(
        session_factory,
        storage,
        tmp_path / runtime_model_id,
        runtime_model_id,
    )

    assert report.sample_count == 4


def test_export_precheck_rejects_unknown_paddlex_runtime_model(
    session_factory,
    storage,
    tmp_path: Path,
) -> None:
    output = tmp_path / "unknown-model"
    with pytest.raises(PaddleXDatasetError, match="unsupported PaddleX runtime model"):
        _export(session_factory, storage, output, "PP-YOLOE-X")
    assert not output.exists()


def test_preflight_rejects_sample_ids_that_cannot_be_used_as_safe_filenames(
    session_factory,
    storage,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        sample = session.get(DatasetSample, "sample-test")
        annotation = session.get(Annotation, "annotation-sample-test")
        sample.id = "../../../../escaped"
        annotation.dataset_sample_id = sample.id
        _refresh_source_revision(session)
        session.commit()

    with pytest.raises(PaddleXDatasetError, match="safe filename"):
        _export(session_factory, storage, tmp_path / "export")

    assert not (tmp_path / "escaped.jpg").exists()


def test_failed_publication_restores_the_previous_export(
    session_factory,
    storage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "export"
    output.mkdir()
    (output / "previous.txt").write_text("previous", encoding="utf-8")
    original_replace = Path.replace

    def fail_staging_publish(path: Path, target: Path) -> Path:
        if path.name.startswith(".export-") and Path(target) == output:
            raise OSError("simulated publication failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_staging_publish)

    with pytest.raises(OSError, match="simulated publication failure"):
        _export(session_factory, storage, output)

    assert (output / "previous.txt").read_text(encoding="utf-8") == "previous"


def test_generates_typed_dataset_override_without_shell_fragments() -> None:
    parameters = PaddleXDetectionConfigParameters()

    overrides = build_paddlex_detection_config_overrides(parameters)

    assert [item.model_dump(mode="json") for item in overrides] == [
        {"key": "Global.dataset_dir", "value": "/workspace/dataset"}
    ]
    with pytest.raises(ValidationError):
        PaddleXDetectionConfigParameters.model_validate(
            {"dataset_dir": "/workspace/dataset; echo unsafe"}
        )
