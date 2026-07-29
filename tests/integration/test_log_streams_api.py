import json

from sqlalchemy.orm import Session

from visiox_api.services.log_streams import (
    LogEntry,
    append_lines,
    close_stream,
    open_stream,
)
from visiox_db.models import LogStream, ResourceGrant, TrainingJob, TrainingPipeline
from visiox_storage.client import InMemoryObjectStorageClient


def _seed_stream(
    session: Session,
    storage: InMemoryObjectStorageClient,
    identity: dict[str, object],
) -> str:
    pipeline = TrainingPipeline(
        name="log-api-pipeline",
        organization_id=str(identity["organization_id"]),
        owner_user_id=str(identity["admin_id"]),
        visibility="private",
        task="detect",
        scale="n",
        status="success",
    )
    session.add(pipeline)
    session.flush()
    job = TrainingJob(
        pipeline_id=pipeline.id,
        organization_id=str(identity["organization_id"]),
        owner_user_id=str(identity["admin_id"]),
        visibility="private",
        status="success",
    )
    session.add(job)
    session.flush()
    stream = open_stream(
        session,
        organization_id=str(identity["organization_id"]),
        resource_type="training_job",
        resource_id=job.id,
        source="training",
    )
    append_lines(
        session,
        storage,
        stream.id,
        [LogEntry(message=f"line-{index}", source="stdout") for index in range(5)],
        target_chunk_bytes=100,
    )
    close_stream(session, storage, stream.id, status="completed")
    session.commit()
    return stream.id


def test_log_history_download_and_completed_sse_are_authorized(
    authenticated_client,
) -> None:
    client, session_factory, _settings, identity = authenticated_client
    storage = client.app.state.object_storage
    with session_factory() as session:
        stream_id = _seed_stream(session, storage, identity)

    metadata = client.get(
        f"/log-streams/{stream_id}", headers=identity["admin_headers"]
    )
    assert metadata.status_code == 200
    assert metadata.json()["status"] == "completed"
    first = client.get(
        f"/log-streams/{stream_id}/chunks?max_lines=2",
        headers=identity["admin_headers"],
    )
    assert first.status_code == 200
    assert [line["message"] for line in first.json()["lines"]] == ["line-0", "line-1"]
    second = client.get(
        f"/log-streams/{stream_id}/chunks",
        params={"cursor": first.json()["next_cursor"]},
        headers=identity["admin_headers"],
    )
    assert [line["message"] for line in second.json()["lines"]] == [
        "line-2",
        "line-3",
        "line-4",
    ]

    download = client.get(
        f"/log-streams/{stream_id}/download", headers=identity["admin_headers"]
    )
    assert download.status_code == 200
    assert (
        f'filename="training-{stream_id}.log"'
        in download.headers["content-disposition"]
    )
    assert [json.loads(line)["message"] for line in download.text.splitlines()] == [
        f"line-{index}" for index in range(5)
    ]

    events = client.get(
        f"/log-streams/{stream_id}/events", headers=identity["admin_headers"]
    )
    assert events.status_code == 200
    assert "event: lines" in events.text
    assert "event: end" in events.text


def test_log_access_requires_parent_view_permission(authenticated_client) -> None:
    client, session_factory, _settings, identity = authenticated_client
    storage = client.app.state.object_storage
    with session_factory() as session:
        stream_id = _seed_stream(session, storage, identity)
        stream = session.get(LogStream, stream_id)
        assert stream is not None
        session.add(
            ResourceGrant(
                organization_id=str(identity["organization_id"]),
                resource_type="training_job",
                resource_id=stream.resource_id,
                principal_type="user",
                principal_id=str(identity["member_id"]),
                permissions=["invoke"],
                created_by=str(identity["admin_id"]),
            )
        )
        session.commit()

    denied = client.get(f"/log-streams/{stream_id}", headers=identity["member_headers"])
    assert denied.status_code == 403

    with session_factory() as session:
        grant = (
            session.query(ResourceGrant)
            .filter_by(
                principal_id=str(identity["member_id"]), resource_type="training_job"
            )
            .one()
        )
        grant.permissions = ["view"]
        session.commit()
    allowed = client.get(
        f"/log-streams/{stream_id}", headers=identity["member_headers"]
    )
    assert allowed.status_code == 200
