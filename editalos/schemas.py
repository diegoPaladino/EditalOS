from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from editalos.enums import ReviewRating, SRSAlgorithm


class SubjectCreate(BaseModel):
    name: str
    weight: float = 1.0
    question_count: int | None = None
    notes: str | None = None


class TopicCreate(BaseModel):
    subject_id: int
    name: str
    weight: float = 1.0
    description: str | None = None
    incidence_estimate: float = 0.5
    difficulty_baseline: float = 0.5
    parent_topic_id: int | None = None


class CardCreate(BaseModel):
    topic_id: int
    front: str
    back: str
    algorithm: SRSAlgorithm = SRSAlgorithm.FSRS
    tags: list[str] | None = None


class ReviewResult(BaseModel):
    card_id: int
    algorithm: str
    rating: str
    reviewed_at: datetime
    due_at: datetime | None
    interval_days: float
    reps: int
    lapses: int
    retrievability: float | None = None
    stability: float | None = None
    difficulty: float | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class StudySessionCreate(BaseModel):
    subject_id: int | None = None
    topic_id: int | None = None
    started_at: datetime
    ended_at: datetime
    planned_minutes: int | None = None
    energy_pre: int | None = Field(default=None, ge=1, le=5)
    focus_pre: int | None = Field(default=None, ge=1, le=5)
    sleepiness_pre: int | None = Field(default=None, ge=1, le=5)
    mood_pre: int | None = Field(default=None, ge=1, le=5)
    retention_post: int | None = Field(default=None, ge=1, le=5)
    difficulty_post: int | None = Field(default=None, ge=1, le=5)
    fatigue_post: int | None = Field(default=None, ge=1, le=5)
    confidence_post: int | None = Field(default=None, ge=1, le=5)
    accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: str | None = None


class SleepLogCreate(BaseModel):
    sleep_start: datetime
    wake_up: datetime
    quality: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = None


class NutritionLogCreate(BaseModel):
    consumed_at: datetime
    meal_label: str | None = None
    items_json: dict[str, Any]
    calories_estimate: float | None = None
    protein_grams: float | None = None
    carbs_grams: float | None = None
    fat_grams: float | None = None
    notes: str | None = None


class HydrationLogCreate(BaseModel):
    consumed_at: datetime
    volume_ml: int = Field(ge=1)
    source: str | None = None
    notes: str | None = None


class ExerciseLogCreate(BaseModel):
    started_at: datetime
    ended_at: datetime
    modality: str
    intensity: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = None


class SupplementLogCreate(BaseModel):
    consumed_at: datetime
    name: str
    dose: str | None = None
    notes: str | None = None


class PlannerWeights(BaseModel):
    edital_weight_factor: float = 1.0
    incidence_factor: float = 1.0
    deficiency_factor: float = 1.2
    forgetting_factor: float = 1.3
    exam_proximity_factor: float = 1.0
    recent_errors_factor: float = 1.0


class TopicPriority(BaseModel):
    topic_id: int
    subject_name: str
    topic_name: str
    priority_score: float
    recommended_minutes: int
    rationale: dict[str, float]


class ReviewRequest(BaseModel):
    card_id: int
    rating: ReviewRating
    response_seconds: float | None = None
