from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy import select

from editalos.database import get_session, initialize_database
from editalos.enums import ReviewTaskStatus, StudySessionRunStatus
from editalos.models import Card, ReviewTask, StudyMaterial, Subject, Topic
from editalos.services.analytics import AnalyticsService
from editalos.services.catalog import CatalogService, CatalogServiceError
from editalos.services.study_execution import StudyExecutionError, StudyExecutionService

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


@st.cache_resource
def _initialize_app() -> bool:
    initialize_database()
    return True


def _format_seconds(total_seconds: int) -> str:
    seconds = max(int(total_seconds), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%d/%m/%Y %H:%M")


def _format_minutes(total_minutes: int | None) -> str:
    if total_minutes is None:
        return "-"
    hours, minutes = divmod(int(total_minutes), 60)
    if minutes == 0:
        return f"{hours}h"
    return f"{hours}h{minutes:02d}min"


def _status_label(status: str) -> str:
    labels = {
        StudySessionRunStatus.IN_PROGRESS.value: "Em andamento",
        StudySessionRunStatus.PAUSED.value: "Pausada",
        StudySessionRunStatus.FINISHED.value: "Finalizada",
        ReviewTaskStatus.PENDING.value: "Pendente",
        ReviewTaskStatus.OVERDUE.value: "Atrasada",
        ReviewTaskStatus.COMPLETED.value: "Concluida",
    }
    return labels.get(status, status)


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


def _render_topic_import_form(catalog: CatalogService, subjects: list[Subject]) -> bool:
    if not subjects:
        st.info("Cadastre ao menos uma disciplina para habilitar a importacao em lote.")
        return False

    labels = [f"{subject.name} (id={subject.id})" for subject in subjects]
    subject_by_label = dict(zip(labels, subjects, strict=True))

    with st.form("topic_import_form", clear_on_submit=True):
        selected_subject = st.selectbox("Disciplina para importacao", options=labels)
        raw_text = st.text_area(
            "Conteudo estruturado",
            height=240,
            placeholder=(
                "Cole aqui um topico por linha, uma secao Markdown com bullets, "
                "ou um CSV com cabecalho Disciplina,Topico."
            ),
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            weight = st.number_input("Peso padrao", min_value=0.1, value=1.0, step=0.1)
        with col2:
            incidence = st.slider("Incidencia padrao", min_value=0.0, max_value=1.0, value=0.5, step=0.05)
        with col3:
            difficulty = st.slider("Dificuldade padrao", min_value=0.0, max_value=1.0, value=0.5, step=0.05)
        skip_existing = st.checkbox("Ignorar topicos ja existentes", value=True)
        submitted = st.form_submit_button("Importar topicos da disciplina")

    if not submitted:
        return False

    subject = subject_by_label[selected_subject]
    try:
        result = catalog.import_topics_for_subject(
            subject_id=subject.id,
            raw_text=raw_text,
            weight=float(weight),
            incidence_estimate=float(incidence),
            difficulty_baseline=float(difficulty),
            skip_existing=skip_existing,
        )
    except CatalogServiceError as exc:
        st.error(str(exc))
        return False

    created_count = len(result.created_topics)
    skipped_count = len(result.skipped_names)
    if created_count and skipped_count:
        message = (
            f"Importacao concluida para {subject.name}: {created_count} topicos criados e "
            f"{skipped_count} duplicados ignorados."
        )
    elif created_count:
        message = f"Importacao concluida para {subject.name}: {created_count} topicos criados."
    else:
        message = f"Nenhum topico novo foi criado para {subject.name}. {skipped_count} duplicados foram ignorados."
    _set_flash_message("success", message)
    return True


def _render_subject_weight_sync_form(catalog: CatalogService, subjects: list[Subject]) -> bool:
    if not subjects:
        st.info("Cadastre as disciplinas antes de sincronizar pesos.")
        return False

    with st.form("subject_weight_sync_form", clear_on_submit=True):
        raw_text = st.text_area(
            "Cole o texto com os pesos por disciplina",
            height=220,
            placeholder="Cole aqui o conteudo do arquivo gerado pela LLM.",
        )
        submitted = st.form_submit_button("Sincronizar pesos das disciplinas")

    if not submitted:
        return False

    try:
        result = catalog.sync_subject_weights(raw_text=raw_text)
    except CatalogServiceError as exc:
        st.error(str(exc))
        return False

    message = (
        f"Sincronizacao de pesos concluida: {len(result.updated_subjects)} disciplinas atualizadas "
        f"de {result.parsed_entries} entradas validas."
    )
    if result.missing_names:
        preview = ", ".join(result.missing_names[:5])
        message += f" Nao encontradas: {preview}."
    _set_flash_message("success", message)
    return True


def _render_subject_time_sync_form(catalog: CatalogService, subjects: list[Subject]) -> bool:
    if not subjects:
        st.info("Cadastre as disciplinas antes de sincronizar metas de tempo.")
        return False

    with st.form("subject_time_sync_form", clear_on_submit=True):
        raw_text = st.text_area(
            "Cole o texto com as metas de tempo por disciplina",
            height=220,
            placeholder="Cole aqui o conteudo do arquivo de tempo por disciplina.",
        )
        submitted = st.form_submit_button("Sincronizar metas de tempo")

    if not submitted:
        return False

    try:
        result = catalog.sync_subject_study_time(raw_text=raw_text)
    except CatalogServiceError as exc:
        st.error(str(exc))
        return False

    message = (
        f"Sincronizacao de tempo concluida: {len(result.updated_subjects)} disciplinas atualizadas "
        f"de {result.parsed_entries} entradas validas."
    )
    if result.missing_names:
        preview = ", ".join(result.missing_names[:5])
        message += f" Nao encontradas: {preview}."
    _set_flash_message("success", message)
    return True


def _reviews_dataframe(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["id", "subject", "topic", "due_at", "status"])
    formatted = []
    for row in rows:
        formatted.append(
            {
                "id": row["id"],
                "subject": row["subject"],
                "topic": row["topic"],
                "due_at": _format_datetime(row["due_at"]),
                "status": _status_label(str(row["status"])),
            }
        )
    return pd.DataFrame(formatted)


def _render_study_session_area(
    service: StudyExecutionService,
    topic_options: list[dict[str, object]],
    active_snapshot: dict[str, object] | None,
) -> bool:
    should_rerun = False

    st.markdown("#### Sessao de estudo")
    if active_snapshot:
        st.info(
            "Sessao ativa: "
            f"{active_snapshot['subject']} / {active_snapshot['topic']} | "
            f"Estado: {_status_label(str(active_snapshot['status']))} | "
            f"Bruto: {_format_seconds(int(active_snapshot['gross_seconds']))} | "
            f"Liquido: {_format_seconds(int(active_snapshot['net_seconds']))}"
        )
    else:
        st.caption("Nenhuma sessao ativa no momento.")

    topic_by_id = {int(item["id"]): item for item in topic_options}
    topic_ids = list(topic_by_id)

    selected_topic_id: int | None = None
    if topic_ids:
        selected_topic_id = st.selectbox(
            "Topico para iniciar sessao",
            options=topic_ids,
            format_func=lambda option: str(topic_by_id[option]["label"]),
            key="study_topic_start",
        )
    else:
        st.warning("Cadastre ao menos um topico para iniciar sessao de estudo.")

    start_disabled = active_snapshot is not None or selected_topic_id is None
    if st.button("Iniciar sessao", disabled=start_disabled, use_container_width=True, key="start_study_session"):
        try:
            started = service.start_session(int(selected_topic_id))
        except StudyExecutionError as exc:
            st.error(str(exc))
        else:
            _set_flash_message("success", f"Sessao iniciada com sucesso (id={started.id}).")
            should_rerun = True

    controls = st.columns(3)
    can_pause = active_snapshot is not None and active_snapshot["status"] == StudySessionRunStatus.IN_PROGRESS.value
    can_resume = active_snapshot is not None and active_snapshot["status"] == StudySessionRunStatus.PAUSED.value
    can_finish = active_snapshot is not None and active_snapshot["status"] in {
        StudySessionRunStatus.IN_PROGRESS.value,
        StudySessionRunStatus.PAUSED.value,
    }

    with controls[0]:
        if st.button("Pausar", disabled=not can_pause, use_container_width=True, key="pause_study_session"):
            try:
                service.pause_session()
            except StudyExecutionError as exc:
                st.error(str(exc))
            else:
                _set_flash_message("info", "Sessao pausada.")
                should_rerun = True

    with controls[1]:
        if st.button("Retomar", disabled=not can_resume, use_container_width=True, key="resume_study_session"):
            try:
                service.resume_session()
            except StudyExecutionError as exc:
                st.error(str(exc))
            else:
                _set_flash_message("info", "Sessao retomada.")
                should_rerun = True

    with controls[2]:
        if st.button("Finalizar", disabled=not can_finish, use_container_width=True, key="finish_study_session"):
            try:
                result = service.finish_session()
            except StudyExecutionError as exc:
                st.error(str(exc))
            else:
                _set_flash_message(
                    "success",
                    "Sessao finalizada. "
                    f"Tempo bruto: {_format_seconds(result.gross_seconds)} | "
                    f"Tempo liquido: {_format_seconds(result.net_seconds)} | "
                    f"Revisoes geradas: {result.generated_reviews}",
                )
                should_rerun = True

    return should_rerun


def _render_due_reviews_area(service: StudyExecutionService, due_reviews: list[dict[str, object]]) -> bool:
    st.markdown("#### Revisoes para concluir hoje")
    if not due_reviews:
        st.info("Nao ha revisoes pendentes ou atrasadas para hoje.")
        return False

    st.dataframe(_reviews_dataframe(due_reviews), use_container_width=True)

    review_by_id = {int(item["id"]): item for item in due_reviews}
    review_ids = list(review_by_id)
    selected_review_id = st.selectbox(
        "Selecione a revisao para marcar como concluida",
        options=review_ids,
        format_func=lambda option: (
            f"{review_by_id[option]['subject']} / {review_by_id[option]['topic']} | "
            f"Vencimento: {_format_datetime(review_by_id[option]['due_at'])} | "
            f"Status: {_status_label(str(review_by_id[option]['status']))}"
        ),
        key="due_review_select",
    )
    if st.button("Marcar revisao como concluida", use_container_width=True, key="complete_review"):
        try:
            service.mark_review_completed(int(selected_review_id))
        except StudyExecutionError as exc:
            st.error(str(exc))
            return False
        _set_flash_message("success", "Revisao marcada como concluida.")
        return True
    return False

st.set_page_config(page_title="EditalOS", layout="wide")
_initialize_app()
st.title("EditalOS")
st.caption("Planejamento, revisao espacada, analytics e biohacking em ambiente local.")

_render_flash_message()

should_rerun = False

st.subheader("Execucao diaria")
with get_session() as session:
    catalog = CatalogService(session)
    study_execution = StudyExecutionService(session)
    subjects = catalog.list_subjects()
    topic_options = study_execution.list_study_topics()
    active_snapshot = study_execution.session_snapshot()
    due_reviews_today = study_execution.list_due_reviews_today()

    should_rerun = _render_study_session_area(study_execution, topic_options, active_snapshot) or should_rerun
    should_rerun = _render_due_reviews_area(study_execution, due_reviews_today) or should_rerun

    st.divider()
    st.subheader("Cadastros de apoio")
    form_col_subject, form_col_topic = st.columns(2)
    with form_col_subject:
        st.markdown("#### Cadastro de disciplina")
        should_rerun = _render_subject_form(catalog) or should_rerun
    with form_col_topic:
        st.markdown("#### Cadastro de topico")
        should_rerun = _render_topic_form(catalog, subjects) or should_rerun
    st.markdown("#### Importacao de topicos por disciplina")
    st.caption(
        "Aceita um topico por linha, uma secao Markdown com bullets ou um CSV com cabecalho "
        "`Disciplina,Topico`."
    )
    should_rerun = _render_topic_import_form(catalog, subjects) or should_rerun

    sync_col_weight, sync_col_time = st.columns(2)
    with sync_col_weight:
        st.markdown("#### Sincronizacao de pesos")
        st.caption("Atualiza `weight` e, quando detectado, a quantidade de questoes das disciplinas ja cadastradas.")
        should_rerun = _render_subject_weight_sync_form(catalog, subjects) or should_rerun
    with sync_col_time:
        st.markdown("#### Sincronizacao de tempo")
        st.caption("Atualiza as metas totais e semanais de tempo por disciplina a partir do texto da LLM.")
        should_rerun = _render_subject_time_sync_form(catalog, subjects) or should_rerun

st.divider()

with get_session() as session:
    study_execution = StudyExecutionService(session)
    operational_metrics = study_execution.today_operational_metrics()
    overdue_reviews = study_execution.list_overdue_reviews()
    upcoming_reviews = study_execution.list_upcoming_reviews(days_ahead=30)

    st.subheader("Painel operacional")
    op_col1, op_col2, op_col3, op_col4 = st.columns(4)
    op_col1.metric("Tempo estudado hoje (min)", operational_metrics["studied_minutes"])
    op_col2.metric("Revisoes concluidas hoje", operational_metrics["completed_reviews"])
    op_col3.metric("Revisoes vencidas", len(overdue_reviews))
    op_col4.metric("Proximas revisoes (30d)", len(upcoming_reviews))

    st.markdown("#### O que estudar hoje")
    minutes = st.slider("Minutos totais de estudo", min_value=60, max_value=720, value=240, step=30)
    study_plan = study_execution.today_study_plan(total_minutes=minutes)
    if study_plan:
        st.dataframe(pd.DataFrame(study_plan), use_container_width=True)
    else:
        st.info("Cadastre disciplinas e topicos para gerar plano de estudo.")

    panel_col1, panel_col2 = st.columns(2)
    with panel_col1:
        st.markdown("#### Revisoes vencidas")
        if overdue_reviews:
            st.dataframe(_reviews_dataframe(overdue_reviews), use_container_width=True)
        else:
            st.info("Nao ha revisoes vencidas.")

    with panel_col2:
        st.markdown("#### Proximas revisoes")
        if upcoming_reviews:
            st.dataframe(_reviews_dataframe(upcoming_reviews), use_container_width=True)
        else:
            st.info("Nao ha proximas revisoes no horizonte de 30 dias.")

    st.divider()

    subject_count = session.query(Subject).count()
    topic_count = session.query(Topic).count()
    card_count = session.query(Card).count()
    material_count = session.query(StudyMaterial).count()
    review_task_count = session.query(ReviewTask).count()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Disciplinas", subject_count)
    col2.metric("Topicos", topic_count)
    col3.metric("Cards", card_count)
    col4.metric("Materiais", material_count)
    col5.metric("Revisoes", review_task_count)

    st.divider()

    analytics = AnalyticsService(session)

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

    st.subheader("Disciplinas cadastradas")
    subjects_rows = session.execute(
        select(
            Subject.id,
            Subject.name,
            Subject.weight,
            Subject.question_count,
            Subject.planned_total_minutes,
            Subject.planned_weekly_minutes,
        ).order_by(Subject.name)
    ).all()
    if subjects_rows:
        formatted_subjects = [
            {
                "id": row.id,
                "subject": row.name,
                "weight": row.weight,
                "question_count": row.question_count,
                "planned_total": _format_minutes(row.planned_total_minutes),
                "planned_weekly": _format_minutes(row.planned_weekly_minutes),
            }
            for row in subjects_rows
        ]
        st.dataframe(pd.DataFrame(formatted_subjects), use_container_width=True)
    else:
        st.info("Nenhuma disciplina cadastrada ainda.")

if should_rerun:
    _safe_rerun()
