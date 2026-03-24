from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

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
from editalos.services.study_execution import StudyExecutionService


class AnalyticsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _normalize_summary(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _study_sessions_dataframe(
        self,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> pd.DataFrame:
        stmt = (
            select(
                StudySession.id.label("session_id"),
                StudySession.started_at,
                StudySession.ended_at,
                StudySession.actual_minutes,
                StudySession.content_summary,
                Subject.name.label("subject"),
                Topic.name.label("topic"),
            )
            .outerjoin(Subject, Subject.id == StudySession.subject_id)
            .outerjoin(Topic, Topic.id == StudySession.topic_id)
            .order_by(StudySession.started_at, StudySession.id)
        )
        if start_at is not None:
            stmt = stmt.where(StudySession.started_at >= start_at)
        if end_at is not None:
            stmt = stmt.where(StudySession.started_at < end_at)

        rows = self.session.execute(stmt).all()
        if not rows:
            return pd.DataFrame(
                columns=[
                    "session_id",
                    "date",
                    "started_at",
                    "ended_at",
                    "actual_minutes",
                    "content_summary",
                    "subject",
                    "topic",
                ]
            )

        normalized_rows = []
        for row in rows:
            local_start = StudyExecutionService.to_local(row.started_at)
            local_end = StudyExecutionService.to_local(row.ended_at)
            if local_start is None or local_end is None:
                continue
            normalized_rows.append(
                {
                    "session_id": int(row.session_id),
                    "date": local_start.date(),
                    "started_at": local_start,
                    "ended_at": local_end,
                    "actual_minutes": int(row.actual_minutes or 0),
                    "content_summary": self._normalize_summary(row.content_summary),
                    "subject": str(row.subject or "Sem disciplina"),
                    "topic": str(row.topic or "Sem topico"),
                }
            )

        return pd.DataFrame(normalized_rows)

    def study_daily_summary(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        days: int = 30,
        reference_date: date | None = None,
    ) -> pd.DataFrame:
        safe_days = max(int(days), 1)
        target_end = end_date or reference_date or StudyExecutionService.local_today()
        target_start = start_date or (target_end - timedelta(days=safe_days - 1))
        if target_start > target_end:
            target_start, target_end = target_end, target_start

        start_at, _ = StudyExecutionService.day_bounds(target_start)
        _, end_at = StudyExecutionService.day_bounds(target_end)
        sessions_df = self._study_sessions_dataframe(start_at=start_at, end_at=end_at)

        full_range = pd.DataFrame({"date": list(pd.date_range(start=target_start, end=target_end, freq="D").date)})
        if sessions_df.empty:
            full_range["sessions"] = 0
            full_range["minutes"] = 0
            full_range["subjects_count"] = 0
            full_range["topics_count"] = 0
        else:
            grouped = (
                sessions_df.groupby("date", as_index=False)
                .agg(
                    sessions=("session_id", "count"),
                    minutes=("actual_minutes", "sum"),
                    subjects_count=("subject", "nunique"),
                    topics_count=("topic", "nunique"),
                )
                .sort_values("date")
            )
            full_range = full_range.merge(grouped, on="date", how="left").fillna(0)

        for column in ("sessions", "minutes", "subjects_count", "topics_count"):
            full_range[column] = full_range[column].astype(int)
        full_range["hours"] = full_range["minutes"] / 60.0
        full_range["label"] = pd.to_datetime(full_range["date"]).dt.strftime("%d/%m")
        full_range["is_studied"] = full_range["minutes"] > 0
        return full_range

    def study_day_detail(self, target_date: date) -> dict[str, Any]:
        start_at, end_at = StudyExecutionService.day_bounds(target_date)
        sessions_df = self._study_sessions_dataframe(start_at=start_at, end_at=end_at)
        if sessions_df.empty:
            return {
                "date": target_date,
                "total_minutes": 0,
                "total_hours": 0.0,
                "sessions_count": 0,
                "subjects_count": 0,
                "topics_count": 0,
                "first_started_at": None,
                "last_ended_at": None,
                "sessions": [],
                "subjects": [],
            }

        sessions_df = sessions_df.sort_values(["started_at", "session_id"]).reset_index(drop=True)
        subject_summary = (
            sessions_df.groupby("subject", as_index=False)
            .agg(
                minutes=("actual_minutes", "sum"),
                sessions=("session_id", "count"),
                topics_count=("topic", "nunique"),
            )
            .sort_values(["minutes", "sessions", "subject"], ascending=[False, False, True])
        )

        return {
            "date": target_date,
            "total_minutes": int(sessions_df["actual_minutes"].sum()),
            "total_hours": float(sessions_df["actual_minutes"].sum() / 60.0),
            "sessions_count": int(len(sessions_df.index)),
            "subjects_count": int(sessions_df["subject"].nunique()),
            "topics_count": int(sessions_df["topic"].nunique()),
            "first_started_at": sessions_df.iloc[0]["started_at"],
            "last_ended_at": sessions_df.iloc[-1]["ended_at"],
            "sessions": sessions_df.to_dict("records"),
            "subjects": subject_summary.to_dict("records"),
        }

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
