from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from editalos.database import Base
from editalos.services.catalog import CatalogService, CatalogServiceError


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_create_subject_success(session):
    service = CatalogService(session)
    subject = service.create_subject(name="Direito Constitucional", weight=2.0, question_count=10)

    assert subject.id is not None
    assert subject.name == "Direito Constitucional"
    assert subject.weight == 2.0
    assert subject.question_count == 10


def test_create_subject_duplicate_name_raises_error(session):
    service = CatalogService(session)
    service.create_subject(name="Português")

    with pytest.raises(CatalogServiceError):
        service.create_subject(name="Português")


def test_create_topic_requires_existing_subject(session):
    service = CatalogService(session)

    with pytest.raises(CatalogServiceError):
        service.create_topic(subject_id=999, name="Crase")


def test_create_topic_duplicate_per_subject_raises_error(session):
    service = CatalogService(session)
    subject = service.create_subject(name="Matematica")
    service.create_topic(subject_id=subject.id, name="Razao e proporcao")

    with pytest.raises(CatalogServiceError):
        service.create_topic(subject_id=subject.id, name="Razao e proporcao")
