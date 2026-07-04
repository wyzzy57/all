from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from visiox_db.models import Annotation, Dataset, DatasetSample
from visiox_storage.client import InMemoryObjectStorageClient
from visiox_yolo26.converters import ConversionError, export_yolo26_dataset
from visiox_yolo26.tasks import YOLO26_SCALES, YOLO26_TASKS, task_scale_key


@pytest.fixture()
def session_factory(tmp_path):
    database_path = tmp_path / "visiox-yolo26-converters.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def storage(tmp_path) -> InMemoryObjectStorageClient:
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (100, 80), color=(12, 34, 56)).save(image_path)
    client = InMemoryObjectStorageClient()
    client.put_file("datasets", "samples/sample.png", image_path)
    return client


def _result(
    shape: str,
    class_name: str = "defect",
    source_result_id: str = "result-1",
    **values,
) -> dict[str, object]:
    return {
        "source_result_id": source_result_id,
        "class_name": class_name,
        "shape": shape,
        **values,
    }


def _create_dataset(
    session_factory,
    task: str,
    results: list[dict[str, object]],
    *,
    width: int | None = 100,
    height: int | None = 80,
    split: str | None = "train",
    class_schema: dict[str, object] | None = None,
) -> str:
    with session_factory() as session:
        dataset = Dataset(
            name=f"{task}-{uuid4()}",
            task=task,
            status="created",
            class_schema=class_schema or {"names": ["ok", "defect"]},
            sample_count=1,
            annotation_count=1,
            source="upload",
        )
        session.add(dataset)
        session.flush()
        sample = DatasetSample(
            dataset_id=dataset.id,
            file_uri="memory://datasets/samples/sample.png",
            width=width,
            height=height,
            checksum=f"{task}-sample",
            split=split,
            annotation_status="labeled",
        )
        session.add(sample)
        session.flush()
        session.add(
            Annotation(
                dataset_sample_id=sample.id,
                source="label_studio",
                internal_payload={
                    "annotations": [
                        {
                            "source_annotation_id": "annotation-1",
                            "results": results,
                        }
                    ]
                },
                validation_status="valid",
            )
        )
        session.commit()
        return dataset.id


def _export(
    session_factory,
    storage: InMemoryObjectStorageClient,
    tmp_path: Path,
    dataset_id: str,
):
    output_dir = tmp_path / "export"
    with session_factory() as session:
        report = export_yolo26_dataset(session, storage, dataset_id, output_dir)
    return report, output_dir


def _read_text(output_dir: Path, relative_path: str) -> str:
    return (output_dir / relative_path).read_text(encoding="utf-8")


def _assert_common_data_yaml(output_dir: Path, task: str) -> None:
    assert _read_text(output_dir, "data.yaml") == (
        "path: .\n"
        "train: images/train\n"
        "val: images/val\n"
        "test: images/test\n"
        "names:\n"
        "  0: ok\n"
        "  1: defect\n"
        f"task: {task}\n"
    )


def test_task_registry_covers_yolo26_tasks_and_scales():
    assert YOLO26_TASKS == ("detect", "segment", "semantic", "pose", "obb", "classify")
    assert YOLO26_SCALES == ("n", "s", "m", "l", "x")
    assert task_scale_key("detect", "n") == "yolo26n-detect"


def test_detect_converter_writes_yolo_rectangle_labels_and_data_yaml(session_factory, storage, tmp_path):
    dataset_id = _create_dataset(
        session_factory,
        "detect",
        [_result("rectangle", x=10, y=20, width=30, height=40)],
    )

    report, output_dir = _export(session_factory, storage, tmp_path, dataset_id)

    assert _read_text(output_dir, "labels/train/sample.txt") == "1 0.25 0.4 0.3 0.4\n"
    assert (output_dir / "images/train/sample.png").read_bytes() == storage.objects[("datasets", "samples/sample.png")]
    _assert_common_data_yaml(output_dir, "detect")
    assert report.sample_count == 1
    assert report.annotation_count == 1
    assert report.warnings == []


def test_segment_converter_writes_polygon_labels(session_factory, storage, tmp_path):
    dataset_id = _create_dataset(
        session_factory,
        "segment",
        [_result("polygon", points=[[10, 10], [50, 10], [50, 50], [10, 50]])],
    )

    _report, output_dir = _export(session_factory, storage, tmp_path, dataset_id)

    assert _read_text(output_dir, "labels/train/sample.txt") == "1 0.1 0.1 0.5 0.1 0.5 0.5 0.1 0.5\n"
    _assert_common_data_yaml(output_dir, "segment")


def test_semantic_converter_rasterizes_masks_and_warns_on_overlap(session_factory, storage, tmp_path):
    dataset_id = _create_dataset(
        session_factory,
        "semantic",
        [
            _result("polygon", "ok", "poly-1", points=[[10, 10], [60, 10], [60, 60], [10, 60]]),
            _result("polygon", "defect", "poly-2", points=[[40, 40], [80, 40], [80, 70], [40, 70]]),
            _result("brush", "ok", "brush-1", points=[[80, 10], [95, 10], [95, 25], [80, 25]]),
        ],
    )

    report, output_dir = _export(session_factory, storage, tmp_path, dataset_id)

    with Image.open(output_dir / "masks/train/sample.png") as mask:
        assert mask.mode == "L"
        assert mask.getpixel((20, 20)) == 1
        assert mask.getpixel((50, 50)) == 2
        assert mask.getpixel((90, 15)) == 1
        assert mask.getpixel((90, 70)) == 0
    assert report.warnings
    assert "semantic mask overlap" in report.warnings[0].message
    assert _read_text(output_dir, "data.yaml") == (
        "path: .\n"
        "train: images/train\n"
        "val: images/val\n"
        "test: images/test\n"
        "names:\n"
        "  0: ok\n"
        "  1: defect\n"
        "task: semantic\n"
        "mask: masks\n"
    )


def test_semantic_converter_accepts_decoded_brush_mask(session_factory, storage, tmp_path):
    dataset_id = _create_dataset(
        session_factory,
        "semantic",
        [
            _result(
                "brush",
                "defect",
                "brush-mask-1",
                mask=[
                    [0, 1, 0],
                    [1, 1, 0],
                    [0, 0, 0],
                ],
            )
        ],
        width=3,
        height=3,
    )

    _report, output_dir = _export(session_factory, storage, tmp_path, dataset_id)

    with Image.open(output_dir / "masks/train/sample.png") as mask:
        assert list(mask.getdata()) == [0, 2, 0, 2, 2, 0, 0, 0, 0]


def test_pose_converter_writes_coco17_keypoints_with_missing_points(session_factory, storage, tmp_path):
    dataset_id = _create_dataset(
        session_factory,
        "pose",
        [
            _result("rectangle", source_result_id="bbox-1", x=10, y=20, width=30, height=40),
            _result("keypoints", source_result_id="nose-1", keypoint_name="nose", x=20, y=30),
            _result("keypoints", source_result_id="left-eye-1", keypoint_name="left_eye", x=25, y=35),
        ],
    )

    _report, output_dir = _export(session_factory, storage, tmp_path, dataset_id)

    values = _read_text(output_dir, "labels/train/sample.txt").strip().split(" ")
    assert values[:5] == ["1", "0.25", "0.4", "0.3", "0.4"]
    assert values[5:11] == ["0.2", "0.3", "2", "0.25", "0.35", "2"]
    assert values[11:] == ["0", "0", "0"] * 15


def test_obb_converter_accepts_polygon_and_rectangle(session_factory, storage, tmp_path):
    polygon_dataset_id = _create_dataset(
        session_factory,
        "obb",
        [_result("polygon", points=[[10, 10], [50, 10], [50, 40], [10, 40]])],
    )
    _report, polygon_output = _export(session_factory, storage, tmp_path / "polygon", polygon_dataset_id)

    rectangle_dataset_id = _create_dataset(
        session_factory,
        "obb",
        [_result("rectangle", x=10, y=20, width=30, height=40)],
    )
    _report, rectangle_output = _export(session_factory, storage, tmp_path / "rectangle", rectangle_dataset_id)

    assert _read_text(polygon_output, "labels/train/sample.txt") == "1 0.1 0.1 0.5 0.1 0.5 0.4 0.1 0.4\n"
    assert _read_text(rectangle_output, "labels/train/sample.txt") == "1 0.1 0.2 0.4 0.2 0.4 0.6 0.1 0.6\n"


def test_classify_converter_copies_image_to_class_directory_and_writes_data_yaml(session_factory, storage, tmp_path):
    dataset_id = _create_dataset(
        session_factory,
        "classify",
        [_result("classification", class_name="defect")],
    )

    _report, output_dir = _export(session_factory, storage, tmp_path, dataset_id)

    assert (output_dir / "train/defect/sample.png").read_bytes() == storage.objects[("datasets", "samples/sample.png")]
    assert _read_text(output_dir, "data.yaml") == (
        "path: .\n"
        "train: train\n"
        "val: val\n"
        "test: test\n"
        "names:\n"
        "  0: ok\n"
        "  1: defect\n"
        "task: classify\n"
    )


@pytest.mark.parametrize(
    ("results", "mutate", "match"),
    [
        ([_result("rectangle", class_name="missing", x=10, y=20, width=30, height=40)], None, "unknown class"),
        ([_result("rectangle", x=10, y=20, width=30, height=40)], lambda sample: setattr(sample, "width", None), "missing width"),
        ([_result("rectangle", x=-1, y=20, width=30, height=40)], None, "invalid coordinate"),
        ([], None, "missing annotation"),
        ([_result("brush", rle=[1, 2, 3])], None, "brush rle is not supported"),
    ],
)
def test_converter_errors_include_dataset_sample_and_source_result_id(
    session_factory,
    storage,
    tmp_path,
    results: list[dict[str, object]],
    mutate: Callable[[DatasetSample], None] | None,
    match: str,
):
    dataset_id = _create_dataset(session_factory, "detect", results)
    if mutate is not None:
        with session_factory() as session:
            sample = session.query(DatasetSample).one()
            mutate(sample)
            session.add(sample)
            session.commit()

    with session_factory() as session:
        with pytest.raises(ConversionError) as exc_info:
            export_yolo26_dataset(session, storage, dataset_id, tmp_path / "export")

    message = str(exc_info.value)
    assert match in message
    assert f"dataset={dataset_id}" in message
    assert "sample=" in message
    if results:
        assert "source_result_id=" in message
