from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from editalos.database import get_session
from editalos.models import Card, StudyMaterial, Subject, Topic
from editalos.services.analytics import AnalyticsService
from editalos.services.catalog import CatalogService, CatalogServiceError
from editalos.services.planner import PlannerService

FLASH_MESSAGE_KEY = "catalog_flash_message"


def _set_flash_message(level: str, text: str) -> None:
    st.session_state[FLASH_MESSAGE_KEY] = {"level": level, "text": text}


def _render_flash_message() -> None:
    message = st.session_state.pop(FLASH_MESSAGE_KEY, None)
    if not message:
        return
    level = str(message.get("level", "info"))
    text = str(message.get("text", ""))
    show_message = getattr(st, level, st.info)
    show_message(text)


def _safe_rerun() -> None:
    try:
        st.rerun()
    except AttributeError:
        st.experimental_rerun()


def _render_subject_form(catalog: CatalogService) -> bool:
    with st.form("subject_form", clear_on_submit=True):
        name = st.text_input("Nome da disciplina")
        weight = st.number_input("Peso da disciplina", min_value=0.1, value=1.0, step=0.1)
        include_question_count = st.checkbox("Informar quantidade de questoes")
        question_count: int | None = None
        if include_question_count:
            question_count = int(st.number_input("Quantidade de questoes", min_value=0, value=0, step=1))
        notes = st.text_area("Observacoes", placeholder="Opcional")
        submitted = st.form_submit_button("Cadastrar disciplina")

    if not submitted:
        return False

    try:
        subject = catalog.create_subject(
            name=name,
            weight=float(weight),
            question_count=question_count,
            notes=notes,
        )
    except CatalogServiceError as exc:
        st.error(str(exc))
        return False

    _set_flash_message("success", f"Disciplina cadastrada: {subject.name} (id={subject.id}).")
    return True


def _render_topic_form(catalog: CatalogService, subjects: list[Subject]) -> bool:
    if not subjects:
        st.info("Cadastre ao menos uma disciplina para habilitar o cadastro de topico.")
        return False

    labels = [f"{subject.name} (id={subject.id})" for subject in subjects]
    subject_by_label = dict(zip(labels, subjects, strict=True))

    with st.form("topic_form", clear_on_submit=True):
        selected_subject = st.selectbox("Disciplina", options=labels)
        name = st.text_input("Nome do topico")
        weight = st.number_input("Peso do topico", min_value=0.1, value=1.0, step=0.1)
        incidence = st.slider("Incidencia estimada", min_value=0.0, max_value=1.0, value=0.5, step=0.05)
        difficulty = st.slider("Dificuldade base", min_value=0.0, max_value=1.0, value=0.5, step=0.05)
        description = st.text_area("Descricao", placeholder="Opcional")
        submitted = st.form_submit_button("Cadastrar topico")

    if not submitted:
        return False

    subject = subject_by_label[selected_subject]
    try:
        topic = catalog.create_topic(
            subject_id=subject.id,
            name=name,
            weight=float(weight),
            description=description,
            incidence_estimate=float(incidence),
            difficulty_baseline=float(difficulty),
        )
    except CatalogServiceError as exc:
        st.error(str(exc))
        return False

    _set_flash_message("success", f"Topico cadastrado: {subject.name} / {topic.name} (id={topic.id}).")
    return True


st.set_page_config(page_title="EditalOS", layout="wide")
st.title("EditalOS")
st.caption("Planejamento, revisao espacada, analytics e biohacking em ambiente local.")

_render_flash_message()

st.subheader("Operacoes")
should_rerun = False
form_col_subject, form_col_topic = st.columns(2)
with get_session() as session:
    catalog = CatalogService(session)
    subjects = catalog.list_subjects()

    with form_col_subject:
        st.markdown("#### Cadastro de disciplina")
        should_rerun = _render_subject_form(catalog) or should_rerun
    with form_col_topic:
        st.markdown("#### Cadastro de topico")
        should_rerun = _render_topic_form(catalog, subjects) or should_rerun

st.divider()

with get_session() as session:
    subject_count = session.query(Subject).count()
    topic_count = session.query(Topic).count()
    card_count = session.query(Card).count()
    material_count = session.query(StudyMaterial).count()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Disciplinas", subject_count)
    col2.metric("Topicos", topic_count)
    col3.metric("Cards", card_count)
    col4.metric("Materiais", material_count)

    st.divider()

    analytics = AnalyticsService(session)
    planner = PlannerService(session)

    st.subheader("Plano diario sugerido")
    minutes = st.slider("Minutos totais de estudo", min_value=60, max_value=720, value=240, step=30)
    plan = planner.build_daily_plan(total_minutes=minutes)
    if plan:
        st.dataframe(pd.DataFrame([item.model_dump() for item in plan]), use_container_width=True)
    else:
        st.info("Cadastre disciplinas e topicos para gerar plano.")

    st.subheader("Estudo por hora")
    study_by_hour = analytics.study_by_hour()
    if not study_by_hour.empty:
        st.bar_chart(study_by_hour.set_index("hour")["minutes"])
        st.dataframe(study_by_hour, use_container_width=True)
    else:
        st.info("Sem dados de sessoes de estudo.")

    st.subheader("Acuracia por disciplina")
    acc_subject = analytics.accuracy_by_subject()
    if not acc_subject.empty:
        chart_df = acc_subject.set_index("subject")[["accuracy"]]
        st.bar_chart(chart_df)
        st.dataframe(acc_subject, use_container_width=True)
    else:
        st.info("Sem dados de questoes por disciplina.")

    st.subheader("Topicos cadastrados")
    topics = session.execute(
        select(Topic.id, Subject.name.label("subject"), Topic.name, Topic.weight, Topic.incidence_estimate)
        .join(Subject)
        .order_by(Subject.name, Topic.name)
    ).all()
    if topics:
        st.dataframe(
            pd.DataFrame(topics, columns=["id", "subject", "topic", "weight", "incidence_estimate"]),
            use_container_width=True,
        )
    else:
        st.info("Nenhum topico cadastrado ainda.")

if should_rerun:
    _safe_rerun()
