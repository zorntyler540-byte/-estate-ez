"""Alembic owns the schema; API startup only checks it."""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session

from app import main as main_mod
from app import schema_check
from app.db import Base
from app.models import Account, EngagementSnapshot

ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"
PREVIOUS = "0049_client_invites"


def _cfg() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    return cfg


def _run(engine, fn, rev: str) -> None:
    cfg = _cfg()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        fn(cfg, rev)


def _engine(tmp_path, name: str = "app.db"):
    return create_engine(f"sqlite:///{tmp_path / name}")


def _columns(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_fresh_database_upgrade_matches_models(tmp_path):
    engine = _engine(tmp_path)
    _run(engine, command.upgrade, "head")
    assert schema_check.schema_problems(engine) == []


def test_head_revision_is_0050():
    assert schema_check.alembic_head_revisions() == {"0050_startup_ddl_to_alembic"}


def test_0050_adds_objects_only_startup_used_to_create(tmp_path):
    engine = _engine(tmp_path)
    _run(engine, command.upgrade, PREVIOUS)
    assert "engagement_snapshots" not in inspect(engine).get_table_names()
    assert "engagement_json" not in _columns(engine, "lots")
    assert "assigned_sale_id" not in _columns(engine, "users")
    problems = schema_check.schema_problems(engine)
    assert any(p.startswith("Missing tables: engagement_snapshots.") for p in problems)
    assert any("lots is missing columns engagement_json" in p for p in problems)
    assert any(PREVIOUS in p for p in problems)

    _run(engine, command.upgrade, "head")
    assert "engagement_snapshots" in inspect(engine).get_table_names()
    assert "engagement_json" in _columns(engine, "lots")
    assert "assigned_sale_id" in _columns(engine, "users")
    assert schema_check.schema_problems(engine) == []


def test_0050_is_a_noop_on_a_database_startup_already_built(tmp_path):
    """Production: every table/column already exists (create_all + _ensure_*), version is 0049."""
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    _run(engine, command.stamp, PREVIOUS)
    with Session(engine) as db:
        db.add(Account(id="acct-1", name="CT"))
        db.flush()
        db.add(
            EngagementSnapshot(
                id="snap-1", account_id="acct-1", ctseller_sale_id=1, ctseller_item_id=2, bucket="Rescue"
            )
        )
        db.commit()
    before = {t: _columns(engine, t) for t in inspect(engine).get_table_names()}

    _run(engine, command.upgrade, "head")

    after = {t: _columns(engine, t) for t in inspect(engine).get_table_names() if t != "alembic_version"}
    assert after == {t: c for t, c in before.items() if t != "alembic_version"}
    with engine.connect() as conn:
        bucket = conn.execute(text("SELECT bucket FROM engagement_snapshots WHERE id = 'snap-1'")).scalar()
    assert bucket != "Rescue"
    assert schema_check.schema_problems(engine) == []


def test_0050_downgrade_keeps_data_and_reupgrades(tmp_path):
    engine = _engine(tmp_path)
    _run(engine, command.upgrade, "head")
    _run(engine, command.downgrade, PREVIOUS)
    assert "engagement_json" in _columns(engine, "lots")
    _run(engine, command.upgrade, "head")
    assert schema_check.schema_problems(engine) == []


def test_api_startup_runs_no_ddl_and_warns_when_behind(tmp_path, monkeypatch, caplog):
    engine = _engine(tmp_path)
    _run(engine, command.upgrade, PREVIOUS)
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute", lambda *a: statements.append(a[2]))
    monkeypatch.setattr(main_mod, "engine", engine)
    monkeypatch.setattr(main_mod, "_start_in_process_maintenance", lambda: None)

    with caplog.at_level(logging.WARNING, logger="app.schema_check"):
        main_mod.startup()

    writes = [
        s for s in statements
        if s.lstrip().split(" ", 1)[0].upper() in {"CREATE", "ALTER", "DROP", "INSERT", "UPDATE", "DELETE"}
    ]
    assert writes == []
    assert "engagement_snapshots" not in inspect(engine).get_table_names()
    assert "alembic upgrade head" in caplog.text


def test_api_startup_is_quiet_at_head(tmp_path, monkeypatch, caplog):
    engine = _engine(tmp_path)
    _run(engine, command.upgrade, "head")
    monkeypatch.setattr(main_mod, "engine", engine)
    monkeypatch.setattr(main_mod, "_start_in_process_maintenance", lambda: None)
    with caplog.at_level(logging.WARNING, logger="app.schema_check"):
        main_mod.startup()
    assert caplog.text == ""


def test_schema_check_failure_does_not_block_startup(monkeypatch, caplog):
    def boom(_engine):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(schema_check, "schema_problems", boom)
    with caplog.at_level(logging.ERROR, logger="app.schema_check"):
        assert schema_check.warn_if_schema_out_of_date(object()) == []
    assert "schema check failed" in caplog.text


def test_api_start_commands_migrate_before_serving():
    root = Path(__file__).resolve().parents[2]
    procfile = (root / "Procfile").read_text()
    dockerfile = (root / "infra" / "docker" / "Dockerfile.api").read_text()
    worker = (root / "infra" / "docker" / "Dockerfile.worker").read_text()
    for start in (procfile, dockerfile):
        assert "alembic upgrade head &&" in start
    assert "alembic" not in worker.split("CMD", 1)[1]

