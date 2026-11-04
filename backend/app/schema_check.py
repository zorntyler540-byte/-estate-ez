"""Read-only startup check that the database matches the code. Never runs DDL.

Schema changes ship as Alembic revisions under ``backend/alembic/versions`` and are applied
by ``python -m alembic upgrade head`` before the API starts (``Procfile`` / ``Dockerfile.api``).
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.db import Base

_LOG = logging.getLogger(__name__)

_ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"

UPGRADE_HINT = "Run `python -m alembic upgrade head` (from backend/ in local dev)."


def alembic_head_revisions() -> set[str]:
    cfg = Config()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def schema_problems(engine: Engine) -> list[str]:
    """Human-readable differences between the live database and the code. Empty when in sync."""
    problems: list[str] = []
    heads = alembic_head_revisions()
    with engine.connect() as conn:
        current = set(MigrationContext.configure(conn).get_current_heads())
        live_columns = {
            table: {c["name"] for c in cols}
            for (_schema, table), cols in inspect(conn).get_multi_columns().items()
        }
    if current != heads:
        shown = ", ".join(sorted(current)) or "none"
        problems.append(
            f"Database Alembic revision is {shown}; code expects {', '.join(sorted(heads))}. {UPGRADE_HINT}"
        )
    missing_tables: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in live_columns:
            missing_tables.append(table.name)
            continue
        missing = sorted(c.name for c in table.columns if c.name not in live_columns[table.name])
        if missing:
            problems.append(f"Table {table.name} is missing columns {', '.join(missing)}. {UPGRADE_HINT}")
    if missing_tables:
        problems.append(f"Missing tables: {', '.join(sorted(missing_tables))}. {UPGRADE_HINT}")
    return problems


def warn_if_schema_out_of_date(engine: Engine) -> list[str]:
    try:
        problems = schema_problems(engine)
    except Exception:
        _LOG.exception("Database schema check failed; continuing startup")
        return []
    for problem in problems:
        _LOG.warning("Schema check: %s", problem)
    return problems
