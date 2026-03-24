from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from editalos.database import Base
from editalos.models import Subject
from editalos.services.catalog import CatalogService
from editalos.services.study_strategy import StudyStrategyService


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    testing_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    db = testing_session_local()
    try:
        yield db
    finally:
        db.close()


def _seed_subjects(session) -> None:
    catalog = CatalogService(session)
    catalog.create_subject(name="Língua Portuguesa")
    catalog.create_subject(name="RACIOCÍNIO LÓGICO, MATEMÁTICA FINANCEIRA e ESTATÍSTICA")
    catalog.create_subject(name="Direito Constitucional")
    catalog.create_subject(name="Direito Administrativo")
    catalog.create_subject(name="Direito Financeiro")
    catalog.create_subject(name="DIREITO CIVIL, PENAL e EMPRESARIAL")
    catalog.create_subject(name="Economia")
    catalog.create_subject(name="Contabilidade Geral")
    catalog.create_subject(name="Realidade Ética, Social, Histórica, Geográfica, Cultural, Política E Econômica De Goiás")
    catalog.create_subject(name="Tecnologias Da Informação")
    catalog.create_subject(name="Auditoria")
    catalog.create_subject(name="CONTABILIDADE AVANÇADA e DE CUSTOS")
    catalog.create_subject(name="Direito Tributário I")
    catalog.create_subject(name="Direito Tributário II – Reforma Tributária")
    catalog.create_subject(name="Legislação Tributária")
    session.flush()


def test_import_strategy_updates_subject_weights_and_weekly_minutes(session):
    _seed_subjects(session)
    service = StudyStrategyService(session)
    raw_text = """
**Legislação Tributária Estadual sozinha vale 16,67%**; cada uma das cinco específicas seguintes vale **10%**, enquanto a maior parte dos básicos vale **4,17%** cada, e Direito Financeiro/Goiás valem **2,08%** cada.

* **Legislação Tributária Estadual:** **6h40/semana**
* **TI:** **4h/semana**
* **Auditoria:** **4h/semana**
* **Contabilidade Avançada e de Custos:** **4h/semana**
* **Direito Tributário I:** **4h/semana**
* **Direito Tributário II:** **4h/semana**
* **Cada básico de 4,17%:** **1h40/semana**
* **Direito Financeiro:** **50 min/semana**
* **Realidade de Goiás:** **50 min/semana**

**Preset 1 — Legislação Tributária Estadual**
* retenção desejada: **92%**
* intervalo máximo: **25 a 30 dias**
* passos de aprendizagem: **10m 30m**
* reaprendizagem: **10m**
* ocultar irmãos: **os 3 ligados**
* reagendar ao alterar: **desligado**

**Preset 2 — Específicos 10%**
(TI, Auditoria, Contabilidade Avançada/Custos, DT I, DT II)
* retenção desejada: **91%**
* intervalo máximo: **30 a 35 dias**
* passos: **10m 30m**
* reaprendizagem: **10m**
* ocultar irmãos: **os 3 ligados**
* reagendar ao alterar: **desligado**

**Preset 3 — Básicos**
* retenção desejada: **90%**
* intervalo máximo: **35 a 40 dias**
* passos: **10m 30m**
* reaprendizagem: **10m**
* ocultar irmãos: **os 3 ligados**
* reagendar ao alterar: **desligado**

**Dias úteis**
* **60 a 90 min**: revisões vencidas do Anki

**Fins de semana**
* **90 a 120 min**: revisões vencidas

1. **Legislação Tributária Estadual**
2. **TI / Auditoria / Contabilidade Avançada e de Custos / DT I / DT II**
3. **Português / RLMF-Estatística / Constitucional / Administrativo / Civil-Empresarial-Penal / Economia / Contabilidade Geral**
4. **Direito Financeiro / Goiás**
"""

    result = service.import_strategy(
        name="SEFAZ - Estrutura ANKI",
        raw_text=raw_text,
        source_label="Estrutura_Anki.txt",
    )

    summary = service.get_active_summary()
    subjects = {subject.name: subject for subject in session.query(Subject).all()}

    assert result.missing_subjects == []
    assert summary is not None
    assert summary["profile_name"] == "SEFAZ - Estrutura ANKI"
    assert summary["daily_windows"]["weekdays"]["min_minutes"] == 60
    assert summary["daily_windows"]["weekend"]["max_minutes"] == 120
    assert subjects["Legislação Tributária"].weight == 16.67
    assert subjects["Legislação Tributária"].planned_weekly_minutes == 400
    assert subjects["Tecnologias Da Informação"].weight == 10.0
    assert subjects["Tecnologias Da Informação"].planned_weekly_minutes == 240
    assert subjects["Direito Financeiro"].weight == 2.08
    assert subjects["Direito Financeiro"].planned_weekly_minutes == 50
    assert subjects["Língua Portuguesa"].weight == 4.17
    assert subjects["Língua Portuguesa"].planned_weekly_minutes == 100


def test_build_daily_guidance_reserves_anki_block(session):
    _seed_subjects(session)
    service = StudyStrategyService(session)
    raw_text = """
**Dias úteis**
* **60 a 90 min**: revisões vencidas do Anki

**Fins de semana**
* **90 a 120 min**: revisões vencidas
"""

    service.import_strategy(name="Perfil", raw_text=raw_text, source_label="teste.txt")

    weekday = service.build_daily_guidance(total_minutes=240, reference_date=date(2026, 3, 19))
    weekend = service.build_daily_guidance(total_minutes=240, reference_date=date(2026, 3, 21))

    assert weekday["anki_minutes_target"] == 75
    assert weekday["available_study_minutes"] == 165
    assert weekend["anki_minutes_target"] == 105
    assert weekend["available_study_minutes"] == 135
