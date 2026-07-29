from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from visiox_api.maintenance.backfill_resource_ownership import backfill_resource_ownership
from visiox_db.base import Base
from visiox_db.models import Dataset, ResourceGrant, TrainingPipeline
from visiox_db.models.identity import Organization, User


def test_resource_ownership_backfill_is_idempotent_and_preserves_public_pipeline() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        organization = Organization(name="Default", slug="default")
        session.add(organization)
        session.flush()
        admin = User(
            organization_id=organization.id,
            username="admin",
            display_name="Admin",
            email="admin@example.test",
            password_hash="hash",
            role="admin",
            status="active",
            must_change_password=False,
        )
        session.add(admin)
        session.flush()
        dataset = Dataset(name="legacy-data", task="detect")
        pipeline = TrainingPipeline(
            name="legacy-public",
            task="detect",
            scale="n",
            is_public=True,
        )
        session.add_all([dataset, pipeline])
        session.commit()

        first = backfill_resource_ownership(session, organization, admin)
        second = backfill_resource_ownership(session, organization, admin)

        assert first.before == 2
        assert first.after == 0
        assert second.updated == 0
        assert dataset.organization_id == organization.id
        assert dataset.owner_user_id == admin.id
        assert pipeline.visibility == "organization"
        assert session.scalar(select(func.count()).select_from(ResourceGrant)) == 1
        grant = session.scalar(select(ResourceGrant))
        assert grant.permissions == ["view"]


def test_resource_ownership_backfill_runs_against_the_nullable_migration_schema(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'ownership-backfill-0003.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260727_0003")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, status) "
                "VALUES ('org-1', 'Default', 'default', 'active')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, organization_id, username, display_name, email, password_hash, role, status, must_change_password) "
                "VALUES ('user-1', 'org-1', 'admin', 'Admin', 'admin@example.test', 'hash', 'admin', 'active', false)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO datasets "
                "(id, name, task, status, class_schema, sample_count, annotation_count, created_at, updated_at) "
                "VALUES ('dataset-1', 'legacy', 'detect', 'created', '{}', 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    with Session(engine, expire_on_commit=False) as session:
        organization = session.get(Organization, "org-1")
        admin = session.get(User, "user-1")
        result = backfill_resource_ownership(session, organization, admin)

    assert result.before == 1
    assert result.after == 0
    with engine.connect() as connection:
        owner = connection.execute(
            text("SELECT organization_id, owner_user_id FROM datasets WHERE id='dataset-1'")
        ).one()
        assert owner == ("org-1", "user-1")
