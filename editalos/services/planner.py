from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import ceil

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from editalos.models import Card, CardScheduleState, QuestionAttempt, StudySession, Subject, Topic
from editalos.schemas import PlannerWeights, TopicPriority


@dataclass(slots=True)
class PlannerConfig:
    exam_date: datetime | None = None
    weights: PlannerWeights = field(default_factory=PlannerWeights)


class PlannerService:
    def __init__(self, session: Session, config: PlannerConfig | None = None) -> None:
        self.session = session
        self.config = config or PlannerConfig()

    def build_daily_plan(self, total_minutes: int) -> list[TopicPriority]:
        topics = self.session.scalars(
            select(Topic).join(Subject).where(Topic.is_active.is_(True)).order_by(Subject.name, Topic.name)
        ).all()
        if not topics:
            return []

        scored: list[TopicPriority] = []
        for topic in topics:
            score, rationale = self._score_topic(topic)
            minutes = self._minutes_from_score(score, total_minutes, len(topics))
            scored.append(
                TopicPriority(
                    topic_id=topic.id,
                    subject_name=topic.subject.name,
                    topic_name=topic.name,
                    priority_score=round(score, 4),
                    recommended_minutes=minutes,
                    rationale={k: round(v, 4) for k, v in rationale.items()},
                )
            )

        scored.sort(key=lambda item: item.priority_score, reverse=True)
        return self._rebalance_minutes(scored, total_minutes)

    def _score_topic(self, topic: Topic) -> tuple[float, dict[str, float]]:
        edital_weight = topic.subject.weight * topic.weight
        incidence = max(topic.incidence_estimate, 0.05)
        deficiency = self._topic_deficiency(topic.id)
        forgetting = self._topic_forgetting_risk(topic.id)
        proximity = self._exam_proximity_multiplier()
        recent_errors = self._recent_error_multiplier(topic.id)

        weights = self.config.weights
        score = (
            (edital_weight * weights.edital_weight_factor)
            * (incidence * weights.incidence_factor)
            * (deficiency * weights.deficiency_factor)
            * (forgetting * weights.forgetting_factor)
            * (proximity * weights.exam_proximity_factor)
            * (recent_errors * weights.recent_errors_factor)
        )
        rationale = {
            "edital_weight": edital_weight,
            "incidence": incidence,
            "deficiency": deficiency,
            "forgetting": forgetting,
            "proximity": proximity,
            "recent_errors": recent_errors,
        }
        return score, rationale

    def _topic_deficiency(self, topic_id: int) -> float:
        accuracy_stmt = (
            select(func.avg(case((QuestionAttempt.is_correct.is_(True), 1.0), else_=0.0)))
            .where(QuestionAttempt.topic_id == topic_id)
        )
        accuracy = self.session.scalar(accuracy_stmt)
        if accuracy is None:
            return 1.0
        return max(1.0 - float(accuracy), 0.15)

    def _topic_forgetting_risk(self, topic_id: int) -> float:
        now = datetime.now(UTC)
        due_stmt = (
            select(func.count(CardScheduleState.id))
            .join(Card)
            .where(Card.topic_id == topic_id)
            .where(CardScheduleState.due_at.is_not(None))
            .where(CardScheduleState.due_at <= now)
        )
        due_count = int(self.session.scalar(due_stmt) or 0)

        total_stmt = select(func.count(Card.id)).where(Card.topic_id == topic_id)
        total_count = int(self.session.scalar(total_stmt) or 0)
        if total_count == 0:
            return 0.6
        overdue_ratio = due_count / max(total_count, 1)
        return max(0.5 + overdue_ratio, 0.2)

    def _recent_error_multiplier(self, topic_id: int) -> float:
        since = datetime.now(UTC) - timedelta(days=7)
        recent_stmt = (
            select(func.avg(case((QuestionAttempt.is_correct.is_(True), 1.0), else_=0.0)))
            .where(QuestionAttempt.topic_id == topic_id)
            .where(QuestionAttempt.attempted_at >= since)
        )
        recent_accuracy = self.session.scalar(recent_stmt)
        if recent_accuracy is None:
            return 1.0
        return max(1.0 + (0.7 - float(recent_accuracy)), 0.8)

    def _exam_proximity_multiplier(self) -> float:
        if self.config.exam_date is None:
            return 1.0
        delta_days = max((self.config.exam_date - datetime.now(UTC)).days, 1)
        return min(2.0, 1.0 + (60 / delta_days) * 0.25)

    @staticmethod
    def _minutes_from_score(score: float, total_minutes: int, topic_count: int) -> int:
        baseline = max(total_minutes // max(topic_count, 1), 20)
        return max(20, ceil(baseline * max(score, 0.5)))

    @staticmethod
    def _rebalance_minutes(items: list[TopicPriority], total_minutes: int) -> list[TopicPriority]:
        if not items:
            return items
        raw_total = sum(item.recommended_minutes for item in items)
        if raw_total <= 0:
            even = max(total_minutes // len(items), 20)
            for item in items:
                item.recommended_minutes = even
            return items

        for item in items:
            item.recommended_minutes = max(20, round((item.recommended_minutes / raw_total) * total_minutes))

        diff = total_minutes - sum(item.recommended_minutes for item in items)
        idx = 0
        while diff != 0 and items:
            item = items[idx % len(items)]
            if diff > 0:
                item.recommended_minutes += 1
                diff -= 1
            elif item.recommended_minutes > 20:
                item.recommended_minutes -= 1
                diff += 1
            idx += 1
        return items

    def recent_study_heatmap(self) -> list[dict[str, int | str]]:
        stmt = (
            select(
                func.strftime("%H", StudySession.started_at).label("hour"),
                func.count(StudySession.id).label("sessions"),
                func.sum(StudySession.actual_minutes).label("minutes"),
            )
            .group_by("hour")
            .order_by("hour")
        )
        rows = self.session.execute(stmt).all()
        return [
            {"hour": row.hour, "sessions": int(row.sessions or 0), "minutes": int(row.minutes or 0)}
            for row in rows
        ]
