from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from editalos.enums import CardStatus, ReviewRating, SRSAlgorithm
from editalos.models import Card, CardReview, CardScheduleState, StudySession, Subject, Topic
from editalos.schemas import CardCreate, ReviewResult
from editalos.services.srs import SRSService


class FlashcardServiceError(Exception):
    pass


class FlashcardService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.srs = SRSService()

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def day_bounds(reference_date: date) -> tuple[datetime, datetime]:
        start = datetime.combine(reference_date, time.min, tzinfo=UTC)
        end = start + timedelta(days=1)
        return start, end

    def list_topic_options(self) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(Topic.id, Subject.name.label("subject"), Topic.name.label("topic"))
            .join(Subject, Subject.id == Topic.subject_id)
            .where(Topic.is_active.is_(True))
            .order_by(Subject.name, Topic.name)
        ).all()
        return [
            {
                "id": int(row.id),
                "subject": str(row.subject),
                "topic": str(row.topic),
                "label": f"{row.subject} / {row.topic} (id={row.id})",
            }
            for row in rows
        ]

    def create_card(
        self,
        *,
        topic_id: int,
        study_session_id: int | None = None,
        front: str,
        back: str,
        algorithm: SRSAlgorithm = SRSAlgorithm.FSRS,
        tags: str | list[str] | None = None,
    ) -> Card:
        topic = self.session.get(Topic, topic_id)
        if topic is None or not topic.is_active:
            raise FlashcardServiceError("Topico invalido para cadastrar flashcard.")

        normalized_front = front.strip()
        normalized_back = back.strip()
        if not normalized_front:
            raise FlashcardServiceError("Informe a frente do flashcard.")
        if not normalized_back:
            raise FlashcardServiceError("Informe o verso do flashcard.")

        target_study_session_id: int | None = None
        if study_session_id is not None:
            study_session = self.session.get(StudySession, study_session_id)
            if study_session is None:
                raise FlashcardServiceError("Sessao de estudo selecionada nao foi encontrada.")
            if study_session.topic_id != topic_id:
                raise FlashcardServiceError("A sessao selecionada nao pertence ao mesmo topico do flashcard.")
            target_study_session_id = study_session.id

        payload = CardCreate(
            topic_id=topic_id,
            study_session_id=target_study_session_id,
            front=normalized_front,
            back=normalized_back,
            algorithm=algorithm,
            tags=self._normalize_tags(tags),
        )
        card = Card(**payload.model_dump(mode="json"))
        self.session.add(card)
        self.session.flush()
        return card

    def review_card(self, *, card_id: int, rating: ReviewRating) -> ReviewResult:
        card = self.session.get(Card, card_id)
        if card is None:
            raise FlashcardServiceError("Flashcard nao encontrado.")
        if card.status in {CardStatus.SUSPENDED.value, CardStatus.BURIED.value}:
            raise FlashcardServiceError("Esse flashcard nao pode ser revisado no estado atual.")

        result = self.srs.review(card=card, rating=rating)
        self.session.add(card)
        self.session.flush()
        return result

    def review_metrics(self, today: date | None = None) -> dict[str, int]:
        now = self.now_utc()
        target_day = today or now.date()
        start, end = self.day_bounds(target_day)

        new_cards = int(
            self.session.scalar(
                select(func.count(Card.id))
                .join(Topic, Topic.id == Card.topic_id)
                .where(Topic.is_active.is_(True), Card.status == CardStatus.NEW.value)
            )
            or 0
        )
        due_cards = int(
            self.session.scalar(
                select(func.count(Card.id))
                .join(Topic, Topic.id == Card.topic_id)
                .outerjoin(CardScheduleState, CardScheduleState.card_id == Card.id)
                .where(
                    Topic.is_active.is_(True),
                    Card.status == CardStatus.ACTIVE.value,
                    CardScheduleState.due_at.is_not(None),
                    CardScheduleState.due_at <= now,
                )
            )
            or 0
        )
        reviewed_today = int(
            self.session.scalar(
                select(func.count(CardReview.id)).where(
                    CardReview.reviewed_at >= start,
                    CardReview.reviewed_at < end,
                )
            )
            or 0
        )
        return {
            "new_cards": new_cards,
            "due_cards": due_cards,
            "reviewed_today": reviewed_today,
        }

    def list_recent_study_sessions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(
                StudySession.id,
                StudySession.content_summary,
                StudySession.started_at,
                Topic.id.label("topic_id"),
                Topic.name.label("topic"),
                Subject.name.label("subject"),
            )
            .join(Topic, Topic.id == StudySession.topic_id)
            .join(Subject, Subject.id == Topic.subject_id)
            .where(StudySession.content_summary.is_not(None))
            .order_by(StudySession.started_at.desc(), StudySession.id.desc())
            .limit(max(int(limit), 1))
        ).all()
        return [
            {
                "id": int(row.id),
                "topic_id": int(row.topic_id),
                "subject": str(row.subject),
                "topic": str(row.topic),
                "started_at": row.started_at,
                "content_summary": str(row.content_summary),
                "label": (
                    f"{row.subject} / {row.topic} | "
                    f"{self._truncate_text(str(row.content_summary), limit=72)}"
                ),
            }
            for row in rows
        ]

    def list_review_queue(self, *, limit: int = 100) -> list[dict[str, Any]]:
        now = self.now_utc()
        rows = self.session.execute(
            select(
                Card.id,
                Card.front,
                Card.back,
                Card.algorithm,
                Card.status,
                Card.tags,
                Card.study_session_id,
                Topic.name.label("topic"),
                Subject.name.label("subject"),
                StudySession.content_summary,
                CardScheduleState.due_at,
                CardScheduleState.reps,
                CardScheduleState.lapses,
            )
            .join(Topic, Topic.id == Card.topic_id)
            .join(Subject, Subject.id == Topic.subject_id)
            .outerjoin(StudySession, StudySession.id == Card.study_session_id)
            .outerjoin(CardScheduleState, CardScheduleState.card_id == Card.id)
            .where(Topic.is_active.is_(True))
            .where(Card.status.in_([CardStatus.NEW.value, CardStatus.ACTIVE.value]))
            .where(
                or_(
                    Card.status == CardStatus.NEW.value,
                    CardScheduleState.due_at.is_(None),
                    CardScheduleState.due_at <= now,
                )
            )
            .order_by(CardScheduleState.due_at.is_(None), CardScheduleState.due_at, Card.created_at, Card.id)
            .limit(max(int(limit), 1))
        ).all()
        return [
            {
                "id": int(row.id),
                "front": str(row.front),
                "back": str(row.back),
                "algorithm": str(row.algorithm),
                "status": str(row.status),
                "tags": list(row.tags or []),
                "study_session_id": int(row.study_session_id) if row.study_session_id is not None else None,
                "subject": str(row.subject),
                "topic": str(row.topic),
                "content_summary": row.content_summary,
                "due_at": row.due_at,
                "reps": int(row.reps or 0),
                "lapses": int(row.lapses or 0),
            }
            for row in rows
        ]

    def list_cards(self, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(
                Card.id,
                Card.front,
                Card.back,
                Card.algorithm,
                Card.status,
                Card.tags,
                Card.study_session_id,
                Topic.name.label("topic"),
                Subject.name.label("subject"),
                StudySession.content_summary,
                CardScheduleState.due_at,
                CardScheduleState.reps,
                CardScheduleState.lapses,
            )
            .join(Topic, Topic.id == Card.topic_id)
            .join(Subject, Subject.id == Topic.subject_id)
            .outerjoin(StudySession, StudySession.id == Card.study_session_id)
            .outerjoin(CardScheduleState, CardScheduleState.card_id == Card.id)
            .order_by(Subject.name, Topic.name, Card.created_at.desc(), Card.id.desc())
            .limit(max(int(limit), 1))
        ).all()
        return [
            {
                "id": int(row.id),
                "subject": str(row.subject),
                "topic": str(row.topic),
                "front": str(row.front),
                "back": str(row.back),
                "algorithm": str(row.algorithm),
                "status": str(row.status),
                "tags": list(row.tags or []),
                "study_session_id": int(row.study_session_id) if row.study_session_id is not None else None,
                "content_summary": row.content_summary,
                "due_at": row.due_at,
                "reps": int(row.reps or 0),
                "lapses": int(row.lapses or 0),
            }
            for row in rows
        ]

    @staticmethod
    def _normalize_tags(tags: str | list[str] | None) -> list[str] | None:
        if tags is None:
            return None
        if isinstance(tags, str):
            raw_items = tags.replace("\n", ",").split(",")
        else:
            raw_items = tags

        normalized: list[str] = []
        seen: set[str] = set()
        for raw_item in raw_items:
            item = str(raw_item).strip()
            if not item:
                continue
            key = item.casefold()
            if key in seen:
                continue
            normalized.append(item)
            seen.add(key)
        return normalized or None

    @staticmethod
    def _truncate_text(value: str, *, limit: int = 96) -> str:
        normalized = " ".join(str(value).split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: max(limit - 3, 1)].rstrip()}..."
