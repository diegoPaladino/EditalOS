from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from editalos.database import Base
from editalos.models import StudySession, Subject, Topic
from editalos.services.analytics import AnalyticsService


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
    subject = Subject(name="Legislacao Tributaria", weight=1.0)
    session.add(subject)
    session.flush()

    topic = Topic(
        subject_id=subject.id,
        name="Lei 11.651/1991",
        weight=1.0,
        incidence_estimate=0.8,
        difficulty_baseline=0.6,
        is_active=True,
    )
    session.add(topic)
    session.flush()
    return topic


def test_study_daily_summary_groups_sessions_by_local_day(session):
    topic = _seed_topic(session)
    session.add_all(
        [
            StudySession(
                subject_id=topic.subject_id,
                topic_id=topic.id,
                started_at=datetime(2026, 3, 11, 1, 30, tzinfo=UTC),
                ended_at=datetime(2026, 3, 11, 2, 15, tzinfo=UTC),
                actual_minutes=45,
                content_summary="Leitura inicial.",
            ),
            StudySession(
                subject_id=topic.subject_id,
                topic_id=topic.id,
                started_at=datetime(2026, 3, 11, 4, 10, tzinfo=UTC),
                ended_at=datetime(2026, 3, 11, 5, 0, tzinfo=UTC),
                actual_minutes=50,
                content_summary="Questoes comentadas.",
            ),
        ]
    )
    session.flush()

    analytics = AnalyticsService(session)
    summary = analytics.study_daily_summary(start_date=date(2026, 3, 10), end_date=date(2026, 3, 11))

    assert summary["date"].tolist() == [date(2026, 3, 10), date(2026, 3, 11)]
    assert summary["minutes"].tolist() == [45, 50]
    assert summary["sessions"].tolist() == [1, 1]
    assert summary["is_studied"].tolist() == [True, True]


def test_study_day_detail_returns_sessions_and_subject_breakdown(session):
    topic = _seed_topic(session)
    base = datetime(2026, 3, 20, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            StudySession(
                subject_id=topic.subject_id,
                topic_id=topic.id,
                started_at=base,
                ended_at=base + timedelta(minutes=35),
                actual_minutes=35,
                content_summary="Leitura seca da lei.",
            ),
            StudySession(
                subject_id=topic.subject_id,
                topic_id=topic.id,
                started_at=base + timedelta(hours=3),
                ended_at=base + timedelta(hours=3, minutes=25),
                actual_minutes=25,
                content_summary="Revisao de excecoes.",
            ),
        ]
    )
    session.flush()

    analytics = AnalyticsService(session)
    detail = analytics.study_day_detail(date(2026, 3, 20))

    assert detail["total_minutes"] == 60
    assert detail["sessions_count"] == 2
    assert detail["subjects_count"] == 1
    assert detail["topics_count"] == 1
    assert [row["content_summary"] for row in detail["sessions"]] == [
        "Leitura seca da lei.",
        "Revisao de excecoes.",
    ]
    assert detail["subjects"] == [
        {
            "subject": "Legislacao Tributaria",
            "minutes": 60,
            "sessions": 2,
            "topics_count": 1,
        }
    ]
