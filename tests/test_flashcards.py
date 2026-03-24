from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from editalos.database import Base
from editalos.enums import CardStatus, ReviewRating, SRSAlgorithm
from editalos.models import CardReview, StudySession, Subject, Topic
from editalos.services.flashcards import FlashcardService


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
    subject = Subject(name="Tecnologia da Informacao", weight=1.0)
    session.add(subject)
    session.flush()

    topic = Topic(
        subject_id=subject.id,
        name="Banco de Dados",
        weight=1.0,
        incidence_estimate=0.5,
        difficulty_baseline=0.5,
        is_active=True,
    )
    session.add(topic)
    session.flush()
    return topic


def test_create_card_adds_new_card_to_review_queue(session):
    topic = _seed_topic(session)
    service = FlashcardService(session)

    card = service.create_card(
        topic_id=topic.id,
        front="O que e normalizacao?",
        back="Processo de organizar dados para reduzir redundancia.",
        algorithm=SRSAlgorithm.FSRS,
        tags="sql, banco de dados, SQL",
    )

    queue = service.list_review_queue()

    assert card.id is not None
    assert card.status == CardStatus.NEW.value
    assert card.tags == ["sql", "banco de dados"]
    assert len(queue) == 1
    assert queue[0]["id"] == card.id
    assert queue[0]["status"] == CardStatus.NEW.value


def test_review_card_persists_schedule_state_and_history(session):
    topic = _seed_topic(session)
    service = FlashcardService(session)
    card = service.create_card(
        topic_id=topic.id,
        front="O que e uma chave primaria?",
        back="Identificador unico de cada registro.",
        algorithm=SRSAlgorithm.FSRS,
    )

    result = service.review_card(card_id=card.id, rating=ReviewRating.GOOD)
    reviews = session.scalars(select(CardReview).where(CardReview.card_id == card.id)).all()
    queue_after_review = service.list_review_queue()

    assert result.card_id == card.id
    assert result.algorithm == SRSAlgorithm.FSRS.value
    assert result.due_at is not None
    assert result.due_at > result.reviewed_at
    assert card.status == CardStatus.ACTIVE.value
    assert card.schedule_state is not None
    assert card.schedule_state.due_at == result.due_at
    assert len(reviews) == 1
    assert queue_after_review == []


def test_create_card_can_link_to_study_session_of_same_topic(session):
    topic = _seed_topic(session)
    service = FlashcardService(session)
    study_session = StudySession(
        subject_id=topic.subject_id,
        topic_id=topic.id,
        started_at=datetime(2026, 3, 15, 10, 0, tzinfo=UTC),
        ended_at=datetime(2026, 3, 15, 10, 30, tzinfo=UTC),
        actual_minutes=30,
        content_summary="Artigo sobre inferencia textual e erros recorrentes.",
    )
    session.add(study_session)
    session.flush()

    card = service.create_card(
        topic_id=topic.id,
        study_session_id=study_session.id,
        front="Qual era a ideia central do artigo?",
        back="Inferencia depende de pistas textuais e contexto.",
        algorithm=SRSAlgorithm.FSRS,
    )

    assert card.study_session_id == study_session.id


def test_export_to_anki_tsv_includes_header_and_html_breaks(session):
    topic = _seed_topic(session)
    service = FlashcardService(session)
    service.create_card(
        topic_id=topic.id,
        front="Primeira linha\nSegunda linha",
        back="Resposta objetiva",
        algorithm=SRSAlgorithm.FSRS,
        tags="sql, banco",
    )

    exported = service.export_to_anki_tsv()
    lines = exported.strip().splitlines()

    assert lines[0] == "Front\tBack\tTags\tDisciplina\tTopico\tContexto\tCardID"
    assert "Primeira linha<br>Segunda linha" in lines[1]
    assert "\tsql banco\t" in lines[1]
