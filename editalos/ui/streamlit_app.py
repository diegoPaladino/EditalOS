from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from editalos.database import get_session
from editalos.models import Card, StudyMaterial, Subject, Topic
from editalos.services.analytics import AnalyticsService
from editalos.services.planner import PlannerService

st.set_page_config(page_title="EditalOS", layout="wide")
st.title("EditalOS")
st.caption("Planejamento, revisão espaçada, analytics e biohacking em ambiente local.")

with get_session() as session:
    subject_count = session.query(Subject).count()
    topic_count = session.query(Topic).count()
    card_count = session.query(Card).count()
    material_count = session.query(StudyMaterial).count()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Disciplinas", subject_count)
col2.metric("Tópicos", topic_count)
col3.metric("Cards", card_count)
col4.metric("Materiais", material_count)

st.divider()

with get_session() as session:
    analytics = AnalyticsService(session)
    planner = PlannerService(session)

    st.subheader("Plano diário sugerido")
    minutes = st.slider("Minutos totais de estudo", min_value=60, max_value=720, value=240, step=30)
    plan = planner.build_daily_plan(total_minutes=minutes)
    if plan:
        st.dataframe(pd.DataFrame([item.model_dump() for item in plan]), use_container_width=True)
    else:
        st.info("Cadastre disciplinas e tópicos para gerar plano.")

    st.subheader("Estudo por hora")
    study_by_hour = analytics.study_by_hour()
    if not study_by_hour.empty:
        st.bar_chart(study_by_hour.set_index("hour")["minutes"])
        st.dataframe(study_by_hour, use_container_width=True)
    else:
        st.info("Sem dados de sessões de estudo.")

    st.subheader("Acurácia por disciplina")
    acc_subject = analytics.accuracy_by_subject()
    if not acc_subject.empty:
        chart_df = acc_subject.set_index("subject")[["accuracy"]]
        st.bar_chart(chart_df)
        st.dataframe(acc_subject, use_container_width=True)
    else:
        st.info("Sem dados de questões por disciplina.")

    st.subheader("Tópicos cadastrados")
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
        st.info("Nenhum tópico cadastrado ainda.")
