from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from editalos.models import StudyStrategyProfile, Subject


class StudyStrategyError(Exception):
    pass


@dataclass(slots=True)
class StudyStrategyImportResult:
    profile: StudyStrategyProfile
    updated_subjects: list[str]
    missing_subjects: list[str]


class StudyStrategyService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_active_profile(self) -> StudyStrategyProfile | None:
        return self.session.scalars(
            select(StudyStrategyProfile)
            .where(StudyStrategyProfile.is_active.is_(True))
            .order_by(StudyStrategyProfile.updated_at.desc(), StudyStrategyProfile.id.desc())
        ).first()

    def get_active_summary(self) -> dict[str, Any] | None:
        profile = self.get_active_profile()
        if profile is None:
            return None
        summary = dict(profile.strategy_json or {})
        summary["profile_name"] = profile.name
        summary["source_label"] = profile.source_label
        summary["profile_id"] = profile.id
        return summary

    def import_strategy(
        self,
        *,
        name: str,
        raw_text: str,
        source_label: str | None = None,
    ) -> StudyStrategyImportResult:
        normalized_name = name.strip()
        normalized_text = raw_text.strip()
        if not normalized_name:
            raise StudyStrategyError("Informe um nome para a estrategia.")
        if not normalized_text:
            raise StudyStrategyError("Cole o conteudo da estrategia antes de salvar.")

        summary = self.parse_strategy_text(normalized_text)
        updated_subjects, missing_subjects = self._apply_strategy_to_subjects(summary)

        for existing_profile in self.session.scalars(select(StudyStrategyProfile)).all():
            existing_profile.is_active = False

        profile = StudyStrategyProfile(
            name=normalized_name,
            source_label=source_label.strip() if source_label else None,
            raw_text=normalized_text,
            strategy_json=summary,
            is_active=True,
        )
        self.session.add(profile)
        self.session.flush()
        return StudyStrategyImportResult(
            profile=profile,
            updated_subjects=updated_subjects,
            missing_subjects=missing_subjects,
        )

    def build_daily_guidance(self, *, total_minutes: int, reference_date: date | None = None) -> dict[str, Any]:
        summary = self.get_active_summary()
        safe_total_minutes = max(int(total_minutes), 0)
        if summary is None:
            return {
                "has_strategy": False,
                "available_study_minutes": safe_total_minutes,
                "anki_minutes_target": 0,
                "anki_minutes_min": 0,
                "anki_minutes_max": 0,
                "day_kind": "manual",
                "profile_name": None,
                "source_label": None,
                "presets": [],
                "weekly_minutes": {},
                "macro_minutes": {},
                "priority_groups": {},
            }

        target_date = reference_date or date.today()
        is_weekend = target_date.weekday() >= 5
        window_key = "weekend" if is_weekend else "weekdays"
        window = dict(summary.get("daily_windows", {}).get(window_key) or {})
        min_minutes = int(window.get("min_minutes") or 0)
        max_minutes = int(window.get("max_minutes") or 0)
        if max_minutes < min_minutes:
            max_minutes = min_minutes
        target_minutes = int(round((min_minutes + max_minutes) / 2)) if max_minutes or min_minutes else 0

        return {
            "has_strategy": True,
            "available_study_minutes": max(safe_total_minutes - target_minutes, 0),
            "anki_minutes_target": target_minutes,
            "anki_minutes_min": min_minutes,
            "anki_minutes_max": max_minutes,
            "day_kind": "weekend" if is_weekend else "weekdays",
            "profile_name": summary.get("profile_name"),
            "source_label": summary.get("source_label"),
            "presets": list(summary.get("presets", [])),
            "weekly_minutes": dict(summary.get("weekly_minutes", {})),
            "macro_minutes": dict(summary.get("macro_minutes", {})),
            "priority_groups": dict(summary.get("priority_groups", {})),
        }

    def parse_strategy_text(self, raw_text: str) -> dict[str, Any]:
        priority_groups = self._parse_priority_groups(raw_text)
        presets = self._parse_presets(raw_text)
        preset_specific_subjects = self._preset_subjects_by_name(presets, "Especificos 10%")
        weekly_minutes = self._parse_weekly_minutes(raw_text, priority_groups)
        macro_minutes = self._parse_macro_minutes(raw_text)
        weights = self._derive_subject_weights(raw_text, priority_groups, preset_specific_subjects)
        daily_windows = self._parse_daily_windows(raw_text)

        return {
            "weekly_minutes": weekly_minutes,
            "macro_minutes": macro_minutes,
            "weights": weights,
            "daily_windows": daily_windows,
            "priority_groups": priority_groups,
            "presets": presets,
        }

    def _apply_strategy_to_subjects(self, summary: dict[str, Any]) -> tuple[list[str], list[str]]:
        weekly_minutes = dict(summary.get("weekly_minutes", {}))
        weights = dict(summary.get("weights", {}))
        subject_lookup = self._subject_lookup()
        updated_subjects: dict[int, Subject] = {}
        missing_subjects: list[str] = []

        for label, minutes in weekly_minutes.items():
            subject = subject_lookup.get(self._normalize_key(label))
            if subject is None:
                missing_subjects.append(f"tempo: {label}")
                continue
            subject.planned_weekly_minutes = int(minutes)
            updated_subjects[subject.id] = subject

        for label, weight in weights.items():
            subject = subject_lookup.get(self._normalize_key(label))
            if subject is None:
                missing_subjects.append(f"peso: {label}")
                continue
            subject.weight = float(weight)
            updated_subjects[subject.id] = subject

        self.session.flush()
        unique_missing = sorted(set(missing_subjects))
        return sorted(subject.name for subject in updated_subjects.values()), unique_missing

    def _subject_lookup(self) -> dict[str, Subject]:
        lookup: dict[str, Subject] = {}
        for subject in self.session.scalars(select(Subject).order_by(Subject.name)).all():
            for alias in self._subject_aliases(subject.name):
                lookup.setdefault(alias, subject)
        return lookup

    def _parse_weekly_minutes(self, raw_text: str, priority_groups: dict[str, list[str]]) -> dict[str, int]:
        weekly_minutes: dict[str, int] = {}
        for line in raw_text.splitlines():
            cleaned = self._strip_markdown(line)
            if not cleaned or ":" not in cleaned:
                continue
            name, value = cleaned.split(":", 1)
            if "/semana" not in value.casefold() and "por semana" not in value.casefold():
                continue
            minutes_match = re.search(r"(\d+h(?:\d{1,2}(?:min)?)?|\d+\s*min)", value, flags=re.IGNORECASE)
            if minutes_match is None:
                continue
            minutes = self._duration_to_minutes(minutes_match.group(1))
            normalized_name = self._normalize_key(name)
            if normalized_name.startswith("cada basico"):
                for subject_name in self._basic_subjects(priority_groups):
                    weekly_minutes[subject_name] = minutes
                continue
            weekly_minutes[self._clean_subject_label(name)] = minutes
        return weekly_minutes

    def _parse_macro_minutes(self, raw_text: str) -> dict[str, int]:
        macro_minutes: dict[str, int] = {}
        for line in raw_text.splitlines():
            cleaned = self._strip_markdown(line)
            if ":" not in cleaned:
                continue
            name, value = cleaned.split(":", 1)
            normalized_name = self._normalize_key(name)
            if normalized_name not in {"especificos", "basicos"}:
                continue
            minutes_match = re.search(r"(\d+h(?:\d{1,2}(?:min)?)?|\d+\s*min)", value, flags=re.IGNORECASE)
            if minutes_match is None:
                continue
            macro_minutes[normalized_name] = self._duration_to_minutes(minutes_match.group(1))
        return macro_minutes

    def _derive_subject_weights(
        self,
        raw_text: str,
        priority_groups: dict[str, list[str]],
        specific_subjects: list[str],
    ) -> dict[str, float]:
        weights: dict[str, float] = {}
        normalized_text = self._normalize_free_text(raw_text)

        main_group = priority_groups.get("1", [])
        if main_group and "sozinha vale 16 67" in normalized_text:
            weights[main_group[0]] = 16.67

        if "cinco especificas seguintes vale 10" in normalized_text:
            for subject_name in specific_subjects or priority_groups.get("2", []):
                weights[subject_name] = 10.0

        if "basicos vale 4 17 cada" in normalized_text:
            for subject_name in self._basic_subjects(priority_groups):
                weights[subject_name] = 4.17

        if "direito financeiro/goias valem 2 08 cada" in normalized_text or "direito financeiro goias valem 2 08 cada" in normalized_text:
            for subject_name in priority_groups.get("4", []):
                weights[subject_name] = 2.08

        return weights

    def _parse_daily_windows(self, raw_text: str) -> dict[str, dict[str, int]]:
        weekdays = self._extract_section(raw_text, "Dias úteis", "Fins de semana")
        weekend = self._extract_section(raw_text, "Fins de semana", "5. O que eu mudaria")
        return {
            "weekdays": self._extract_anki_window(weekdays, require_anki=True),
            "weekend": self._extract_anki_window(weekend, require_anki=False),
        }

    def _parse_priority_groups(self, raw_text: str) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        pattern = re.compile(r"^\s*(\d+)\.\s+\*\*(.+?)\*\*\s*$")
        for line in raw_text.splitlines():
            match = pattern.match(line.strip())
            if match is None:
                continue
            order = match.group(1)
            labels = [self._clean_subject_label(part) for part in match.group(2).split("/") if part.strip()]
            groups[order] = labels
        return groups

    def _parse_presets(self, raw_text: str) -> list[dict[str, Any]]:
        presets: list[dict[str, Any]] = []
        pattern = re.compile(
            r"\*\*Preset\s+(?P<number>\d+)\s+[—-]\s+(?P<name>.+?)\*\*(?P<body>.*?)(?=\n\*\*Preset\s+\d+\s+[—-]|\n\*\*\d+\.\s|\Z)",
            flags=re.DOTALL,
        )
        for match in pattern.finditer(raw_text):
            body = match.group("body")
            interval_match = re.search(r"intervalo m[aá]ximo:\s*\*\*(\d+)\s*a\s*(\d+)\s*dias\*\*", body, flags=re.IGNORECASE)
            retention_match = re.search(r"reten[cç][aã]o desejada:\s*\*\*(\d+)%\*\*", body, flags=re.IGNORECASE)
            steps_match = re.search(r"passos(?: de aprendizagem)?:\s*\*\*([^*]+)\*\*", body, flags=re.IGNORECASE)
            relearning_match = re.search(r"reaprendizagem:\s*\*\*([^*]+)\*\*", body, flags=re.IGNORECASE)
            siblings_match = re.search(r"ocultar irm[aã]os:\s*\*\*([^*]+)\*\*", body, flags=re.IGNORECASE)
            reschedule_match = re.search(r"reagendar ao alterar:\s*\*\*([^*]+)\*\*", body, flags=re.IGNORECASE)
            subjects_match = re.search(r"\(([^)]+)\)", body)
            presets.append(
                {
                    "number": int(match.group("number")),
                    "name": self._clean_subject_label(match.group("name")),
                    "desired_retention": int(retention_match.group(1)) if retention_match else None,
                    "max_interval_min_days": int(interval_match.group(1)) if interval_match else None,
                    "max_interval_max_days": int(interval_match.group(2)) if interval_match else None,
                    "learning_steps": self._clean_subject_label(steps_match.group(1)) if steps_match else None,
                    "relearning_steps": self._clean_subject_label(relearning_match.group(1)) if relearning_match else None,
                    "bury_siblings": self._clean_subject_label(siblings_match.group(1)) if siblings_match else None,
                    "reschedule_on_change": self._clean_subject_label(reschedule_match.group(1)) if reschedule_match else None,
                    "subjects": [
                        self._clean_subject_label(item)
                        for item in subjects_match.group(1).split(",")
                        if item.strip()
                    ]
                    if subjects_match
                    else [],
                }
            )
        return presets

    @staticmethod
    def _extract_section(raw_text: str, start_label: str, end_label: str | None) -> str:
        start_marker = f"**{start_label}**"
        start_index = raw_text.find(start_marker)
        if start_index < 0:
            return ""
        content_start = start_index + len(start_marker)
        if end_label is None:
            return raw_text[content_start:]
        end_marker = f"**{end_label}**"
        end_index = raw_text.find(end_marker, content_start)
        if end_index < 0:
            return raw_text[content_start:]
        return raw_text[content_start:end_index]

    def _extract_anki_window(self, section_text: str, *, require_anki: bool) -> dict[str, int]:
        for line in section_text.splitlines():
            cleaned = self._strip_markdown(line)
            if not cleaned:
                continue
            normalized_line = self._normalize_key(cleaned)
            if "revis" not in normalized_line:
                continue
            if require_anki and "anki" not in normalized_line:
                continue
            match = re.search(r"(\d+)\s*a\s*(\d+)\s*min", cleaned, flags=re.IGNORECASE)
            if match is None:
                continue
            return {
                "min_minutes": int(match.group(1)),
                "max_minutes": int(match.group(2)),
            }
        return {"min_minutes": 0, "max_minutes": 0}

    def _preset_subjects_by_name(self, presets: list[dict[str, Any]], preset_name: str) -> list[str]:
        target_key = self._normalize_key(preset_name)
        for preset in presets:
            if self._normalize_key(str(preset.get("name"))) == target_key:
                return list(preset.get("subjects", []))
        return []

    def _basic_subjects(self, priority_groups: dict[str, list[str]]) -> list[str]:
        return list(priority_groups.get("3", []))

    @staticmethod
    def _clean_subject_label(value: str) -> str:
        cleaned = value.replace("â€”", "-").replace("—", "-").replace("–", "-")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -*")
        return cleaned.strip()

    @classmethod
    def _strip_markdown(cls, value: str) -> str:
        cleaned = value.strip().lstrip("\ufeff")
        cleaned = cleaned.replace("**", "").replace("`", "")
        cleaned = cleaned.replace("â€”", "-").replace("—", "-").replace("–", "-")
        cleaned = re.sub(r"^\*\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    @classmethod
    def _normalize_key(cls, value: str | None) -> str:
        if value is None:
            return ""
        normalized = unicodedata.normalize("NFKD", value)
        ascii_like = "".join(char for char in normalized if not unicodedata.combining(char))
        ascii_like = ascii_like.casefold()
        ascii_like = re.sub(r"[^a-z0-9]+", " ", ascii_like)
        return re.sub(r"\s+", " ", ascii_like).strip()

    @classmethod
    def _normalize_free_text(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        ascii_like = "".join(char for char in normalized if not unicodedata.combining(char))
        ascii_like = ascii_like.casefold().replace("%", "")
        ascii_like = re.sub(r"[^a-z0-9/]+", " ", ascii_like)
        return re.sub(r"\s+", " ", ascii_like).strip()

    @classmethod
    def _subject_aliases(cls, subject_name: str) -> set[str]:
        key = cls._normalize_key(subject_name)
        aliases = {key}

        if "tecnologia" in key and "informacao" in key:
            aliases.update({"ti", "tecnologia da informacao", "tecnologias da informacao"})
        if "legislacao tributaria" in key:
            aliases.update({"legislacao tributaria", "legislacao tributaria estadual"})
        if "realidade" in key and "goias" in key:
            aliases.update({"goias", "realidade de goias"})
        if all(token in key for token in ("civil", "penal", "empresarial")):
            aliases.update(
                {
                    "civil empresarial penal",
                    "civil-empresarial-penal",
                    "direito civil empresarial penal",
                    "direito civil penal e empresarial",
                }
            )
        if "raciocinio logico" in key and "matematica financeira" in key and "estatistica" in key:
            aliases.update(
                {
                    "rlmf estatistica",
                    "rlmf matematica financeira estatistica",
                    "raciocinio logico matematica financeira e estatistica",
                }
            )
        if key == "lingua portuguesa":
            aliases.add("portugues")
        if key == "direito constitucional":
            aliases.add("constitucional")
        if key == "direito administrativo":
            aliases.add("administrativo")
        if key == "direito financeiro":
            aliases.add("financeiro")
        if key == "economia":
            aliases.add("economia")
        if key == "contabilidade geral":
            aliases.add("contabilidade geral")
        if key.startswith("direito tributario i"):
            aliases.update({"dt i", "direito tributario i"})
        if key.startswith("direito tributario ii"):
            aliases.update({"dt ii", "direito tributario ii"})
        if "contabilidade avancada" in key and "custos" in key:
            aliases.update({"contabilidade avancada e de custos", "contabilidade avancada custos"})
        return aliases

    @classmethod
    def _duration_to_minutes(cls, value: str) -> int:
        compact = re.sub(r"\s+", "", value.casefold())
        match = re.fullmatch(
            r"(?:(?P<hours>\d+)h(?:(?P<minutes_after_hours>\d{1,2})(?:min)?)?|(?P<minutes_only>\d+)min)",
            compact,
        )
        if match is None:
            raise StudyStrategyError(f"Formato de duracao invalido: {value}")
        hours = int(match.group("hours") or 0)
        minutes = int(match.group("minutes_after_hours") or match.group("minutes_only") or 0)
        return (hours * 60) + minutes
