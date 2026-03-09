from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from editalos.models import (
    CardReview,
    HydrationLog,
    NutritionLog,
    QuestionAttempt,
    SleepLog,
    StudySession,
    Subject,
    Topic,
)


class AnalyticsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def study_by_hour(self) -> pd.DataFrame:
        stmt = (
            select(
                func.strftime("%H", StudySession.started_at).label("hour"),
                func.count(StudySession.id).label("sessions"),
                func.sum(StudySession.actual_minutes).label("minutes"),
                func.avg(StudySession.accuracy).label("avg_accuracy"),
                func.avg(StudySession.focus_pre).label("avg_focus"),
            )
            .group_by("hour")
            .order_by("hour")
        )
        rows = self.session.execute(stmt).all()
        return pd.DataFrame(rows, columns=["hour", "sessions", "minutes", "avg_accuracy", "avg_focus"])

    def accuracy_by_subject(self) -> pd.DataFrame:
        stmt = (
            select(
                Subject.name.label("subject"),
                func.count(QuestionAttempt.id).label("attempts"),
                func.avg(case((QuestionAttempt.is_correct.is_(True), 1.0), else_=0.0)).label("accuracy"),
            )
            .join(Topic, Topic.subject_id == Subject.id)
            .join(QuestionAttempt, QuestionAttempt.topic_id == Topic.id)
            .group_by(Subject.name)
            .order_by(Subject.name)
        )
        rows = self.session.execute(stmt).all()
        return pd.DataFrame(rows, columns=["subject", "attempts", "accuracy"])

    def review_load(self, days: int = 14) -> pd.DataFrame:
        since = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(
                func.date(CardReview.reviewed_at).label("date"),
                func.count(CardReview.id).label("reviews"),
                func.avg(case((CardReview.was_correct.is_(True), 1.0), else_=0.0)).label("success_rate"),
            )
            .where(CardReview.reviewed_at >= since)
            .group_by("date")
            .order_by("date")
        )
        rows = self.session.execute(stmt).all()
        return pd.DataFrame(rows, columns=["date", "reviews", "success_rate"])

    def simple_biohacking_snapshot(self) -> dict[str, float | None]:
        now = datetime.now(UTC)
        since = now - timedelta(days=7)

        sleep_stmt = select(func.avg(SleepLog.duration_hours)).where(SleepLog.sleep_start >= since)
        hydration_stmt = select(func.avg(HydrationLog.volume_ml)).where(HydrationLog.consumed_at >= since)
        study_stmt = select(func.avg(StudySession.actual_minutes)).where(StudySession.started_at >= since)
        nutrition_stmt = select(func.avg(NutritionLog.protein_grams)).where(NutritionLog.consumed_at >= since)

        return {
            "avg_sleep_hours_7d": self.session.scalar(sleep_stmt),
            "avg_hydration_ml_entry_7d": self.session.scalar(hydration_stmt),
            "avg_study_minutes_session_7d": self.session.scalar(study_stmt),
            "avg_protein_g_entry_7d": self.session.scalar(nutrition_stmt),
        }
