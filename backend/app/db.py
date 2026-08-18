from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _create_engine_kwargs() -> dict:
    url = (settings.database_url or "").strip().lower()
    kwargs: dict = {
        "future": True,
        "pool_pre_ping": True,
        "pool_recycle": settings.db_pool_recycle,
    }
    if "postgresql" in url:
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
        kwargs["pool_timeout"] = settings.db_pool_timeout
        # Recycle the most-recently-used connection first so bursty HTTP (phone Sync,
        # dashboard poll) does not keep every overflow slot checked out.
        kwargs["pool_use_lifo"] = True
    return kwargs


engine = create_engine(settings.database_url, **_create_engine_kwargs())
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

