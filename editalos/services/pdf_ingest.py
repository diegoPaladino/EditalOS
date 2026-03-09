from __future__ import annotations

from pathlib import Path
from typing import Any

from pypdf import PdfReader
from sqlalchemy.orm import Session

from editalos.enums import StudyMaterialType
from editalos.models import StudyMaterial
from editalos.services.openai_service import OpenAIService


EDITAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "exam_name": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "question_count": {"type": ["integer", "null"]},
                    "weight": {"type": ["number", "null"]},
                    "topics": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["subject", "question_count", "weight", "topics"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["exam_name", "sections"],
    "additionalProperties": False,
}


class PDFIngestService:
    def __init__(self, session: Session, openai_service: OpenAIService | None = None) -> None:
        self.session = session
        self.openai_service = openai_service

    @staticmethod
    def extract_text(path: str | Path) -> str:
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)

    def import_pdf(self, path: str | Path, title: str, topic_id: int | None = None) -> StudyMaterial:
        source_path = Path(path)
        raw_text = self.extract_text(source_path)
        material = StudyMaterial(
            topic_id=topic_id,
            title=title,
            material_type=StudyMaterialType.PDF.value,
            source_path=str(source_path),
            raw_text=raw_text,
        )
        self.session.add(material)
        self.session.flush()
        return material

    def extract_edital_structure(self, raw_text: str) -> dict[str, Any]:
        if not self.openai_service:
            raise RuntimeError("OpenAIService não configurado para extração estruturada do edital.")
        instructions = (
            "Você é um parser de edital para o EditalOS. "
            "Extraia somente estrutura útil para estudo. "
            "Não invente dados ausentes. "
            "Mantenha o máximo de fidelidade ao documento."
        )
        input_text = raw_text[:120000]
        return self.openai_service.structured(
            instructions=instructions,
            input_text=input_text,
            schema_name="edital_structure",
            schema=EDITAL_SCHEMA,
            cache_suffix="edital-structure-v1",
            use_flex=True,
        )
