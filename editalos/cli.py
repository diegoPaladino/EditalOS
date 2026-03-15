from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from editalos.database import get_session, initialize_database
from editalos.enums import ReviewRating, SRSAlgorithm
from editalos.models import (
    Card,
    ExerciseLog,
    HydrationLog,
    NutritionLog,
    SleepLog,
    Subject,
    SupplementLog,
    Topic,
)
from editalos.schemas import (
    CardCreate,
    ExerciseLogCreate,
    HydrationLogCreate,
    NutritionLogCreate,
    SleepLogCreate,
    StudySessionCreate,
    SubjectCreate,
    SupplementLogCreate,
    TopicCreate,
)
from editalos.services.analytics import AnalyticsService
from editalos.services.catalog import CatalogService, CatalogServiceError
from editalos.services.embeddings import EmbeddingService
from editalos.services.openai_service import OpenAIService
from editalos.services.pdf_ingest import PDFIngestService
from editalos.services.planner import PlannerConfig, PlannerService
from editalos.services.srs import SRSService
from editalos.models import StudySession



def parse_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class CLI:
    def build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="editalos", description="EditalOS CLI")
        sub = parser.add_subparsers(dest="command", required=True)

        sub.add_parser("init-db")

        p = sub.add_parser("add-subject")
        p.add_argument("--name", required=True)
        p.add_argument("--weight", type=float, default=1.0)
        p.add_argument("--question-count", type=int)
        p.add_argument("--notes")

        p = sub.add_parser("add-topic")
        p.add_argument("--subject-id", required=True, type=int)
        p.add_argument("--name", required=True)
        p.add_argument("--weight", type=float, default=1.0)
        p.add_argument("--description")
        p.add_argument("--incidence-estimate", type=float, default=0.5)
        p.add_argument("--difficulty-baseline", type=float, default=0.5)
        p.add_argument("--parent-topic-id", type=int)

        p = sub.add_parser("import-topics")
        p.add_argument("--subject-id", required=True, type=int)
        p.add_argument("--path", required=True)
        p.add_argument("--weight", type=float, default=1.0)
        p.add_argument("--incidence-estimate", type=float, default=0.5)
        p.add_argument("--difficulty-baseline", type=float, default=0.5)
        p.add_argument("--skip-existing", action="store_true")

        p = sub.add_parser("sync-subject-weights")
        p.add_argument("--path", required=True)

        p = sub.add_parser("sync-subject-time")
        p.add_argument("--path", required=True)

        p = sub.add_parser("add-card")
        p.add_argument("--topic-id", required=True, type=int)
        p.add_argument("--front", required=True)
        p.add_argument("--back", required=True)
        p.add_argument("--algorithm", choices=[e.value for e in SRSAlgorithm], default=SRSAlgorithm.FSRS.value)
        p.add_argument("--tags", nargs="*")

        p = sub.add_parser("review-card")
        p.add_argument("--card-id", required=True, type=int)
        p.add_argument("--rating", required=True, choices=[e.value for e in ReviewRating])
        p.add_argument("--response-seconds", type=float)

        p = sub.add_parser("log-study")
        p.add_argument("--subject-id", type=int)
        p.add_argument("--topic-id", type=int)
        p.add_argument("--started-at", required=True)
        p.add_argument("--ended-at", required=True)
        p.add_argument("--planned-minutes", type=int)
        p.add_argument("--energy", type=int)
        p.add_argument("--focus", type=int)
        p.add_argument("--sleepiness", type=int)
        p.add_argument("--mood", type=int)
        p.add_argument("--retention", type=int)
        p.add_argument("--difficulty", type=int)
        p.add_argument("--fatigue", type=int)
        p.add_argument("--confidence", type=int)
        p.add_argument("--accuracy", type=float)
        p.add_argument("--notes")

        p = sub.add_parser("log-sleep")
        p.add_argument("--sleep-start", required=True)
        p.add_argument("--wake-up", required=True)
        p.add_argument("--quality", type=int)
        p.add_argument("--notes")

        p = sub.add_parser("log-hydration")
        p.add_argument("--consumed-at", required=True)
        p.add_argument("--volume-ml", required=True, type=int)
        p.add_argument("--source")
        p.add_argument("--notes")

        p = sub.add_parser("log-nutrition")
        p.add_argument("--consumed-at", required=True)
        p.add_argument("--meal-label")
        p.add_argument("--items-json", required=True)
        p.add_argument("--calories", type=float)
        p.add_argument("--protein", type=float)
        p.add_argument("--carbs", type=float)
        p.add_argument("--fat", type=float)
        p.add_argument("--notes")

        p = sub.add_parser("log-exercise")
        p.add_argument("--started-at", required=True)
        p.add_argument("--ended-at", required=True)
        p.add_argument("--modality", required=True)
        p.add_argument("--intensity", type=int)
        p.add_argument("--notes")

        p = sub.add_parser("log-supplement")
        p.add_argument("--consumed-at", required=True)
        p.add_argument("--name", required=True)
        p.add_argument("--dose")
        p.add_argument("--notes")

        p = sub.add_parser("import-pdf")
        p.add_argument("--path", required=True)
        p.add_argument("--title", required=True)
        p.add_argument("--topic-id", type=int)
        p.add_argument("--extract-structure", action="store_true")
        p.add_argument("--reindex", action="store_true")

        p = sub.add_parser("semantic-search")
        p.add_argument("--query", required=True)
        p.add_argument("--top-k", type=int, default=5)

        p = sub.add_parser("plan-day")
        p.add_argument("--minutes", type=int, default=240)
        p.add_argument("--exam-date")

        sub.add_parser("analytics")
        return parser

    def run(self) -> None:
        args = self.build_parser().parse_args()
        initialize_database()
        handler = getattr(self, f"cmd_{args.command.replace('-', '_')}")
        handler(args)

    @staticmethod
    def cmd_init_db(args: argparse.Namespace) -> None:
        del args
        initialize_database()
        print("Banco inicializado com sucesso.")

    @staticmethod
    def cmd_add_subject(args: argparse.Namespace) -> None:
        payload = SubjectCreate(
            name=args.name,
            weight=args.weight,
            question_count=args.question_count,
            notes=args.notes,
        )
        with get_session() as session:
            subject = Subject(**payload.model_dump())
            session.add(subject)
            session.flush()
            print(f"Disciplina criada: id={subject.id} nome={subject.name}")

    @staticmethod
    def cmd_add_topic(args: argparse.Namespace) -> None:
        payload = TopicCreate(
            subject_id=args.subject_id,
            name=args.name,
            weight=args.weight,
            description=args.description,
            incidence_estimate=args.incidence_estimate,
            difficulty_baseline=args.difficulty_baseline,
            parent_topic_id=args.parent_topic_id,
        )
        with get_session() as session:
            topic = Topic(**payload.model_dump())
            session.add(topic)
            session.flush()
            print(f"Tópico criado: id={topic.id} nome={topic.name}")

    @staticmethod
    def cmd_import_topics(args: argparse.Namespace) -> None:
        raw_text = Path(args.path).read_text(encoding="utf-8-sig")
        with get_session() as session:
            catalog = CatalogService(session)
            try:
                result = catalog.import_topics_for_subject(
                    subject_id=args.subject_id,
                    raw_text=raw_text,
                    weight=args.weight,
                    incidence_estimate=args.incidence_estimate,
                    difficulty_baseline=args.difficulty_baseline,
                    skip_existing=args.skip_existing,
                )
            except CatalogServiceError as exc:
                raise SystemExit(str(exc)) from exc

            print(
                "Importacao concluida: "
                f"criados={len(result.created_topics)} ignorados={len(result.skipped_names)}"
            )

    @staticmethod
    def cmd_sync_subject_weights(args: argparse.Namespace) -> None:
        raw_text = Path(args.path).read_text(encoding="utf-8-sig")
        with get_session() as session:
            catalog = CatalogService(session)
            try:
                result = catalog.sync_subject_weights(raw_text=raw_text)
            except CatalogServiceError as exc:
                raise SystemExit(str(exc)) from exc

            print(
                "Sincronizacao de pesos concluida: "
                f"linhas_validas={result.parsed_entries} atualizadas={len(result.updated_subjects)} "
                f"nao_encontradas={len(result.missing_names)}"
            )
            if result.missing_names:
                print("Disciplinas nao encontradas:", ", ".join(result.missing_names))

    @staticmethod
    def cmd_sync_subject_time(args: argparse.Namespace) -> None:
        raw_text = Path(args.path).read_text(encoding="utf-8-sig")
        with get_session() as session:
            catalog = CatalogService(session)
            try:
                result = catalog.sync_subject_study_time(raw_text=raw_text)
            except CatalogServiceError as exc:
                raise SystemExit(str(exc)) from exc

            print(
                "Sincronizacao de tempo concluida: "
                f"linhas_validas={result.parsed_entries} atualizadas={len(result.updated_subjects)} "
                f"nao_encontradas={len(result.missing_names)}"
            )
            if result.missing_names:
                print("Disciplinas nao encontradas:", ", ".join(result.missing_names))

    @staticmethod
    def cmd_add_card(args: argparse.Namespace) -> None:
        payload = CardCreate(
            topic_id=args.topic_id,
            front=args.front,
            back=args.back,
            algorithm=SRSAlgorithm(args.algorithm),
            tags=args.tags,
        )
        with get_session() as session:
            card = Card(**payload.model_dump(mode="json"))
            session.add(card)
            session.flush()
            print(f"Card criado: id={card.id} algoritmo={card.algorithm}")

    @staticmethod
    def cmd_review_card(args: argparse.Namespace) -> None:
        with get_session() as session:
            card = session.get(Card, args.card_id)
            if card is None:
                raise SystemExit(f"Card {args.card_id} não encontrado.")
            service = SRSService()
            result = service.review(
                card=card,
                rating=ReviewRating(args.rating),
                response_seconds=args.response_seconds,
            )
            session.add(card)
            session.flush()
            print(result.model_dump_json(indent=2, exclude_none=True))

    @staticmethod
    def cmd_log_study(args: argparse.Namespace) -> None:
        started_at = parse_datetime(args.started_at)
        ended_at = parse_datetime(args.ended_at)
        actual_minutes = max(int((ended_at - started_at).total_seconds() // 60), 1)
        payload = StudySessionCreate(
            subject_id=args.subject_id,
            topic_id=args.topic_id,
            started_at=started_at,
            ended_at=ended_at,
            planned_minutes=args.planned_minutes,
            energy_pre=args.energy,
            focus_pre=args.focus,
            sleepiness_pre=args.sleepiness,
            mood_pre=args.mood,
            retention_post=args.retention,
            difficulty_post=args.difficulty,
            fatigue_post=args.fatigue,
            confidence_post=args.confidence,
            accuracy=args.accuracy,
            notes=args.notes,
        )
        with get_session() as session:
            study = StudySession(actual_minutes=actual_minutes, **payload.model_dump())
            session.add(study)
            session.flush()
            print(f"Sessão registrada: id={study.id} minutos={study.actual_minutes}")

    @staticmethod
    def cmd_log_sleep(args: argparse.Namespace) -> None:
        payload = SleepLogCreate(
            sleep_start=parse_datetime(args.sleep_start),
            wake_up=parse_datetime(args.wake_up),
            quality=args.quality,
            notes=args.notes,
        )
        duration_hours = (payload.wake_up - payload.sleep_start).total_seconds() / 3600.0
        with get_session() as session:
            log = SleepLog(duration_hours=duration_hours, **payload.model_dump())
            session.add(log)
            session.flush()
            print(f"Sono registrado: id={log.id} horas={log.duration_hours:.2f}")

    @staticmethod
    def cmd_log_hydration(args: argparse.Namespace) -> None:
        payload = HydrationLogCreate(
            consumed_at=parse_datetime(args.consumed_at),
            volume_ml=args.volume_ml,
            source=args.source,
            notes=args.notes,
        )
        with get_session() as session:
            entry = HydrationLog(**payload.model_dump())
            session.add(entry)
            session.flush()
            print(f"Hidratação registrada: id={entry.id} volume_ml={entry.volume_ml}")

    @staticmethod
    def cmd_log_nutrition(args: argparse.Namespace) -> None:
        payload = NutritionLogCreate(
            consumed_at=parse_datetime(args.consumed_at),
            meal_label=args.meal_label,
            items_json=json.loads(args.items_json),
            calories_estimate=args.calories,
            protein_grams=args.protein,
            carbs_grams=args.carbs,
            fat_grams=args.fat,
            notes=args.notes,
        )
        with get_session() as session:
            entry = NutritionLog(**payload.model_dump())
            session.add(entry)
            session.flush()
            print(f"Nutrição registrada: id={entry.id}")

    @staticmethod
    def cmd_log_exercise(args: argparse.Namespace) -> None:
        payload = ExerciseLogCreate(
            started_at=parse_datetime(args.started_at),
            ended_at=parse_datetime(args.ended_at),
            modality=args.modality,
            intensity=args.intensity,
            notes=args.notes,
        )
        duration_minutes = max(int((payload.ended_at - payload.started_at).total_seconds() // 60), 1)
        with get_session() as session:
            entry = ExerciseLog(duration_minutes=duration_minutes, **payload.model_dump())
            session.add(entry)
            session.flush()
            print(f"Exercício registrado: id={entry.id} minutos={entry.duration_minutes}")

    @staticmethod
    def cmd_log_supplement(args: argparse.Namespace) -> None:
        payload = SupplementLogCreate(
            consumed_at=parse_datetime(args.consumed_at),
            name=args.name,
            dose=args.dose,
            notes=args.notes,
        )
        with get_session() as session:
            entry = SupplementLog(**payload.model_dump())
            session.add(entry)
            session.flush()
            print(f"Suplemento registrado: id={entry.id} nome={entry.name}")

    @staticmethod
    def cmd_import_pdf(args: argparse.Namespace) -> None:
        with get_session() as session:
            openai_service = OpenAIService() if args.extract_structure or args.reindex else None
            ingest = PDFIngestService(session, openai_service=openai_service)
            material = ingest.import_pdf(path=args.path, title=args.title, topic_id=args.topic_id)
            if args.extract_structure and openai_service:
                material.extracted_structure = ingest.extract_edital_structure(material.raw_text or "")
            session.add(material)
            session.flush()
            if args.reindex:
                embed_service = EmbeddingService(session, openai_service=openai_service)
                count = embed_service.reindex_material(material.id)
                print(f"PDF importado: id={material.id} chunks_indexados={count}")
                return
            print(f"PDF importado: id={material.id}")

    @staticmethod
    def cmd_semantic_search(args: argparse.Namespace) -> None:
        with get_session() as session:
            service = EmbeddingService(session)
            results = service.semantic_search(query=args.query, top_k=args.top_k)
            print(json.dumps(results, ensure_ascii=False, indent=2))

    @staticmethod
    def cmd_plan_day(args: argparse.Namespace) -> None:
        exam_date = parse_datetime(args.exam_date) if args.exam_date else None
        with get_session() as session:
            planner = PlannerService(session, config=PlannerConfig(exam_date=exam_date))
            plan = planner.build_daily_plan(total_minutes=args.minutes)
            print(json.dumps([item.model_dump() for item in plan], ensure_ascii=False, indent=2))

    @staticmethod
    def cmd_analytics(args: argparse.Namespace) -> None:
        del args
        with get_session() as session:
            analytics = AnalyticsService(session)
            study_hour = analytics.study_by_hour()
            by_subject = analytics.accuracy_by_subject()
            print("=== ESTUDO POR HORA ===")
            print(study_hour.to_string(index=False) if not study_hour.empty else "Sem dados.")
            print("\n=== ACURÁCIA POR DISCIPLINA ===")
            print(by_subject.to_string(index=False) if not by_subject.empty else "Sem dados.")
            print("\n=== SNAPSHOT BIOHACKING ===")
            print(json.dumps(analytics.simple_biohacking_snapshot(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    CLI().run()
