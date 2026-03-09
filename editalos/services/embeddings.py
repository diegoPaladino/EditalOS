from __future__ import annotations

import math
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from editalos.config import get_settings
from editalos.enums import EntityType
from editalos.models import Card, EmbeddingVector, StudyMaterial, Topic
from editalos.services.openai_service import OpenAIService


settings = get_settings()


class EmbeddingService:
    def __init__(self, session: Session, openai_service: OpenAIService | None = None) -> None:
        self.session = session
        self.openai_service = openai_service or OpenAIService()

    @staticmethod
    def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 150) -> list[str]:
        cleaned = " ".join(text.split())
        if not cleaned:
            return []
        chunks: list[str] = []
        start = 0
        while start < len(cleaned):
            end = min(start + chunk_size, len(cleaned))
            chunks.append(cleaned[start:end])
            if end >= len(cleaned):
                break
            start = max(end - overlap, 0)
        return chunks

    def reindex_material(self, material_id: int) -> int:
        material = self.session.get(StudyMaterial, material_id)
        if material is None or not material.raw_text:
            return 0
        self.session.execute(delete(EmbeddingVector).where(EmbeddingVector.material_id == material_id))
        chunks = self.chunk_text(material.raw_text)
        if not chunks:
            return 0
        vectors = self.openai_service.embeddings(chunks)
        for idx, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            self.session.add(
                EmbeddingVector(
                    entity_type=EntityType.MATERIAL.value,
                    entity_id=material.id,
                    material_id=material.id,
                    model_name=settings.openai_embedding_model,
                    chunk_index=idx,
                    chunk_text=chunk,
                    vector_json=vector,
                    topic_id=material.topic_id,
                )
            )
        return len(chunks)

    def reindex_card(self, card_id: int) -> int:
        card = self.session.get(Card, card_id)
        if card is None:
            return 0
        self.session.execute(delete(EmbeddingVector).where(EmbeddingVector.card_id == card_id))
        combined = f"Frente: {card.front}\nVerso: {card.back}"
        vectors = self.openai_service.embeddings([combined])
        self.session.add(
            EmbeddingVector(
                entity_type=EntityType.CARD.value,
                entity_id=card.id,
                card_id=card.id,
                topic_id=card.topic_id,
                model_name=settings.openai_embedding_model,
                chunk_index=0,
                chunk_text=combined,
                vector_json=vectors[0],
            )
        )
        return 1

    def reindex_topic(self, topic_id: int) -> int:
        topic = self.session.get(Topic, topic_id)
        if topic is None:
            return 0
        self.session.execute(
            delete(EmbeddingVector)
            .where(EmbeddingVector.entity_type == EntityType.TOPIC.value)
            .where(EmbeddingVector.entity_id == topic_id)
        )
        text = f"Disciplina: {topic.subject.name}\nTópico: {topic.name}\nDescrição: {topic.description or ''}"
        vectors = self.openai_service.embeddings([text])
        self.session.add(
            EmbeddingVector(
                entity_type=EntityType.TOPIC.value,
                entity_id=topic.id,
                topic_id=topic.id,
                model_name=settings.openai_embedding_model,
                chunk_index=0,
                chunk_text=text,
                vector_json=vectors[0],
            )
        )
        return 1

    def semantic_search(self, query: str, top_k: int = 5) -> list[dict[str, object]]:
        [query_vector] = self.openai_service.embeddings([query])
        rows = self.session.scalars(select(EmbeddingVector)).all()
        scored = []
        for row in rows:
            similarity = self.cosine_similarity(query_vector, row.vector_json)
            scored.append(
                {
                    "embedding_id": row.id,
                    "entity_type": row.entity_type,
                    "entity_id": row.entity_id,
                    "topic_id": row.topic_id,
                    "chunk_text": row.chunk_text,
                    "similarity": similarity,
                }
            )
        scored.sort(key=lambda item: float(item["similarity"]), reverse=True)
        return scored[:top_k]

    @staticmethod
    def cosine_similarity(a: Iterable[float], b: Iterable[float]) -> float:
        a_list = list(a)
        b_list = list(b)
        if len(a_list) != len(b_list) or not a_list:
            return 0.0
        dot = sum(x * y for x, y in zip(a_list, b_list, strict=True))
        norm_a = math.sqrt(sum(x * x for x in a_list))
        norm_b = math.sqrt(sum(y * y for y in b_list))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
