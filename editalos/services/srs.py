from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from fsrs import Card as FSRSCard
from fsrs import Rating as FSRSRating
from fsrs import Scheduler as FSRSScheduler
from sm_2 import Card as SM2Card
from sm_2 import Scheduler as SM2Scheduler

from editalos.config import get_settings
from editalos.enums import ReviewRating, SRSAlgorithm
from editalos.models import Card, CardReview, CardScheduleState
from editalos.schemas import ReviewResult


settings = get_settings()


def _serialize_obj(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return obj.to_dict()
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        return obj.model_dump()
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "__dict__"):
        data: dict[str, Any] = {}
        for key, value in vars(obj).items():
            if key.startswith("_"):
                continue
            if isinstance(value, datetime):
                data[key] = value.isoformat()
            else:
                data[key] = value
        return data
    raise TypeError(f"Cannot serialize object of type {type(obj)!r}")


class SRSService:
    def __init__(self) -> None:
        self.fsrs_scheduler = FSRSScheduler(desired_retention=settings.fsrs_desired_retention)
        self.sm2_scheduler = SM2Scheduler()

    @staticmethod
    def _to_utc(dt: datetime | None = None) -> datetime:
        dt = dt or datetime.now(UTC)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    @staticmethod
    def _rating_to_fsrs(rating: ReviewRating) -> FSRSRating:
        return {
            ReviewRating.AGAIN: FSRSRating.Again,
            ReviewRating.HARD: FSRSRating.Hard,
            ReviewRating.GOOD: FSRSRating.Good,
            ReviewRating.EASY: FSRSRating.Easy,
        }[rating]

    @staticmethod
    def _rating_to_sm2(rating: ReviewRating) -> int:
        return {
            ReviewRating.AGAIN: 1,
            ReviewRating.HARD: 3,
            ReviewRating.GOOD: 4,
            ReviewRating.EASY: 5,
        }[rating]

    @staticmethod
    def _build_fsrs_card(state: dict[str, Any] | None) -> FSRSCard:
        if not state:
            return FSRSCard()
        normalized = dict(state)
        if "due" in normalized and isinstance(normalized["due"], str):
            normalized["due"] = datetime.fromisoformat(normalized["due"])
        return FSRSCard(**normalized)

    @staticmethod
    def _build_sm2_card(state: dict[str, Any] | None) -> SM2Card:
        if not state:
            return SM2Card()
        if hasattr(SM2Card, "from_dict"):
            return SM2Card.from_dict(state)
        normalized = dict(state)
        if "due" in normalized and isinstance(normalized["due"], str):
            normalized["due"] = datetime.fromisoformat(normalized["due"])
        return SM2Card(**normalized)

    def review(self, card: Card, rating: ReviewRating, response_seconds: float | None = None) -> ReviewResult:
        schedule_state = card.schedule_state
        reviewed_at = self._to_utc()

        if card.algorithm == SRSAlgorithm.FSRS.value:
            fsrs_card = self._build_fsrs_card(schedule_state.algorithm_state if schedule_state else None)
            due_before = getattr(fsrs_card, "due", None)
            fsrs_rating = self._rating_to_fsrs(rating)
            reviewed_card, review_log = self.fsrs_scheduler.review_card(
                fsrs_card,
                fsrs_rating,
                review_datetime=reviewed_at,
            )
            payload = {
                "card": _serialize_obj(reviewed_card),
                "review_log": _serialize_obj(review_log),
            }
            retrievability = None
            try:
                retrievability = float(self.fsrs_scheduler.get_card_retrievability(reviewed_card))
            except Exception:
                retrievability = None
            result = ReviewResult(
                card_id=card.id,
                algorithm=SRSAlgorithm.FSRS.value,
                rating=rating.value,
                reviewed_at=reviewed_at,
                due_at=getattr(reviewed_card, "due", None),
                interval_days=float(getattr(reviewed_card, "scheduled_days", 0.0) or 0.0),
                reps=int(getattr(reviewed_card, "reps", 0) or 0),
                lapses=int(getattr(reviewed_card, "lapses", 0) or 0),
                retrievability=retrievability,
                stability=float(getattr(reviewed_card, "stability", 0.0) or 0.0),
                difficulty=float(getattr(reviewed_card, "difficulty", 0.0) or 0.0),
                payload=payload,
            )
            self._persist_review(
                card=card,
                result=result,
                due_before=due_before,
                response_seconds=response_seconds,
            )
            return result

        sm2_card = self._build_sm2_card(schedule_state.algorithm_state if schedule_state else None)
        due_before = getattr(sm2_card, "due", None)
        sm2_rating = self._rating_to_sm2(rating)
        reviewed_card, review_log = self.sm2_scheduler.review_card(
            card=sm2_card,
            rating=sm2_rating,
            review_datetime=reviewed_at,
        )
        payload = {
            "card": _serialize_obj(reviewed_card),
            "review_log": _serialize_obj(review_log),
        }
        interval_days = 0.0
        due_after = getattr(reviewed_card, "due", None)
        if due_after:
            delta = due_after - reviewed_at
            interval_days = max(delta.total_seconds() / 86400.0, 0.0)
        result = ReviewResult(
            card_id=card.id,
            algorithm=SRSAlgorithm.SM2.value,
            rating=rating.value,
            reviewed_at=reviewed_at,
            due_at=due_after,
            interval_days=interval_days,
            reps=int(getattr(reviewed_card, "repetitions", getattr(reviewed_card, "reps", 0)) or 0),
            lapses=int(getattr(reviewed_card, "lapses", 0) or 0),
            retrievability=None,
            stability=None,
            difficulty=float(getattr(reviewed_card, "ease_factor", 0.0) or 0.0),
            payload=payload,
        )
        self._persist_review(
            card=card,
            result=result,
            due_before=due_before,
            response_seconds=response_seconds,
        )
        return result

    @staticmethod
    def _persist_review(
        card: Card,
        result: ReviewResult,
        due_before: datetime | None,
        response_seconds: float | None,
    ) -> None:
        schedule_state = card.schedule_state
        if schedule_state is None:
            schedule_state = CardScheduleState(card=card, algorithm=result.algorithm)
            card.schedule_state = schedule_state
        schedule_state.algorithm = result.algorithm
        schedule_state.due_at = result.due_at
        schedule_state.interval_days = result.interval_days
        schedule_state.retrievability = result.retrievability
        schedule_state.stability = result.stability
        schedule_state.difficulty = result.difficulty
        schedule_state.reps = result.reps
        schedule_state.lapses = result.lapses
        schedule_state.algorithm_state = result.payload.get("card", {})

        review = CardReview(
            card=card,
            reviewed_at=result.reviewed_at,
            rating=result.rating,
            was_correct=result.rating != ReviewRating.AGAIN.value,
            due_before=due_before,
            due_after=result.due_at,
            elapsed_days=None if due_before is None else (result.reviewed_at - due_before).total_seconds() / 86400.0,
            response_seconds=response_seconds,
            review_payload=result.payload,
        )
        card.reviews.append(review)
        card.status = "active"
