from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from editalos.models import Subject, Topic
from editalos.schemas import SubjectCreate, TopicCreate


class CatalogServiceError(Exception):
    pass


class CatalogService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_subjects(self) -> list[Subject]:
        return self.session.scalars(select(Subject).order_by(Subject.name)).all()

    def create_subject(
        self,
        *,
        name: str,
        weight: float = 1.0,
        question_count: int | None = None,
        notes: str | None = None,
    ) -> Subject:
        normalized_name = name.strip()
        if not normalized_name:
            raise CatalogServiceError("Informe o nome da disciplina.")

        payload = SubjectCreate(
            name=normalized_name,
            weight=weight,
            question_count=question_count,
            notes=notes.strip() if notes else None,
        )
        subject = Subject(**payload.model_dump())
        self.session.add(subject)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise CatalogServiceError("Ja existe uma disciplina com esse nome.") from exc
        return subject

    def create_topic(
        self,
        *,
        subject_id: int,
        name: str,
        weight: float = 1.0,
        description: str | None = None,
        incidence_estimate: float = 0.5,
        difficulty_baseline: float = 0.5,
    ) -> Topic:
        if self.session.get(Subject, subject_id) is None:
            raise CatalogServiceError("Disciplina selecionada nao foi encontrada.")

        normalized_name = name.strip()
        if not normalized_name:
            raise CatalogServiceError("Informe o nome do topico.")

        payload = TopicCreate(
            subject_id=subject_id,
            name=normalized_name,
            weight=weight,
            description=description.strip() if description else None,
            incidence_estimate=incidence_estimate,
            difficulty_baseline=difficulty_baseline,
        )
        topic = Topic(**payload.model_dump())
        self.session.add(topic)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise CatalogServiceError("Ja existe esse topico nessa disciplina.") from exc
        return topic
