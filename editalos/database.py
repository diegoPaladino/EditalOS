from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from editalos.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine_kwargs: dict[str, object] = {
    "echo": False,
    "future": True,
}
if settings.db_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {
        "check_same_thread": False,
        "timeout": 30,
    }
    # SQLite local + Streamlit funciona melhor sem QueuePool.
    engine_kwargs["poolclass"] = NullPool

engine = create_engine(settings.db_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def initialize_database() -> None:
    Base.metadata.create_all(bind=engine)
    if not settings.db_url.startswith("sqlite"):
        return
    _ensure_subject_planning_columns()
    _ensure_study_session_context_columns()
    _ensure_card_study_session_columns()


def _ensure_subject_planning_columns() -> None:
    inspector = inspect(engine)
    if "subjects" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("subjects")}
    statements: list[str] = []
    if "planned_total_minutes" not in columns:
        statements.append("ALTER TABLE subjects ADD COLUMN planned_total_minutes INTEGER")
    if "planned_weekly_minutes" not in columns:
        statements.append("ALTER TABLE subjects ADD COLUMN planned_weekly_minutes INTEGER")
    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_study_session_context_columns() -> None:
    inspector = inspect(engine)
    if "study_sessions" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("study_sessions")}
    statements: list[str] = []
    if "content_summary" not in columns:
        statements.append("ALTER TABLE study_sessions ADD COLUMN content_summary TEXT")
    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_card_study_session_columns() -> None:
    inspector = inspect(engine)
    if "cards" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("cards")}
    statements: list[str] = []
    if "study_session_id" not in columns:
        statements.append("ALTER TABLE cards ADD COLUMN study_session_id INTEGER")
    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
