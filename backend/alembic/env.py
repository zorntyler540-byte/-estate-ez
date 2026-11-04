from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from app.config import settings
from app.db import Base
from app import models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata

# Two-argument advisory lock, separate from catalog-job (one-arg) and Gemini-slot ('GEMI') keys.
# Held for the migration transaction so two ``alembic upgrade head`` runs (API replicas, a
# manual run during a deploy) apply revisions one at a time; the second sees the new head.
MIGRATION_LOCK_CLASSID = 0x414C4D42  # 'ALMB'
MIGRATION_LOCK_OBJID = 1


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        if connection.dialect.name.startswith("postgres"):
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:c, :o)"),
                {"c": MIGRATION_LOCK_CLASSID, "o": MIGRATION_LOCK_OBJID},
            )
        context.run_migrations()


def run_migrations_online() -> None:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        _run_with_connection(supplied)
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _run_with_connection(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
