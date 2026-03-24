from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from editalos.database import Base
from editalos.enums import (
    CardStatus,
    EntityType,
    ReviewTaskStatus,
    SRSAlgorithm,
    StudyMaterialType,
    StudySessionRunStatus,
)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Subject(Base, TimestampMixin):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    question_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    planned_total_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    planned_weekly_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    topics: Mapped[list[Topic]] = relationship(back_populates="subject", cascade="all, delete-orphan")
    study_sessions: Mapped[list[StudySession]] = relationship(back_populates="subject")


class Topic(Base, TimestampMixin):
    __tablename__ = "topics"
    __table_args__ = (UniqueConstraint("subject_id", "name", name="uq_topic_subject_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), nullable=False, index=True)
    parent_topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    difficulty_baseline: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    incidence_estimate: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    subject: Mapped[Subject] = relationship(back_populates="topics")
    parent_topic: Mapped[Topic | None] = relationship(remote_side=[id])
    materials: Mapped[list[StudyMaterial]] = relationship(back_populates="topic")
    cards: Mapped[list[Card]] = relationship(back_populates="topic")
    question_attempts: Mapped[list[QuestionAttempt]] = relationship(back_populates="topic")
    study_sessions: Mapped[list[StudySession]] = relationship(back_populates="topic")


class StudyMaterial(Base, TimestampMixin):
    __tablename__ = "study_materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    material_type: Mapped[str] = mapped_column(String(50), nullable=False, default=StudyMaterialType.PDF.value)
    source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_structure: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    topic: Mapped[Topic | None] = relationship(back_populates="materials")
    embeddings: Mapped[list[EmbeddingVector]] = relationship(back_populates="material")


class Card(Base, TimestampMixin):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    study_session_id: Mapped[int | None] = mapped_column(ForeignKey("study_sessions.id"), nullable=True, index=True)
    front: Mapped[str] = mapped_column(Text, nullable=False)
    back: Mapped[str] = mapped_column(Text, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(20), nullable=False, default=SRSAlgorithm.FSRS.value)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CardStatus.NEW.value)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    topic: Mapped[Topic] = relationship(back_populates="cards")
    schedule_state: Mapped[CardScheduleState | None] = relationship(
        back_populates="card", uselist=False, cascade="all, delete-orphan"
    )
    reviews: Mapped[list[CardReview]] = relationship(back_populates="card", cascade="all, delete-orphan")
    embeddings: Mapped[list[EmbeddingVector]] = relationship(back_populates="card")


class CardScheduleState(Base, TimestampMixin):
    __tablename__ = "card_schedule_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"), nullable=False, unique=True)
    algorithm: Mapped[str] = mapped_column(String(20), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    interval_days: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    stability: Mapped[float | None] = mapped_column(Float, nullable=True)
    difficulty: Mapped[float | None] = mapped_column(Float, nullable=True)
    retrievability: Mapped[float | None] = mapped_column(Float, nullable=True)
    reps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lapses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    algorithm_state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    card: Mapped[Card] = relationship(back_populates="schedule_state")


class CardReview(Base, TimestampMixin):
    __tablename__ = "card_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"), nullable=False, index=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    rating: Mapped[str] = mapped_column(String(20), nullable=False)
    was_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    due_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    elapsed_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    response_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    card: Mapped[Card] = relationship(back_populates="reviews")


class StudySession(Base, TimestampMixin):
    __tablename__ = "study_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id"), nullable=True, index=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    energy_pre: Mapped[int | None] = mapped_column(Integer, nullable=True)
    focus_pre: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleepiness_pre: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mood_pre: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retention_post: Mapped[int | None] = mapped_column(Integer, nullable=True)
    difficulty_post: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fatigue_post: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence_post: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    content_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    subject: Mapped[Subject | None] = relationship(back_populates="study_sessions")
    topic: Mapped[Topic | None] = relationship(back_populates="study_sessions")


class StudySessionRun(Base, TimestampMixin):
    __tablename__ = "study_session_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), nullable=False, index=True)
    study_session_id: Mapped[int | None] = mapped_column(ForeignKey("study_sessions.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True, default=StudySessionRunStatus.IN_PROGRESS.value
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    accumulated_active_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_paused_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gross_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    net_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ReviewTask(Base, TimestampMixin):
    __tablename__ = "review_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    study_session_id: Mapped[int | None] = mapped_column(ForeignKey("study_sessions.id"), nullable=True, index=True)
    study_session_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("study_session_runs.id"), nullable=True, index=True
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True, default=ReviewTaskStatus.PENDING.value)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class TopicProgress(Base, TimestampMixin):
    __tablename__ = "topic_progress"
    __table_args__ = (UniqueConstraint("topic_id", name="uq_topic_progress_topic"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    total_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_gross_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_net_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_reviews_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_studied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_review_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class QuestionAttempt(Base, TimestampMixin):
    __tablename__ = "question_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    difficulty_self: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    topic: Mapped[Topic] = relationship(back_populates="question_attempts")


class SleepLog(Base, TimestampMixin):
    __tablename__ = "sleep_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sleep_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    wake_up: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_hours: Mapped[float] = mapped_column(Float, nullable=False)
    quality: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class NutritionLog(Base, TimestampMixin):
    __tablename__ = "nutrition_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    meal_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    items_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    calories_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein_grams: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_grams: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_grams: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class HydrationLog(Base, TimestampMixin):
    __tablename__ = "hydration_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    volume_ml: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExerciseLog(Base, TimestampMixin):
    __tablename__ = "exercise_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    modality: Mapped[str] = mapped_column(String(100), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    intensity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class SupplementLog(Base, TimestampMixin):
    __tablename__ = "supplement_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    dose: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class DailyMetricSnapshot(Base, TimestampMixin):
    __tablename__ = "daily_metric_snapshots"
    __table_args__ = (UniqueConstraint("metric_date", name="uq_daily_metric_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    metric_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    total_study_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_reviews: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    question_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_energy: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_focus: Mapped[float | None] = mapped_column(Float, nullable=True)
    sleep_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class StudyStrategyProfile(Base, TimestampMixin):
    __tablename__ = "study_strategy_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class EmbeddingVector(Base, TimestampMixin):
    __tablename__ = "embedding_vectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True, default=EntityType.MATERIAL.value)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    material_id: Mapped[int | None] = mapped_column(ForeignKey("study_materials.id"), nullable=True)
    card_id: Mapped[int | None] = mapped_column(ForeignKey("cards.id"), nullable=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    vector_json: Mapped[list[float]] = mapped_column(JSON, nullable=False)

    material: Mapped[StudyMaterial | None] = relationship(back_populates="embeddings")
    card: Mapped[Card | None] = relationship(back_populates="embeddings")
