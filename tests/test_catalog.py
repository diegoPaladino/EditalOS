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


def test_import_topics_for_subject_from_markdown_section(session):
    service = CatalogService(session)
    subject = service.create_subject(name="Direito Constitucional")
    other_subject = service.create_subject(name="Direito Administrativo")
    assert other_subject.id is not None

    raw_text = """
# Conteudo Programatico

## Estrutura por Disciplinas

### Direito Constitucional
- Constituicao Federal
- Direitos e garantias fundamentais.

### Direito Administrativo
- Ato administrativo
"""

    result = service.import_topics_for_subject(subject_id=subject.id, raw_text=raw_text)

    assert [topic.name for topic in result.created_topics] == [
        "Constituicao Federal",
        "Direitos e garantias fundamentais",
    ]
    assert result.skipped_names == []


def test_import_topics_for_subject_from_csv_skips_existing(session):
    service = CatalogService(session)
    subject = service.create_subject(name="Lingua Portuguesa")
    service.create_topic(subject_id=subject.id, name="Ortografia oficial")

    raw_text = """
Disciplina,Topico
Lingua Portuguesa,Ortografia oficial
Lingua Portuguesa,Interpretacao de texto
Direito Constitucional,Controle de constitucionalidade
"""

    result = service.import_topics_for_subject(
        subject_id=subject.id,
        raw_text=raw_text,
        skip_existing=True,
    )

    assert [topic.name for topic in result.created_topics] == ["Interpretacao de texto"]
    assert result.skipped_names == ["Ortografia oficial"]


def test_import_topics_for_subject_rejects_duplicate_names_in_payload(session):
    service = CatalogService(session)
    subject = service.create_subject(name="Auditoria")

    with pytest.raises(CatalogServiceError):
        service.import_topics_for_subject(
            subject_id=subject.id,
            raw_text="- Planejamento\n- Planejamento",
        )


def test_sync_subject_weights_updates_existing_subjects(session):
    service = CatalogService(session)
    service.create_subject(name="Tecnologia da Informacao")
    service.create_subject(name="Legislacao Tributaria Estadual")

    raw_text = """
    * Tecnologia da Informacao: 12 x 2 = 24 pontos
    * **Legislacao Tributaria Estadual:** **40 pontos** = **16,67%** da objetiva
    """

    result = service.sync_subject_weights(raw_text=raw_text)

    subjects = {subject.name: subject for subject in service.list_subjects()}
    assert result.parsed_entries == 2
    assert len(result.updated_subjects) == 2
    assert result.missing_names == []
    assert subjects["Tecnologia da Informacao"].weight == 24.0
    assert subjects["Tecnologia da Informacao"].question_count == 12
    assert subjects["Legislacao Tributaria Estadual"].weight == 40.0


def test_sync_subject_study_time_updates_total_and_weekly_targets(session):
    service = CatalogService(session)
    service.create_subject(name="Tecnologia da Informacao")
    service.create_subject(name="Legislacao Tributaria Estadual")

    raw_text = """
    * **Legislacao Tributaria Estadual**: **16,67%** -> **58h20min**
    * **Tecnologia da Informacao**: **10,00%** -> **35h**
    * **Legislacao Tributaria Estadual**: **6h40 por semana**
    * **Tecnologia da Informacao**: **4h por semana**
    """

    result = service.sync_subject_study_time(raw_text=raw_text)

    subjects = {subject.name: subject for subject in service.list_subjects()}
    assert result.parsed_entries == 2
    assert len(result.updated_subjects) == 2
    assert result.missing_names == []
    assert subjects["Legislacao Tributaria Estadual"].planned_total_minutes == 3500
    assert subjects["Legislacao Tributaria Estadual"].planned_weekly_minutes == 400
    assert subjects["Tecnologia da Informacao"].planned_total_minutes == 2100
    assert subjects["Tecnologia da Informacao"].planned_weekly_minutes == 240
