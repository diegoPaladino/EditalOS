from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from editalos.enums import ReviewTaskStatus, StudySessionRunStatus
from editalos.models import ReviewTask, StudySession, StudySessionRun, Subject, Topic, TopicProgress
from editalos.services.planner import PlannerService

REVIEW_INTERVAL_DAYS = (1, 7, 15, 30)


class StudyExecutionError(Exception):
    pass


@dataclass(slots=True)
class SessionFinishResult:
    run_id: int
    study_session_id: int
    gross_seconds: int
    net_seconds: int
    generated_reviews: int


class StudyExecutionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def day_bounds(reference_date: date) -> tuple[datetime, datetime]:
        start = datetime.combine(reference_date, time.min, tzinfo=UTC)
        end = start + timedelta(days=1)
        return start, end

    @staticmethod
    def _elapsed_seconds(start: datetime, end: datetime) -> int:
        return max(int((end - start).total_seconds()), 0)

    def list_study_topics(self) -> list[dict[str, Any]]:
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

    def get_active_session(self) -> StudySessionRun | None:
        return self.session.scalars(
            select(StudySessionRun)
            .where(
                StudySessionRun.status.in_(
                    [StudySessionRunStatus.IN_PROGRESS.value, StudySessionRunStatus.PAUSED.value]
                )
            )
            .order_by(StudySessionRun.started_at.desc())
        ).first()

    def session_snapshot(self, run: StudySessionRun | None = None) -> dict[str, Any] | None:
        target = run or self.get_active_session()
        if target is None:
            return None

        now = self.now_utc()
        gross_seconds, net_seconds, paused_seconds = self._runtime_seconds(target, now)
        topic_row = self.session.execute(
            select(Topic.name.label("topic"), Subject.name.label("subject"))
            .join(Subject, Subject.id == Topic.subject_id)
            .where(Topic.id == target.topic_id)
        ).first()

        return {
            "run_id": target.id,
            "status": target.status,
            "topic_id": target.topic_id,
            "topic": topic_row.topic if topic_row else f"Topico {target.topic_id}",
            "subject": topic_row.subject if topic_row else "Disciplina nao encontrada",
            "started_at": target.started_at,
            "gross_seconds": gross_seconds,
            "net_seconds": net_seconds,
            "paused_seconds": paused_seconds,
        }

    def start_session(self, topic_id: int) -> StudySessionRun:
        if self.get_active_session() is not None:
            raise StudyExecutionError("Ja existe uma sessao em andamento. Finalize ou pause/retome a sessao atual.")

        topic = self.session.get(Topic, topic_id)
        if topic is None or not topic.is_active:
            raise StudyExecutionError("Topico invalido para iniciar sessao.")

        now = self.now_utc()
        run = StudySessionRun(
            topic_id=topic.id,
            subject_id=topic.subject_id,
            status=StudySessionRunStatus.IN_PROGRESS.value,
            started_at=now,
            last_resumed_at=now,
            paused_at=None,
            accumulated_active_seconds=0,
            total_paused_seconds=0,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def pause_session(self) -> StudySessionRun:
        run = self._require_active_session(expected_status=StudySessionRunStatus.IN_PROGRESS.value)
        if run.last_resumed_at is None:
            raise StudyExecutionError("A sessao nao pode ser pausada no estado atual.")

        now = self.now_utc()
        run.accumulated_active_seconds += self._elapsed_seconds(run.last_resumed_at, now)
        run.last_resumed_at = None
        run.paused_at = now
        run.status = StudySessionRunStatus.PAUSED.value
        self.session.flush()
        return run

    def resume_session(self) -> StudySessionRun:
        run = self._require_active_session(expected_status=StudySessionRunStatus.PAUSED.value)
        if run.paused_at is None:
            raise StudyExecutionError("A sessao nao pode ser retomada no estado atual.")

        now = self.now_utc()
        run.total_paused_seconds += self._elapsed_seconds(run.paused_at, now)
        run.paused_at = None
        run.last_resumed_at = now
        run.status = StudySessionRunStatus.IN_PROGRESS.value
        self.session.flush()
        return run

    def finish_session(self) -> SessionFinishResult:
        run = self.get_active_session()
        if run is None:
            raise StudyExecutionError("Nao existe sessao ativa para finalizar.")

        now = self.now_utc()
        if run.status == StudySessionRunStatus.IN_PROGRESS.value and run.last_resumed_at is not None:
            run.accumulated_active_seconds += self._elapsed_seconds(run.last_resumed_at, now)
            run.last_resumed_at = None
        elif run.status == StudySessionRunStatus.PAUSED.value and run.paused_at is not None:
            run.total_paused_seconds += self._elapsed_seconds(run.paused_at, now)
            run.paused_at = None

        gross_seconds = max(self._elapsed_seconds(run.started_at, now), 1)
        net_seconds = max(min(run.accumulated_active_seconds, gross_seconds), 1)
        actual_minutes = max(net_seconds // 60, 1)

        study_session = StudySession(
            subject_id=run.subject_id,
            topic_id=run.topic_id,
            started_at=run.started_at,
            ended_at=now,
            actual_minutes=actual_minutes,
            notes=f"session_run_id={run.id};gross_seconds={gross_seconds};net_seconds={net_seconds}",
        )
        self.session.add(study_session)
        self.session.flush()

        run.status = StudySessionRunStatus.FINISHED.value
        run.ended_at = now
        run.gross_seconds = gross_seconds
        run.net_seconds = net_seconds
        run.study_session_id = study_session.id

        generated_reviews = self._generate_review_tasks(
            topic_id=run.topic_id,
            study_session_id=study_session.id,
            run_id=run.id,
            reference_time=now,
        )
        self._register_topic_study(
            topic_id=run.topic_id,
            gross_seconds=gross_seconds,
            net_seconds=net_seconds,
            studied_at=now,
        )
        self.session.flush()

        return SessionFinishResult(
            run_id=run.id,
            study_session_id=study_session.id,
            gross_seconds=gross_seconds,
            net_seconds=net_seconds,
            generated_reviews=generated_reviews,
        )

    def sync_review_statuses(self, reference_time: datetime | None = None) -> int:
        now = reference_time or self.now_utc()
        tasks = self.session.scalars(
            select(ReviewTask).where(
                ReviewTask.status == ReviewTaskStatus.PENDING.value,
                ReviewTask.due_at < now,
            )
        ).all()
        for task in tasks:
            task.status = ReviewTaskStatus.OVERDUE.value
        return len(tasks)

    def list_due_reviews_today(self, today: date | None = None) -> list[dict[str, Any]]:
        self.sync_review_statuses()
        target_day = today or self.now_utc().date()
        _, day_end = self.day_bounds(target_day)
        rows = self.session.execute(
            select(
                ReviewTask.id,
                Subject.name.label("subject"),
                Topic.name.label("topic"),
                ReviewTask.due_at,
                ReviewTask.status,
            )
            .join(Topic, Topic.id == ReviewTask.topic_id)
            .join(Subject, Subject.id == Topic.subject_id)
            .where(ReviewTask.status.in_([ReviewTaskStatus.PENDING.value, ReviewTaskStatus.OVERDUE.value]))
            .where(ReviewTask.due_at < day_end)
            .order_by(ReviewTask.due_at, Subject.name, Topic.name)
        ).all()
        return [self._review_row_to_dict(row) for row in rows]

    def list_overdue_reviews(self) -> list[dict[str, Any]]:
        self.sync_review_statuses()
        rows = self.session.execute(
            select(
                ReviewTask.id,
                Subject.name.label("subject"),
                Topic.name.label("topic"),
                ReviewTask.due_at,
                ReviewTask.status,
            )
            .join(Topic, Topic.id == ReviewTask.topic_id)
            .join(Subject, Subject.id == Topic.subject_id)
            .where(ReviewTask.status == ReviewTaskStatus.OVERDUE.value)
            .order_by(ReviewTask.due_at, Subject.name, Topic.name)
        ).all()
        return [self._review_row_to_dict(row) for row in rows]

    def list_upcoming_reviews(self, days_ahead: int = 30, today: date | None = None) -> list[dict[str, Any]]:
        self.sync_review_statuses()
        target_day = today or self.now_utc().date()
        _, day_end = self.day_bounds(target_day)
        future_end = day_end + timedelta(days=max(days_ahead, 1))
        rows = self.session.execute(
            select(
                ReviewTask.id,
                Subject.name.label("subject"),
                Topic.name.label("topic"),
                ReviewTask.due_at,
                ReviewTask.status,
            )
            .join(Topic, Topic.id == ReviewTask.topic_id)
            .join(Subject, Subject.id == Topic.subject_id)
            .where(ReviewTask.status == ReviewTaskStatus.PENDING.value)
            .where(ReviewTask.due_at >= day_end)
            .where(ReviewTask.due_at < future_end)
            .order_by(ReviewTask.due_at, Subject.name, Topic.name)
        ).all()
        return [self._review_row_to_dict(row) for row in rows]

    def mark_review_completed(self, review_task_id: int) -> ReviewTask:
        task = self.session.get(ReviewTask, review_task_id)
        if task is None:
            raise StudyExecutionError("Revisao nao encontrada.")
        if task.status == ReviewTaskStatus.COMPLETED.value:
            raise StudyExecutionError("Essa revisao ja foi concluida.")

        now = self.now_utc()
        task.status = ReviewTaskStatus.COMPLETED.value
        task.completed_at = now
        self._register_review_completion(topic_id=task.topic_id, completed_at=now)
        self.session.flush()
        return task

    def today_operational_metrics(self, today: date | None = None) -> dict[str, int]:
        target_day = today or self.now_utc().date()
        start, end = self.day_bounds(target_day)
        studied_minutes = int(
            self.session.scalar(
                select(func.coalesce(func.sum(StudySession.actual_minutes), 0)).where(
                    StudySession.started_at >= start,
                    StudySession.started_at < end,
                )
            )
            or 0
        )
        completed_reviews = int(
            self.session.scalar(
                select(func.count(ReviewTask.id)).where(
                    ReviewTask.status == ReviewTaskStatus.COMPLETED.value,
                    ReviewTask.completed_at.is_not(None),
                    ReviewTask.completed_at >= start,
                    ReviewTask.completed_at < end,
                )
            )
            or 0
        )
        return {
            "studied_minutes": studied_minutes,
            "completed_reviews": completed_reviews,
        }

    def today_study_plan(self, total_minutes: int) -> list[dict[str, Any]]:
        planner = PlannerService(self.session)
        plan = planner.build_daily_plan(total_minutes=total_minutes)
        return [item.model_dump() for item in plan]

    def _require_active_session(self, expected_status: str) -> StudySessionRun:
        run = self.get_active_session()
        if run is None:
            raise StudyExecutionError("Nao existe sessao ativa.")
        if run.status != expected_status:
            raise StudyExecutionError("Acao invalida para o estado atual da sessao.")
        return run

    def _runtime_seconds(self, run: StudySessionRun, reference_time: datetime) -> tuple[int, int, int]:
        gross_seconds = max(self._elapsed_seconds(run.started_at, reference_time), 0)
        net_seconds = max(run.accumulated_active_seconds, 0)
        paused_seconds = max(run.total_paused_seconds, 0)

        if run.status == StudySessionRunStatus.IN_PROGRESS.value and run.last_resumed_at is not None:
            net_seconds += self._elapsed_seconds(run.last_resumed_at, reference_time)
        if run.status == StudySessionRunStatus.PAUSED.value and run.paused_at is not None:
            paused_seconds += self._elapsed_seconds(run.paused_at, reference_time)
        if run.status == StudySessionRunStatus.FINISHED.value:
            net_seconds = run.net_seconds or net_seconds
            gross_seconds = run.gross_seconds or gross_seconds

        net_seconds = min(net_seconds, gross_seconds)
        return gross_seconds, net_seconds, paused_seconds

    def _generate_review_tasks(
        self,
        *,
        topic_id: int,
        study_session_id: int,
        run_id: int,
        reference_time: datetime,
    ) -> int:
        created = 0
        for days in REVIEW_INTERVAL_DAYS:
            self.session.add(
                ReviewTask(
                    topic_id=topic_id,
                    study_session_id=study_session_id,
                    study_session_run_id=run_id,
                    due_at=reference_time + timedelta(days=days),
                    status=ReviewTaskStatus.PENDING.value,
                )
            )
            created += 1
        return created

    def _register_topic_study(
        self,
        *,
        topic_id: int,
        gross_seconds: int,
        net_seconds: int,
        studied_at: datetime,
    ) -> None:
        progress = self._get_or_create_progress(topic_id)
        progress.total_sessions += 1
        progress.total_gross_seconds += gross_seconds
        progress.total_net_seconds += net_seconds
        progress.last_studied_at = studied_at

    def _register_review_completion(self, *, topic_id: int, completed_at: datetime) -> None:
        progress = self._get_or_create_progress(topic_id)
        progress.total_reviews_completed += 1
        progress.last_review_completed_at = completed_at

    def _get_or_create_progress(self, topic_id: int) -> TopicProgress:
        progress = self.session.scalars(select(TopicProgress).where(TopicProgress.topic_id == topic_id)).first()
        if progress is not None:
            return progress

        progress = TopicProgress(topic_id=topic_id)
        self.session.add(progress)
        self.session.flush()
        return progress

    @staticmethod
    def _review_row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "id": int(row.id),
            "subject": str(row.subject),
            "topic": str(row.topic),
            "due_at": row.due_at,
            "status": str(row.status),
        }
