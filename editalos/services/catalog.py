from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from io import StringIO

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from editalos.models import Subject, Topic
from editalos.schemas import SubjectCreate, TopicCreate


class CatalogServiceError(Exception):
    pass


@dataclass(slots=True)
class BulkTopicImportResult:
    created_topics: list[Topic]
    skipped_names: list[str]


@dataclass(slots=True)
class SubjectWeightSyncEntry:
    name: str
    weight: float
    question_count: int | None = None


@dataclass(slots=True)
class SubjectStudyTimeSyncEntry:
    name: str
    planned_total_minutes: int | None = None
    planned_weekly_minutes: int | None = None


@dataclass(slots=True)
class SubjectSyncResult:
    parsed_entries: int
    updated_subjects: list[Subject]
    missing_names: list[str]


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
        planned_total_minutes: int | None = None,
        planned_weekly_minutes: int | None = None,
        notes: str | None = None,
    ) -> Subject:
        normalized_name = name.strip()
        if not normalized_name:
            raise CatalogServiceError("Informe o nome da disciplina.")

        payload = SubjectCreate(
            name=normalized_name,
            weight=weight,
            question_count=question_count,
            planned_total_minutes=planned_total_minutes,
            planned_weekly_minutes=planned_weekly_minutes,
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

    def sync_subject_weights(self, *, raw_text: str) -> SubjectSyncResult:
        entries = self.parse_subject_weight_import(raw_text)
        if not entries:
            raise CatalogServiceError("Nenhum peso de disciplina valido foi encontrado no texto informado.")

        subject_by_key = self._subject_lookup_by_name()
        updated_subjects: dict[int, Subject] = {}
        missing_names: list[str] = []
        for entry in entries:
            subject = subject_by_key.get(self._normalize_match_key(entry.name))
            if subject is None:
                missing_names.append(entry.name)
                continue
            subject.weight = entry.weight
            if entry.question_count is not None:
                subject.question_count = entry.question_count
            updated_subjects[subject.id] = subject

        self.session.flush()
        return SubjectSyncResult(
            parsed_entries=len(entries),
            updated_subjects=list(updated_subjects.values()),
            missing_names=missing_names,
        )

    def sync_subject_study_time(self, *, raw_text: str) -> SubjectSyncResult:
        entries = self.parse_subject_study_time_import(raw_text)
        if not entries:
            raise CatalogServiceError("Nenhuma meta de tempo por disciplina valida foi encontrada no texto informado.")

        subject_by_key = self._subject_lookup_by_name()
        updated_subjects: dict[int, Subject] = {}
        missing_names: list[str] = []
        for entry in entries:
            subject = subject_by_key.get(self._normalize_match_key(entry.name))
            if subject is None:
                missing_names.append(entry.name)
                continue
            if entry.planned_total_minutes is not None:
                subject.planned_total_minutes = entry.planned_total_minutes
            if entry.planned_weekly_minutes is not None:
                subject.planned_weekly_minutes = entry.planned_weekly_minutes
            updated_subjects[subject.id] = subject

        self.session.flush()
        return SubjectSyncResult(
            parsed_entries=len(entries),
            updated_subjects=list(updated_subjects.values()),
            missing_names=missing_names,
        )

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

    def import_topics_for_subject(
        self,
        *,
        subject_id: int,
        raw_text: str,
        weight: float = 1.0,
        incidence_estimate: float = 0.5,
        difficulty_baseline: float = 0.5,
        skip_existing: bool = False,
    ) -> BulkTopicImportResult:
        subject = self.session.get(Subject, subject_id)
        if subject is None:
            raise CatalogServiceError("Disciplina selecionada nao foi encontrada.")

        topic_names = self.parse_topic_import(raw_text=raw_text, subject_name=subject.name)
        if not topic_names:
            raise CatalogServiceError("Nenhum topico valido foi encontrado para importacao.")

        duplicate_names = self._find_duplicate_names(topic_names)
        if duplicate_names:
            preview = ", ".join(duplicate_names[:5])
            raise CatalogServiceError(f"Topicos duplicados no bloco informado: {preview}.")

        existing_names = {
            self._normalize_name(name): name
            for name in self.session.scalars(select(Topic.name).where(Topic.subject_id == subject_id)).all()
        }

        created_topics: list[Topic] = []
        skipped_names: list[str] = []
        for topic_name in topic_names:
            normalized_name = self._normalize_name(topic_name)
            if normalized_name in existing_names:
                if skip_existing:
                    skipped_names.append(topic_name)
                    continue
                raise CatalogServiceError(f"O topico '{topic_name}' ja existe nessa disciplina.")

            payload = TopicCreate(
                subject_id=subject_id,
                name=topic_name,
                weight=weight,
                incidence_estimate=incidence_estimate,
                difficulty_baseline=difficulty_baseline,
            )
            topic = Topic(**payload.model_dump())
            created_topics.append(topic)
            existing_names[normalized_name] = topic_name

        if created_topics:
            self.session.add_all(created_topics)
            try:
                self.session.flush()
            except IntegrityError as exc:
                self.session.rollback()
                raise CatalogServiceError("Falha ao importar topicos para a disciplina selecionada.") from exc

        return BulkTopicImportResult(created_topics=created_topics, skipped_names=skipped_names)

    def parse_topic_import(self, *, raw_text: str, subject_name: str | None = None) -> list[str]:
        csv_source = self._extract_csv_block(raw_text) or raw_text
        csv_topics = self._parse_topics_from_csv(csv_source, subject_name=subject_name)
        if csv_topics is not None:
            return csv_topics
        return self._parse_topics_from_lines(raw_text, subject_name=subject_name)

    def parse_subject_weight_import(self, raw_text: str) -> list[SubjectWeightSyncEntry]:
        entries_by_key: dict[str, SubjectWeightSyncEntry] = {}
        for line in raw_text.splitlines():
            cleaned = self._clean_subject_sync_line(line)
            if not cleaned:
                continue

            weighted_match = re.match(
                r"^(?P<name>.+?):\s*(?P<questions>\d+)\s*[x×]\s*(?P<proof_weight>[\d.,]+)\s*=\s*(?P<points>[\d.,]+)\s*pontos?\b",
                cleaned,
                flags=re.IGNORECASE,
            )
            score_match = re.match(
                r"^(?P<name>.+?):\s*(?P<points>[\d.,]+)\s*pontos?\b",
                cleaned,
                flags=re.IGNORECASE,
            )

            if weighted_match:
                name = weighted_match.group("name").strip()
                if not self._looks_like_subject_name(name):
                    continue
                entry = SubjectWeightSyncEntry(
                    name=name,
                    weight=self._parse_decimal(weighted_match.group("points")),
                    question_count=int(weighted_match.group("questions")),
                )
            elif score_match:
                name = score_match.group("name").strip()
                if not self._looks_like_subject_name(name):
                    continue
                entry = SubjectWeightSyncEntry(
                    name=name,
                    weight=self._parse_decimal(score_match.group("points")),
                )
            else:
                continue

            key = self._normalize_match_key(entry.name)
            existing = entries_by_key.get(key)
            if existing is None:
                entries_by_key[key] = entry
                continue
            existing.weight = entry.weight
            if entry.question_count is not None:
                existing.question_count = entry.question_count

        return list(entries_by_key.values())

    def parse_subject_study_time_import(self, raw_text: str) -> list[SubjectStudyTimeSyncEntry]:
        entries_by_key: dict[str, SubjectStudyTimeSyncEntry] = {}
        for line in raw_text.splitlines():
            cleaned = self._clean_subject_sync_line(line)
            if not cleaned:
                continue

            total_match = re.match(
                r"^(?P<name>.+?):\s*(?P<percent>[\d.,]+)%\s*(?:->|=>|=)\s*(?P<time>[\dhmin\s]+)$",
                cleaned,
                flags=re.IGNORECASE,
            )
            weekly_match = re.match(
                r"^(?P<name>.+?):\s*(?P<time>[\dhmin\s]+)\s+por\s+semana$",
                cleaned,
                flags=re.IGNORECASE,
            )

            if total_match:
                name = total_match.group("name").strip()
                if not self._looks_like_subject_name(name):
                    continue
                entry = entries_by_key.setdefault(
                    self._normalize_match_key(name),
                    SubjectStudyTimeSyncEntry(name=name),
                )
                entry.planned_total_minutes = self._parse_duration_to_minutes(total_match.group("time"))
                continue

            if weekly_match:
                name = weekly_match.group("name").strip()
                if not self._looks_like_subject_name(name):
                    continue
                entry = entries_by_key.setdefault(
                    self._normalize_match_key(name),
                    SubjectStudyTimeSyncEntry(name=name),
                )
                entry.planned_weekly_minutes = self._parse_duration_to_minutes(weekly_match.group("time"))

        return list(entries_by_key.values())

    @staticmethod
    def _extract_csv_block(raw_text: str) -> str | None:
        in_csv_block = False
        csv_lines: list[str] = []
        for line in raw_text.splitlines():
            stripped = line.strip()
            if stripped.lower() == "```csv":
                in_csv_block = True
                continue
            if in_csv_block and stripped == "```":
                break
            if in_csv_block:
                csv_lines.append(line)
        if not csv_lines:
            return None
        return "\n".join(csv_lines)

    def _parse_topics_from_csv(self, raw_text: str, *, subject_name: str | None) -> list[str] | None:
        cleaned_lines = [
            line.strip()
            for line in raw_text.splitlines()
            if line.strip() and line.strip() not in {"```", "```csv"}
        ]
        if not cleaned_lines:
            return []

        header_line = cleaned_lines[0]
        delimiter = ";" if header_line.count(";") > header_line.count(",") else ","
        rows = list(csv.reader(StringIO("\n".join(cleaned_lines)), delimiter=delimiter))
        if not rows or len(rows[0]) < 2:
            return None

        header = [self._normalize_name(cell) for cell in rows[0][:2]]
        if header[0] != "disciplina" or header[1] != "topico":
            return None

        target_subject = self._normalize_name(subject_name) if subject_name else None
        topics: list[str] = []
        for row in rows[1:]:
            if len(row) < 2:
                continue
            row_subject = self._sanitize_topic_name(row[0])
            row_topic = self._sanitize_topic_name(row[1])
            if not row_subject or not row_topic:
                continue
            if target_subject and self._normalize_name(row_subject) != target_subject:
                continue
            topics.append(row_topic)
        return topics

    def _parse_topics_from_lines(self, raw_text: str, *, subject_name: str | None) -> list[str]:
        topic_names: list[str] = []
        target_subject = self._normalize_name(subject_name) if subject_name else None
        use_markdown_sections = target_subject is not None and self._has_markdown_subject_headings(raw_text)
        in_selected_section = target_subject is None

        for line in raw_text.splitlines():
            heading = self._parse_markdown_subject_heading(line)
            if heading is not None:
                if use_markdown_sections and target_subject is not None:
                    in_selected_section = self._normalize_name(heading) == target_subject
                continue

            if use_markdown_sections and not in_selected_section:
                continue

            cleaned_name = self._clean_topic_line(line)
            if cleaned_name:
                topic_names.append(cleaned_name)

        return topic_names

    def _find_duplicate_names(self, topic_names: list[str]) -> list[str]:
        seen: set[str] = set()
        duplicates: list[str] = []
        duplicate_keys: set[str] = set()
        for topic_name in topic_names:
            normalized_name = self._normalize_name(topic_name)
            if normalized_name in seen and normalized_name not in duplicate_keys:
                duplicates.append(topic_name)
                duplicate_keys.add(normalized_name)
                continue
            seen.add(normalized_name)
        return duplicates

    @staticmethod
    def _has_markdown_subject_headings(raw_text: str) -> bool:
        return any(line.lstrip().startswith("### ") for line in raw_text.splitlines())

    @staticmethod
    def _parse_markdown_subject_heading(line: str) -> str | None:
        stripped = line.lstrip()
        if not stripped.startswith("### "):
            return None
        return stripped[4:].strip()

    def _clean_topic_line(self, line: str) -> str | None:
        stripped = line.strip().lstrip("\ufeff")
        if not stripped:
            return None
        if stripped in {"```", "```csv"}:
            return None
        if all(char in "-=_*" for char in stripped):
            return None

        upper_stripped = stripped.upper()
        if stripped.startswith("#"):
            return None
        if upper_stripped.startswith(("CONTEUDO COBRADO", "JUSTIFICATIVA", "OBSERVACAO", "SUMARIO")):
            return None
        if "|" in stripped and stripped == upper_stripped:
            return None

        cleaned = re.sub(r"^[-*•]+\s*", "", stripped)
        cleaned = re.sub(r"^\d+[.)]\s*", "", cleaned)
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = self._sanitize_topic_name(cleaned)
        return cleaned or None

    @staticmethod
    def _sanitize_topic_name(value: str) -> str:
        sanitized = re.sub(r"\s+", " ", value.strip().strip("\"'"))
        while sanitized.endswith((".", ";")):
            sanitized = sanitized[:-1].rstrip()
        return sanitized

    def _subject_lookup_by_name(self) -> dict[str, Subject]:
        return {
            self._normalize_match_key(subject.name): subject
            for subject in self.session.scalars(select(Subject).order_by(Subject.name)).all()
        }

    @classmethod
    def _clean_subject_sync_line(cls, line: str) -> str | None:
        stripped = line.strip().lstrip("\ufeff")
        if not stripped:
            return None
        stripped = stripped.replace("→", "->").replace("â†’", "->")
        stripped = re.sub(r"^[-*]+\s*", "", stripped)
        stripped = stripped.replace("**", "").replace("`", "")
        stripped = re.sub(r"\s+", " ", stripped).strip()
        return stripped or None

    @classmethod
    def _looks_like_subject_name(cls, value: str) -> bool:
        key = cls._normalize_match_key(value)
        if not key:
            return False
        if key in {"basicos", "especificos", "total da objetiva", "disciplinas", "blocos"}:
            return False
        if key.startswith(("cada ", "leitura estrategica", "estatisticamente", "media ", "ordem ")):
            return False
        return True

    @staticmethod
    def _parse_decimal(value: str) -> float:
        normalized = value.strip()
        if "," in normalized and "." in normalized:
            normalized = normalized.replace(".", "").replace(",", ".")
        elif "," in normalized:
            normalized = normalized.replace(",", ".")
        return float(normalized)

    @classmethod
    def _parse_duration_to_minutes(cls, value: str) -> int:
        compact = re.sub(r"\s+", "", value.casefold())
        match = re.fullmatch(
            r"(?:(?P<hours>\d+)h(?:(?P<hours_minutes>\d{1,2})(?:min)?)?|(?P<minutes_only>\d+)min)",
            compact,
        )
        if not match:
            raise CatalogServiceError(f"Formato de duracao invalido: {value}")
        hours = int(match.group("hours") or 0)
        minutes = int(match.group("hours_minutes") or match.group("minutes_only") or 0)
        return (hours * 60) + minutes

    @staticmethod
    def _normalize_name(value: str | None) -> str:
        if value is None:
            return ""
        normalized = unicodedata.normalize("NFKD", value.strip())
        ascii_like = "".join(char for char in normalized if not unicodedata.combining(char))
        return re.sub(r"\s+", " ", ascii_like).casefold()

    @classmethod
    def _normalize_match_key(cls, value: str | None) -> str:
        normalized = cls._normalize_name(value)
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()
