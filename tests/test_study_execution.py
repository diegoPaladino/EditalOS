from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from editalos.database import Base
from editalos.enums import ReviewTaskStatus
from editalos.enums import SRSAlgorithm
from editalos.models import Card, ReviewTask, StudySession, Subject, Topic, TopicProgress
from editalos.services.study_execution import StudyExecutionService


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    testing_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    db = testing_session_local()
    try:
        yield db
    finally:
        db.close()


def _seed_topic(session) -> Topic:
    subject = Subject(name="Direito Constitucional", weight=1.2)
    session.add(subject)
    session.flush()

    topic = Topic(
        subject_id=subject.id,
        name="Controle de Constitucionalidade",
        weight=1.1,
        incidence_estimate=0.7,
        difficulty_baseline=0.5,
        is_active=True,
    )
    session.add(topic)
    session.flush()
    return topic


def test_finish_session_generates_reviews_and_progress(session, monkeypatch):
    topic = _seed_topic(session)
    service = StudyExecutionService(session)

    t0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=20)
    t2 = t1 + timedelta(minutes=10)
    t3 = t2 + timedelta(minutes=30)
    timeline = iter([t0, t1, t2, t3])
    monkeypatch.setattr(StudyExecutionService, "now_utc", staticmethod(lambda: next(timeline)))

    service.start_session(topic.id)
    service.pause_session()
    service.resume_session()
    result = service.finish_session(content_summary="Texto sobre controle concentrado e difuso.")

    assert result.generated_reviews == 4
    assert result.gross_seconds == 3600
    assert result.net_seconds == 3000

    tasks = session.scalars(select(ReviewTask).order_by(ReviewTask.due_at)).all()
    assert len(tasks) == 4
    assert all(task.status == ReviewTaskStatus.PENDING.value for task in tasks)

    session_row = session.get(StudySession, result.study_session_id)
    assert session_row is not None
    assert session_row.content_summary == "Texto sobre controle concentrado e difuso."

    progress = session.scalars(select(TopicProgress).where(TopicProgress.topic_id == topic.id)).first()
    assert progress is not None
    assert progress.total_sessions == 1
    assert progress.total_gross_seconds == 3600
    assert progress.total_net_seconds == 3000


def test_sync_and_complete_review_updates_status_and_progress(session, monkeypatch):
    topic = _seed_topic(session)
    service = StudyExecutionService(session)

    reference_time = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
    task = ReviewTask(
        topic_id=topic.id,
        due_at=reference_time - timedelta(days=1),
        status=ReviewTaskStatus.PENDING.value,
    )
    session.add(task)
    session.flush()

    monkeypatch.setattr(StudyExecutionService, "now_utc", staticmethod(lambda: reference_time))

    changed = service.sync_review_statuses()
    assert changed == 1
    assert task.status == ReviewTaskStatus.OVERDUE.value

    service.mark_review_completed(task.id)
    assert task.status == ReviewTaskStatus.COMPLETED.value
    assert task.completed_at == reference_time

    progress = session.scalars(select(TopicProgress).where(TopicProgress.topic_id == topic.id)).first()
    assert progress is not None
    assert progress.total_reviews_completed == 1
    assert progress.last_review_completed_at == reference_time


def test_session_snapshot_handles_naive_datetimes_from_sqlite(session, monkeypatch):
    topic = _seed_topic(session)
    service = StudyExecutionService(session)

    started_at = datetime(2026, 3, 15, 19, 9, 0)
    active_run = service.start_session(topic.id)
    active_run.started_at = started_at
    active_run.last_resumed_at = started_at
    session.flush()

    monkeypatch.setattr(
        StudyExecutionService,
        "now_utc",
        staticmethod(lambda: datetime(2026, 3, 15, 19, 19, 0, tzinfo=UTC)),
    )

    snapshot = service.session_snapshot(active_run)

    assert snapshot is not None
    assert snapshot["gross_seconds"] == 600
    assert snapshot["net_seconds"] == 600


def test_due_reviews_include_study_context_and_linked_cards(session, monkeypatch):
    topic = _seed_topic(session)
    service = StudyExecutionService(session)

    t0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=30)
    t2 = t1
    timeline = iter([t0, t1, t2])
    monkeypatch.setattr(StudyExecutionService, "now_utc", staticmethod(lambda: next(timeline)))

    service.start_session(topic.id)
    result = service.finish_session(content_summary="Leitura de texto argumentativo e inferencia.")
    session.add(
        Card(
            topic_id=topic.id,
            study_session_id=result.study_session_id,
            front="Qual era a tese principal do texto?",
            back="Inferencia exige leitura contextualizada.",
            algorithm=SRSAlgorithm.FSRS.value,
        )
    )
    session.flush()
    review_row = service.list_due_reviews_today(today=t0.date())[0]

    assert review_row["content_summary"] == "Leitura de texto argumentativo e inferencia."
    assert review_row["linked_cards"] == 1
