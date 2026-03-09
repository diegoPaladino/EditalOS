from __future__ import annotations

from enum import Enum


class SRSAlgorithm(str, Enum):
    FSRS = "fsrs"
    SM2 = "sm2"


class CardStatus(str, Enum):
    NEW = "new"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    BURIED = "buried"


class StudyMaterialType(str, Enum):
    PDF = "pdf"
    MARKDOWN = "markdown"
    NOTE = "note"
    URL = "url"
    TRANSCRIPT = "transcript"


class ReviewRating(str, Enum):
    AGAIN = "again"
    HARD = "hard"
    GOOD = "good"
    EASY = "easy"


class EntityType(str, Enum):
    MATERIAL = "material"
    TOPIC = "topic"
    CARD = "card"
    NOTE = "note"
