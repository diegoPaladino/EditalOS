from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import ceil

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from editalos.enums import ReviewTaskStatus
from editalos.models import QuestionAttempt, ReviewTask, StudySession, Subject, Topic, TopicProgress
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
        time_balance = self._subject_time_balance_multiplier(topic.subject_id)

        weights = self.config.weights
        score = (
            (edital_weight * weights.edital_weight_factor)
            * (incidence * weights.incidence_factor)
            * (deficiency * weights.deficiency_factor)
            * (forgetting * weights.forgetting_factor)
            * (proximity * weights.exam_proximity_factor)
            * (recent_errors * weights.recent_errors_factor)
            * time_balance
        )
        rationale = {
            "edital_weight": edital_weight,
            "incidence": incidence,
            "deficiency": deficiency,
            "forgetting": forgetting,
            "proximity": proximity,
            "recent_errors": recent_errors,
            "time_balance": time_balance,
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
        due_stmt = select(func.count(ReviewTask.id)).where(
            ReviewTask.topic_id == topic_id,
            ReviewTask.status.in_([ReviewTaskStatus.PENDING.value, ReviewTaskStatus.OVERDUE.value]),
            ReviewTask.due_at <= now,
        )
        due_count = int(self.session.scalar(due_stmt) or 0)

        total_stmt = select(func.count(ReviewTask.id)).where(ReviewTask.topic_id == topic_id)
        total_count = int(self.session.scalar(total_stmt) or 0)
        if total_count > 0:
            overdue_ratio = due_count / max(total_count, 1)
            return max(0.6 + overdue_ratio, 0.25)

        progress = self.session.scalars(select(TopicProgress).where(TopicProgress.topic_id == topic_id)).first()
        if progress is None or progress.last_studied_at is None:
            return 0.6

        days_since_study = max((now - progress.last_studied_at).days, 0)
        return min(1.4, 0.6 + (days_since_study * 0.04))

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

    def _subject_time_balance_multiplier(self, subject_id: int) -> float:
        subject = self.session.get(Subject, subject_id)
        if subject is None or not subject.planned_weekly_minutes or subject.planned_weekly_minutes <= 0:
            return 1.0

        since = datetime.now(UTC) - timedelta(days=7)
        studied_minutes = int(
            self.session.scalar(
                select(func.coalesce(func.sum(StudySession.actual_minutes), 0)).where(
                    StudySession.subject_id == subject_id,
                    StudySession.started_at >= since,
                )
            )
            or 0
        )
        target = max(int(subject.planned_weekly_minutes), 1)
        ratio = studied_minutes / target
        if ratio >= 1.0:
            return max(0.85, 1.0 - min((ratio - 1.0) * 0.15, 0.15))
        return min(1.35, 1.0 + ((1.0 - ratio) * 0.35))

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
