from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import select

from editalos.database import get_session, initialize_database
from editalos.enums import CardStatus, ReviewRating, ReviewTaskStatus, SRSAlgorithm, StudySessionRunStatus
from editalos.models import Card, ReviewTask, StudyMaterial, Subject, Topic
from editalos.services.analytics import AnalyticsService
from editalos.services.catalog import CatalogService, CatalogServiceError
from editalos.services.flashcards import FlashcardService, FlashcardServiceError
from editalos.services.study_strategy import StudyStrategyError, StudyStrategyService
from editalos.services.study_execution import StudyExecutionError, StudyExecutionService

FLASH_MESSAGE_KEY = "catalog_flash_message"
FLASHCARD_REVIEW_CARD_KEY = "flashcard_review_card_id"
FLASHCARD_SHOW_ANSWER_KEY = "flashcard_show_answer"
FLASHCARD_TOPIC_KEY = "flashcard_topic_select"
LAST_SYNCED_STUDY_TOPIC_KEY = "last_synced_study_topic_select"
STUDY_SESSION_CONTEXT_KEY = "study_session_content_summary"
DASHBOARD_SELECTED_STUDY_DATE_KEY = "dashboard_selected_study_date"

MONTH_NAMES_PT = (
    "Janeiro",
    "Fevereiro",
    "Marco",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)
WEEKDAY_LABELS_PT = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom")


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
    localized = StudyExecutionService.to_local(value)
    if localized is None:
        return "-"
    return localized.strftime("%d/%m/%Y %H:%M")


def _format_minutes(total_minutes: int | None) -> str:
    if total_minutes is None:
        return "-"
    hours, minutes = divmod(int(total_minutes), 60)
    if minutes == 0:
        return f"{hours}h"
    return f"{hours}h{minutes:02d}min"


def _format_day(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _status_label(status: str) -> str:
    labels = {
        CardStatus.NEW.value: "Novo",
        CardStatus.ACTIVE.value: "Ativo",
        CardStatus.SUSPENDED.value: "Suspenso",
        CardStatus.BURIED.value: "Oculto",
        StudySessionRunStatus.IN_PROGRESS.value: "Em andamento",
        StudySessionRunStatus.PAUSED.value: "Pausada",
        StudySessionRunStatus.FINISHED.value: "Finalizada",
        ReviewTaskStatus.PENDING.value: "Pendente",
        ReviewTaskStatus.OVERDUE.value: "Atrasada",
        ReviewTaskStatus.COMPLETED.value: "Concluida",
    }
    return labels.get(status, status)


def _algorithm_label(value: str) -> str:
    labels = {
        SRSAlgorithm.FSRS.value: "FSRS",
        SRSAlgorithm.SM2.value: "SM-2",
    }
    return labels.get(value, value)


def _truncate_text(value: str, limit: int = 96) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(limit - 3, 1)].rstrip()}..."


def _month_bounds(reference_date: date) -> tuple[date, date]:
    month_start = reference_date.replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_end = next_month - timedelta(days=1)
    return month_start, month_end


def _previous_month(reference_date: date) -> date:
    month_start = reference_date.replace(day=1)
    return (month_start - timedelta(days=1)).replace(day=1)


def _next_month(reference_date: date) -> date:
    month_start = reference_date.replace(day=1)
    return (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)


def _study_chart_dataframe(daily_summary: pd.DataFrame, days: int = 30) -> pd.DataFrame:
    if daily_summary.empty:
        return pd.DataFrame(columns=["date", "hours"])
    chart_df = daily_summary.tail(days).copy()
    chart_df["date"] = pd.to_datetime(chart_df["date"])
    return chart_df.set_index("date")[["hours"]]


def _study_day_sessions_dataframe(day_detail: dict[str, object]) -> pd.DataFrame:
    sessions = list(day_detail.get("sessions", []))
    if not sessions:
        return pd.DataFrame(columns=["inicio", "fim", "disciplina", "topico", "tempo_liquido", "resumo"])

    return pd.DataFrame(
        [
            {
                "inicio": session["started_at"].strftime("%H:%M"),
                "fim": session["ended_at"].strftime("%H:%M"),
                "disciplina": session["subject"],
                "topico": session["topic"],
                "tempo_liquido": _format_minutes(int(session["actual_minutes"])),
                "resumo": session["content_summary"] or "-",
            }
            for session in sessions
        ]
    )


def _study_day_subjects_dataframe(day_detail: dict[str, object]) -> pd.DataFrame:
    subjects = list(day_detail.get("subjects", []))
    if not subjects:
        return pd.DataFrame(columns=["disciplina", "tempo_liquido", "sessoes", "topicos"])

    return pd.DataFrame(
        [
            {
                "disciplina": row["subject"],
                "tempo_liquido": _format_minutes(int(row["minutes"])),
                "sessoes": int(row["sessions"]),
                "topicos": int(row["topics_count"]),
            }
            for row in subjects
        ]
    )


def _render_study_calendar(month_summary: pd.DataFrame, selected_date: date) -> None:
    st.markdown("##### Calendario mensal")
    lookup = {
        row["date"]: {"minutes": int(row["minutes"]), "sessions": int(row["sessions"])}
        for row in month_summary.to_dict("records")
    }
    month_title = f"{MONTH_NAMES_PT[selected_date.month - 1]} / {selected_date.year}"

    nav_col1, nav_col2, nav_col3 = st.columns([1, 2, 1])
    with nav_col1:
        if st.button("Mes anterior", use_container_width=True, key="study_calendar_prev_month"):
            st.session_state[DASHBOARD_SELECTED_STUDY_DATE_KEY] = _previous_month(selected_date)
            _safe_rerun()
    with nav_col2:
        st.markdown(f"**{month_title}**")
    with nav_col3:
        if st.button("Proximo mes", use_container_width=True, key="study_calendar_next_month"):
            st.session_state[DASHBOARD_SELECTED_STUDY_DATE_KEY] = _next_month(selected_date)
            _safe_rerun()

    weekday_columns = st.columns(7)
    for index, label in enumerate(WEEKDAY_LABELS_PT):
        weekday_columns[index].caption(label)

    month_matrix = calendar.Calendar(firstweekday=0).monthdatescalendar(selected_date.year, selected_date.month)
    for week_index, week in enumerate(month_matrix):
        day_columns = st.columns(7)
        for day_index, current_day in enumerate(week):
            with day_columns[day_index]:
                if current_day.month != selected_date.month:
                    st.caption(" ")
                    st.write("")
                    continue

                day_snapshot = lookup.get(current_day, {"minutes": 0, "sessions": 0})
                studied_minutes = int(day_snapshot["minutes"])
                sessions_count = int(day_snapshot["sessions"])
                st.caption(_format_minutes(studied_minutes))
                if st.button(
                    str(current_day.day),
                    key=f"study_calendar_day_{week_index}_{day_index}_{current_day.isoformat()}",
                    type="primary" if current_day == selected_date else "secondary",
                    use_container_width=True,
                    help=(
                        f"{_format_day(current_day)} | "
                        f"Tempo liquido: {_format_minutes(studied_minutes)} | "
                        f"Sessoes: {sessions_count}"
                    ),
                ):
                    st.session_state[DASHBOARD_SELECTED_STUDY_DATE_KEY] = current_day
                    _safe_rerun()


def _render_study_dashboard(analytics: AnalyticsService) -> None:
    today = StudyExecutionService.local_today()
    selected_date = st.session_state.get(DASHBOARD_SELECTED_STUDY_DATE_KEY, today)
    if isinstance(selected_date, datetime):
        selected_date = selected_date.date()
    if not isinstance(selected_date, date):
        selected_date = today

    overview_summary = analytics.study_daily_summary(days=90, reference_date=today)
    month_start, month_end = _month_bounds(selected_date)
    month_summary = analytics.study_daily_summary(start_date=month_start, end_date=month_end)
    day_detail = analytics.study_day_detail(selected_date)

    today_minutes = int(overview_summary.loc[overview_summary["date"] == today, "minutes"].sum())
    yesterday = today - timedelta(days=1)
    yesterday_minutes = int(overview_summary.loc[overview_summary["date"] == yesterday, "minutes"].sum())
    last_7_days = overview_summary.tail(7)
    last_30_days = overview_summary.tail(30)
    best_day_minutes = int(last_30_days["minutes"].max()) if not last_30_days.empty else 0
    average_7_days = int(round(last_7_days["minutes"].mean())) if not last_7_days.empty else 0

    st.subheader("Dashboard de estudo liquido")
    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric("Hoje", _format_minutes(today_minutes))
    metric_col2.metric("Ontem", _format_minutes(yesterday_minutes))
    metric_col3.metric("Ultimos 7 dias", _format_minutes(int(last_7_days["minutes"].sum())))
    metric_col4.metric("Media diaria 7d", _format_minutes(average_7_days))
    st.caption(f"Melhor dia nos ultimos 30 dias: {_format_minutes(best_day_minutes)}.")

    st.markdown("#### Evolucao diaria")
    chart_df = _study_chart_dataframe(overview_summary, days=30)
    if chart_df.empty or float(chart_df["hours"].sum()) == 0:
        st.info("Sem sessoes finalizadas nos ultimos 30 dias.")
    else:
        st.bar_chart(chart_df)

    st.markdown("#### Calendario e detalhe do dia")
    selector_col, focus_col = st.columns([1, 3])
    with selector_col:
        new_selected_date = st.date_input(
            "Dia em foco",
            value=selected_date,
            key=DASHBOARD_SELECTED_STUDY_DATE_KEY,
        )
        if isinstance(new_selected_date, tuple):
            new_selected_date = new_selected_date[0]
        selected_date = new_selected_date if isinstance(new_selected_date, date) else selected_date
    with focus_col:
        st.caption(
            f"Data selecionada: {_format_day(selected_date)} | "
            f"Tempo liquido: {_format_minutes(int(day_detail['total_minutes']))} | "
            f"Sessoes: {int(day_detail['sessions_count'])}"
        )

    dashboard_col1, dashboard_col2 = st.columns([1.25, 1])
    with dashboard_col1:
        _render_study_calendar(month_summary, selected_date)

    with dashboard_col2:
        st.markdown(f"##### Resumo de {_format_day(selected_date)}")
        detail_col1, detail_col2 = st.columns(2)
        detail_col1.metric("Tempo liquido", _format_minutes(int(day_detail["total_minutes"])))
        detail_col2.metric("Sessoes", int(day_detail["sessions_count"]))
        detail_col3, detail_col4 = st.columns(2)
        detail_col3.metric("Disciplinas", int(day_detail["subjects_count"]))
        detail_col4.metric("Topicos", int(day_detail["topics_count"]))

        if day_detail["first_started_at"] is not None and day_detail["last_ended_at"] is not None:
            st.caption(
                "Janela do estudo no dia: "
                f"{day_detail['first_started_at'].strftime('%H:%M')} ate {day_detail['last_ended_at'].strftime('%H:%M')}"
            )

        subjects_df = _study_day_subjects_dataframe(day_detail)
        if subjects_df.empty:
            st.info("Nenhum estudo registrado nesta data.")
        else:
            st.markdown("###### Distribuicao por disciplina")
            st.dataframe(subjects_df, use_container_width=True, hide_index=True)

            st.markdown("###### Sessoes do dia")
            st.dataframe(_study_day_sessions_dataframe(day_detail), use_container_width=True, hide_index=True)

            summaries = [session["content_summary"] for session in day_detail["sessions"] if session.get("content_summary")]
            if summaries:
                st.markdown("###### O que foi estudado")
                for summary in summaries:
                    st.caption(f"- {_truncate_text(str(summary), limit=180)}")


def _render_live_session_banner(active_snapshot: dict[str, object]) -> None:
    payload = {
        "subject": str(active_snapshot["subject"]),
        "topic": str(active_snapshot["topic"]),
        "status": str(active_snapshot["status"]),
        "status_label": _status_label(str(active_snapshot["status"])),
        "gross_seconds": int(active_snapshot["gross_seconds"]),
        "net_seconds": int(active_snapshot["net_seconds"]),
    }
    components.html(
        f"""
        <div id="session-banner" style="
            background:#102b4c;
            color:#4ea1ff;
            padding:16px;
            border-radius:10px;
            font-family:sans-serif;
            font-size:16px;
            line-height:1.4;
            border:1px solid rgba(78,161,255,0.18);
        "></div>
        <script>
        const data = {json.dumps(payload, ensure_ascii=True)};
        const banner = document.getElementById("session-banner");
        const startedAt = Date.now();

        function formatSeconds(totalSeconds) {{
            const safe = Math.max(Number(totalSeconds) || 0, 0);
            const hours = Math.floor(safe / 3600);
            const minutes = Math.floor((safe % 3600) / 60);
            const seconds = safe % 60;
            return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
        }}

        function render() {{
            const elapsed = Math.floor((Date.now() - startedAt) / 1000);
            const gross = data.gross_seconds + elapsed;
            const net = data.status === "in_progress" ? data.net_seconds + elapsed : data.net_seconds;
            banner.textContent =
                `Sessao ativa: ${{data.subject}} / ${{data.topic}} | ` +
                `Estado: ${{data.status_label}} | ` +
                `Bruto: ${{formatSeconds(gross)}} | ` +
                `Liquido: ${{formatSeconds(net)}}`;
        }}

        render();
        window.setInterval(render, 1000);
        </script>
        """,
        height=74,
    )


def _render_topic_progress_snapshot(progress_snapshot: dict[str, object] | None) -> None:
    if progress_snapshot is None:
        st.caption("Selecione um topico valido para ver o historico acumulado.")
        return

    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric("Sessoes no topico", int(progress_snapshot["total_sessions"]))
    metric_col2.metric("Tempo acumulado", _format_minutes(int(progress_snapshot["total_studied_minutes"])))
    metric_col3.metric("Revisoes concluidas", int(progress_snapshot["total_reviews_completed"]))
    metric_col4.metric("Ultimo estudo", _format_datetime(progress_snapshot["last_studied_at"]))

    if progress_snapshot.get("last_content_summary"):
        st.caption(f"Ultimo contexto registrado: {_truncate_text(str(progress_snapshot['last_content_summary']), limit=160)}")


def _clear_flashcard_review_state() -> None:
    st.session_state.pop(FLASHCARD_REVIEW_CARD_KEY, None)
    st.session_state.pop(FLASHCARD_SHOW_ANSWER_KEY, None)


def _sync_flashcard_topic_with_study_topic(selected_topic_id: int | None) -> None:
    if selected_topic_id is None:
        return
    last_synced = st.session_state.get(LAST_SYNCED_STUDY_TOPIC_KEY)
    if last_synced == selected_topic_id:
        return
    st.session_state[FLASHCARD_TOPIC_KEY] = int(selected_topic_id)
    st.session_state[LAST_SYNCED_STUDY_TOPIC_KEY] = int(selected_topic_id)


def _current_flashcard(review_queue: list[dict[str, object]]) -> dict[str, object] | None:
    if not review_queue:
        _clear_flashcard_review_state()
        return None

    queue_by_id = {int(item["id"]): item for item in review_queue}
    current_id = st.session_state.get(FLASHCARD_REVIEW_CARD_KEY)
    if current_id not in queue_by_id:
        current_id = next(iter(queue_by_id))
        st.session_state[FLASHCARD_REVIEW_CARD_KEY] = current_id
        st.session_state[FLASHCARD_SHOW_ANSWER_KEY] = False
    return queue_by_id[int(current_id)]


def _advance_flashcard_review_state(review_queue: list[dict[str, object]]) -> None:
    if not review_queue:
        _clear_flashcard_review_state()
        return

    ids = [int(item["id"]) for item in review_queue]
    current_id = st.session_state.get(FLASHCARD_REVIEW_CARD_KEY)
    if current_id not in ids:
        next_id = ids[0]
    else:
        next_id = ids[(ids.index(int(current_id)) + 1) % len(ids)]
    st.session_state[FLASHCARD_REVIEW_CARD_KEY] = next_id
    st.session_state[FLASHCARD_SHOW_ANSWER_KEY] = False


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


def _strategy_weekly_minutes_dataframe(summary: dict[str, object] | None) -> pd.DataFrame:
    if not summary:
        return pd.DataFrame(columns=["disciplina", "minutos_semana", "carga_semanal"])

    rows = []
    weekly_minutes = dict(summary.get("weekly_minutes", {}))
    for subject_name, minutes in sorted(weekly_minutes.items(), key=lambda item: (-int(item[1]), str(item[0]))):
        rows.append(
            {
                "disciplina": str(subject_name),
                "minutos_semana": int(minutes),
                "carga_semanal": _format_minutes(int(minutes)),
            }
        )
    return pd.DataFrame(rows)


def _strategy_presets_dataframe(summary: dict[str, object] | None) -> pd.DataFrame:
    if not summary:
        return pd.DataFrame(
            columns=["preset", "retencao", "intervalo_maximo", "passos", "reaprendizagem", "disciplinas"]
        )

    rows = []
    for preset in summary.get("presets", []):
        rows.append(
            {
                "preset": str(preset.get("name") or "-"),
                "retencao": f"{preset.get('desired_retention')}%" if preset.get("desired_retention") else "-",
                "intervalo_maximo": (
                    f"{preset.get('max_interval_min_days')}-{preset.get('max_interval_max_days')} dias"
                    if preset.get("max_interval_min_days") is not None and preset.get("max_interval_max_days") is not None
                    else "-"
                ),
                "passos": str(preset.get("learning_steps") or "-"),
                "reaprendizagem": str(preset.get("relearning_steps") or "-"),
                "disciplinas": ", ".join(preset.get("subjects") or []) or "-",
            }
        )
    return pd.DataFrame(rows)


def _render_study_strategy_form(service: StudyStrategyService) -> bool:
    with st.form("study_strategy_form", clear_on_submit=False):
        profile_name = st.text_input("Nome do perfil", value="SEFAZ - Estrategia ANKI")
        source_label = st.text_input("Origem", value="Estrutura_Anki.txt")
        raw_text = st.text_area(
            "Conteudo da estrategia",
            height=320,
            placeholder="Cole aqui o conteudo integral do arquivo Estrutura_Anki.txt.",
        )
        submitted = st.form_submit_button("Salvar estrategia e aplicar ao planejamento")

    if not submitted:
        return False

    try:
        result = service.import_strategy(name=profile_name, raw_text=raw_text, source_label=source_label)
    except StudyStrategyError as exc:
        st.error(str(exc))
        return False

    message = (
        f"Estrategia ativa: {result.profile.name}. "
        f"Disciplinas atualizadas: {len(result.updated_subjects)}."
    )
    if result.missing_subjects:
        preview = ", ".join(result.missing_subjects[:5])
        message += f" Pendencias: {preview}."
    _set_flash_message("success", message)
    return True


def _render_flashcard_form(
    service: FlashcardService,
    topic_options: list[dict[str, object]],
    recent_study_sessions: list[dict[str, object]],
) -> bool:
    if not topic_options:
        st.info("Cadastre ao menos um topico para habilitar o cadastro de flashcards.")
        return False

    topic_ids = [int(item["id"]) for item in topic_options]
    topic_by_id = {int(item["id"]): item for item in topic_options}
    session_labels = ["Sem vinculo"] + [str(item["label"]) for item in recent_study_sessions]
    session_by_label = {str(item["label"]): item for item in recent_study_sessions}

    with st.form("flashcard_form", clear_on_submit=True):
        selected_topic_id = st.selectbox(
            "Topico do flashcard",
            options=topic_ids,
            format_func=lambda option: str(topic_by_id[option]["label"]),
            key=FLASHCARD_TOPIC_KEY,
        )
        front = st.text_area("Frente", height=120, placeholder="Pergunta, conceito-chave ou gatilho de lembranca.")
        back = st.text_area("Verso", height=160, placeholder="Resposta objetiva e curta.")
        algorithm = st.selectbox(
            "Algoritmo",
            options=[algorithm.value for algorithm in SRSAlgorithm],
            format_func=_algorithm_label,
        )
        tags = st.text_input("Tags", placeholder="Opcional. Separe por virgula.")
        selected_session = st.selectbox("Vincular a revisao/sessao", options=session_labels)
        submitted = st.form_submit_button("Cadastrar flashcard")

    if not submitted:
        return False

    target_topic = topic_by_id[int(selected_topic_id)]
    linked_study_session = None if selected_session == "Sem vinculo" else session_by_label[selected_session]
    try:
        card = service.create_card(
            topic_id=int(selected_topic_id),
            study_session_id=None if linked_study_session is None else int(linked_study_session["id"]),
            front=front,
            back=back,
            algorithm=SRSAlgorithm(str(algorithm)),
            tags=tags,
        )
    except FlashcardServiceError as exc:
        st.error(str(exc))
        return False

    _set_flash_message(
        "success",
        f"Flashcard cadastrado: {target_topic['subject']} / {target_topic['topic']} (id={card.id}).",
    )
    return True


def _flashcards_dataframe(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=[
                "id",
                "subject",
                "topic",
                "front",
                "context",
                "algorithm",
                "status",
                "due_at",
                "reps",
                "lapses",
                "tags",
            ]
        )

    formatted_rows = []
    for row in rows:
        formatted_rows.append(
            {
                "id": row["id"],
                "subject": row["subject"],
                "topic": row["topic"],
                "front": _truncate_text(str(row["front"])),
                "context": _truncate_text(str(row["content_summary"])) if row.get("content_summary") else "-",
                "algorithm": _algorithm_label(str(row["algorithm"])),
                "status": _status_label(str(row["status"])),
                "due_at": _format_datetime(row["due_at"]),
                "reps": row["reps"],
                "lapses": row["lapses"],
                "tags": ", ".join(row["tags"]) if row.get("tags") else "-",
            }
        )
    return pd.DataFrame(formatted_rows)


def _render_flashcard_review_area(
    service: FlashcardService,
    review_queue: list[dict[str, object]],
    metrics: dict[str, int],
) -> bool:
    st.markdown("#### Revisao de flashcards")
    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric("Cards novos", metrics["new_cards"])
    metric_col2.metric("Cards vencidos", metrics["due_cards"])
    metric_col3.metric("Revisados hoje", metrics["reviewed_today"])
    metric_col4.metric("Fila atual", len(review_queue))

    current_card = _current_flashcard(review_queue)
    if current_card is None:
        st.info("Nao ha cards novos ou vencidos na fila neste momento.")
        return False

    st.caption("A fila prioriza cards vencidos e, depois, cards ainda novos.")
    header_col, action_col = st.columns([5, 1])
    with header_col:
        st.write(f"**{current_card['subject']} / {current_card['topic']}**")
        st.caption(
            " | ".join(
                [
                    f"Card #{current_card['id']}",
                    f"Algoritmo: {_algorithm_label(str(current_card['algorithm']))}",
                    f"Estado: {_status_label(str(current_card['status']))}",
                    f"Vencimento: {_format_datetime(current_card['due_at'])}",
                    f"Reps: {current_card['reps']}",
                    f"Lapses: {current_card['lapses']}",
                ]
            )
        )
        if current_card.get("content_summary"):
            st.caption(f"Contexto vinculado: {_truncate_text(str(current_card['content_summary']), limit=140)}")
    with action_col:
        if st.button("Outro card", use_container_width=True, key="next_flashcard"):
            _advance_flashcard_review_state(review_queue)
            return True

    st.text_area(
        "Frente do card",
        value=str(current_card["front"]),
        height=140,
        disabled=True,
        key=f"flashcard_front_{current_card['id']}",
    )

    show_answer = bool(st.session_state.get(FLASHCARD_SHOW_ANSWER_KEY, False))
    if not show_answer:
        if st.button("Mostrar resposta", use_container_width=True, key="show_flashcard_answer"):
            st.session_state[FLASHCARD_SHOW_ANSWER_KEY] = True
            return True
        return False

    st.text_area(
        "Verso do card",
        value=str(current_card["back"]),
        height=180,
        disabled=True,
        key=f"flashcard_back_{current_card['id']}",
    )

    rating_columns = st.columns(4)
    for index, rating in enumerate(ReviewRating):
        label = {
            ReviewRating.AGAIN: "Again",
            ReviewRating.HARD: "Hard",
            ReviewRating.GOOD: "Good",
            ReviewRating.EASY: "Easy",
        }[rating]
        with rating_columns[index]:
            if st.button(label, use_container_width=True, key=f"flashcard_rating_{rating.value}"):
                try:
                    result = service.review_card(card_id=int(current_card["id"]), rating=rating)
                except FlashcardServiceError as exc:
                    st.error(str(exc))
                    return False
                _clear_flashcard_review_state()
                due_text = _format_datetime(result.due_at)
                _set_flash_message(
                    "success",
                    f"Flashcard revisado com nota '{rating.value}'. Proximo vencimento: {due_text}.",
                )
                return True

    return False


def _reviews_dataframe(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["id", "subject", "topic", "context", "linked_cards", "due_at", "status"])
    formatted = []
    for row in rows:
        formatted.append(
            {
                "id": row["id"],
                "subject": row["subject"],
                "topic": row["topic"],
                "context": _truncate_text(str(row["content_summary"])) if row.get("content_summary") else "-",
                "linked_cards": int(row.get("linked_cards", 0) or 0),
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
    feedback_placeholder = st.empty()
    status_placeholder = st.empty()
    content_summary = st.text_area(
        "O que estudei nesta sessao",
        key=STUDY_SESSION_CONTEXT_KEY,
        height=110,
        placeholder="Ex.: artigo X sobre inferencia textual, tipos de questao, erros e pontos de atencao.",
    )

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
        _sync_flashcard_topic_with_study_topic(selected_topic_id)
    else:
        st.warning("Cadastre ao menos um topico para iniciar sessao de estudo.")

    progress_snapshot = (
        service.topic_progress_snapshot(int(selected_topic_id))
        if selected_topic_id is not None
        else None
    )
    _render_topic_progress_snapshot(progress_snapshot)

    start_disabled = active_snapshot is not None or selected_topic_id is None
    if st.button("Iniciar sessao", disabled=start_disabled, use_container_width=True, key="start_study_session"):
        try:
            started = service.start_session(int(selected_topic_id))
        except StudyExecutionError as exc:
            feedback_placeholder.error(str(exc))
        else:
            active_snapshot = service.session_snapshot(started)
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
                paused = service.pause_session()
            except StudyExecutionError as exc:
                feedback_placeholder.error(str(exc))
            else:
                active_snapshot = service.session_snapshot(paused)
                _set_flash_message("info", "Sessao pausada.")
                should_rerun = True

    with controls[1]:
        if st.button("Retomar", disabled=not can_resume, use_container_width=True, key="resume_study_session"):
            try:
                resumed = service.resume_session()
            except StudyExecutionError as exc:
                feedback_placeholder.error(str(exc))
            else:
                active_snapshot = service.session_snapshot(resumed)
                _set_flash_message("info", "Sessao retomada.")
                should_rerun = True

    with controls[2]:
        if st.button("Finalizar", disabled=not can_finish, use_container_width=True, key="finish_study_session"):
            try:
                result = service.finish_session(content_summary=content_summary)
            except StudyExecutionError as exc:
                feedback_placeholder.error(str(exc))
            else:
                active_snapshot = None
                st.session_state.pop(STUDY_SESSION_CONTEXT_KEY, None)
                _set_flash_message(
                    "success",
                    "Sessao finalizada. "
                    f"Tempo bruto: {_format_seconds(result.gross_seconds)} | "
                    f"Tempo liquido: {_format_seconds(result.net_seconds)} | "
                    f"Revisoes geradas: {result.generated_reviews}",
                )
                should_rerun = True

    if active_snapshot:
        with status_placeholder.container():
            _render_live_session_banner(active_snapshot)
    else:
        status_placeholder.caption("Nenhuma sessao ativa no momento.")

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
            f"Status: {_status_label(str(review_by_id[option]['status']))} | "
            f"Cards vinculados: {int(review_by_id[option].get('linked_cards', 0) or 0)}"
        ),
        key="due_review_select",
    )
    selected_review = review_by_id[int(selected_review_id)]
    if selected_review.get("content_summary"):
        st.caption(f"Contexto desta revisao: {selected_review['content_summary']}")
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
st.caption("Planejamento diario, execucao de estudo, integracao com ANKI, analytics e biohacking em ambiente local.")

_render_flash_message()

should_rerun = False

st.subheader("Execucao diaria")
with get_session() as session:
    catalog = CatalogService(session)
    study_execution = StudyExecutionService(session)
    flashcards = FlashcardService(session)
    strategy_service = StudyStrategyService(session)
    subjects = catalog.list_subjects()
    topic_options = study_execution.list_study_topics()
    active_snapshot = study_execution.session_snapshot()
    due_reviews_today = study_execution.list_due_reviews_today()
    flashcard_rows = flashcards.list_cards(limit=200)
    anki_export_payload = flashcards.export_to_anki_tsv() if flashcard_rows else ""
    strategy_summary = strategy_service.get_active_summary()

    should_rerun = _render_study_session_area(study_execution, topic_options, active_snapshot) or should_rerun
    should_rerun = _render_due_reviews_area(study_execution, due_reviews_today) or should_rerun

    st.divider()
    st.subheader("Estrategia ANKI")
    if strategy_summary:
        weekday_window = strategy_summary.get("daily_windows", {}).get("weekdays", {})
        weekend_window = strategy_summary.get("daily_windows", {}).get("weekend", {})
        strategy_col1, strategy_col2, strategy_col3 = st.columns(3)
        strategy_col1.metric("Perfil ativo", str(strategy_summary.get("profile_name") or "-"))
        strategy_col2.metric(
            "ANKI dias uteis",
            f"{weekday_window.get('min_minutes', 0)}-{weekday_window.get('max_minutes', 0)} min",
        )
        strategy_col3.metric(
            "ANKI fim de semana",
            f"{weekend_window.get('min_minutes', 0)}-{weekend_window.get('max_minutes', 0)} min",
        )
        st.caption(
            "Planejamento diario passa a reservar primeiro o bloco de revisao no ANKI e distribui o restante "
            "entre os topicos do EditalOS."
        )
        with st.expander("Ver presets e distribuicao semanal", expanded=False):
            presets_df = _strategy_presets_dataframe(strategy_summary)
            weekly_df = _strategy_weekly_minutes_dataframe(strategy_summary)
            if not presets_df.empty:
                st.markdown("#### Presets recomendados para o ANKI")
                st.dataframe(presets_df, use_container_width=True)
            if not weekly_df.empty:
                st.markdown("#### Metas semanais por disciplina")
                st.dataframe(weekly_df, use_container_width=True)
    else:
        st.info("Nenhuma estrategia ANKI ativa. Importe o conteudo do arquivo para orientar o planejamento.")

    with st.expander("Importar ou atualizar estrategia ANKI", expanded=strategy_summary is None):
        st.caption(
            "Cole o conteudo do arquivo `Estrutura_Anki.txt`. O sistema salva o perfil, atualiza pesos/metas "
            "de tempo e passa a reservar o bloco diario de revisao externa."
        )
        should_rerun = _render_study_strategy_form(strategy_service) or should_rerun

    st.divider()
    st.subheader("Cards legados para ANKI")
    st.caption(
        "A revisao de flashcards saiu da rotina principal do EditalOS. Os cards existentes ficam aqui apenas "
        "como base legada para exportacao."
    )
    if flashcard_rows:
        st.download_button(
            "Baixar TSV para importar no ANKI",
            data=anki_export_payload.encode("utf-8"),
            file_name=f"editalos_anki_export_{datetime.now().strftime('%Y%m%d_%H%M')}.tsv",
            mime="text/tab-separated-values",
            use_container_width=True,
        )
        st.caption(
            "Importe no ANKI como arquivo separado por tabulacao. Os campos exportados sao: frente, verso, tags, "
            "disciplina, topico, contexto e id do card."
        )
        st.dataframe(_flashcards_dataframe(flashcard_rows), use_container_width=True)
    else:
        st.info("Nenhum flashcard legado cadastrado ainda.")

    st.divider()
    with st.expander("Configuracao inicial e manutencao", expanded=False):
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
            st.caption(
                "Atualiza `weight` e, quando detectado, a quantidade de questoes das disciplinas ja cadastradas."
            )
            should_rerun = _render_subject_weight_sync_form(catalog, subjects) or should_rerun
        with sync_col_time:
            st.markdown("#### Sincronizacao de tempo")
            st.caption("Atualiza as metas totais e semanais de tempo por disciplina a partir do texto da LLM.")
            should_rerun = _render_subject_time_sync_form(catalog, subjects) or should_rerun

st.divider()

with get_session() as session:
    study_execution = StudyExecutionService(session)
    strategy_service = StudyStrategyService(session)
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
    guidance = strategy_service.build_daily_guidance(
        total_minutes=minutes,
        reference_date=StudyExecutionService.local_today(),
    )
    if guidance["has_strategy"]:
        plan_col1, plan_col2, plan_col3 = st.columns(3)
        plan_col1.metric("Bloco ANKI hoje", f"{guidance['anki_minutes_target']} min")
        plan_col2.metric(
            "Faixa recomendada",
            f"{guidance['anki_minutes_min']}-{guidance['anki_minutes_max']} min",
        )
        plan_col3.metric("Minutos para estudo novo", guidance["available_study_minutes"])
        st.caption(
            f"Perfil ativo: {guidance['profile_name']}. O plano abaixo ja desconta o tempo reservado ao ANKI "
            f"para {'fim de semana' if guidance['day_kind'] == 'weekend' else 'dias uteis'}."
        )
    else:
        st.caption("Sem estrategia ANKI ativa. O plano abaixo usa todos os minutos informados.")

    study_plan = study_execution.today_study_plan(total_minutes=guidance["available_study_minutes"])
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

    _render_study_dashboard(analytics)

    with st.expander("Analises complementares", expanded=False):
        st.markdown("#### Estudo por hora")
        study_by_hour = analytics.study_by_hour()
        if not study_by_hour.empty:
            st.bar_chart(study_by_hour.set_index("hour")["minutes"])
            st.dataframe(study_by_hour, use_container_width=True, hide_index=True)
        else:
            st.info("Sem dados de sessoes de estudo.")

        st.markdown("#### Acuracia por disciplina")
        acc_subject = analytics.accuracy_by_subject()
        if not acc_subject.empty:
            chart_df = acc_subject.set_index("subject")[["accuracy"]]
            st.bar_chart(chart_df)
            st.dataframe(acc_subject, use_container_width=True, hide_index=True)
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
