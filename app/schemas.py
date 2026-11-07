from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AnswerType(StrEnum):
    YES_NO = "yes_no"
    SCALE = "scale"
    FLAG = "flag"


Answer = Literal["yes", "no", "n/a"] | int


class ScaleStep(BaseModel):
    value: int = Field(ge=0)
    description: str


class SubQuestion(BaseModel):
    id: str
    question: str


class Criterion(BaseModel):
    id: str = Field(min_length=1)
    name: str
    description: str
    answer_type: AnswerType
    weight: int = Field(ge=0)
    scale_steps: list[ScaleStep] = Field(default_factory=list)
    na_allowed: bool = False
    na_condition: str | None = None
    section: str
    version: int = Field(ge=1)
    sub_questions: list[SubQuestion] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_rules(self):
        values = [step.value for step in self.scale_steps]
        if self.answer_type == AnswerType.SCALE:
            if not values or max(values) == 0 or len(set(values)) != len(values):
                raise ValueError("A scale needs unique steps and a positive maximum")
        elif values:
            raise ValueError("Only scale criteria can have scale steps")
        if self.answer_type == AnswerType.FLAG and self.weight != 0:
            raise ValueError("Flags must have zero weight")
        if self.na_allowed != bool(self.na_condition):
            raise ValueError("An N/A condition is required exactly when N/A is allowed")
        return self


class TranscriptEntry(BaseModel):
    timestamp: str
    speaker: Literal["client", "operator", "bot", "system"]
    text: str


class Conversation(BaseModel):
    conversation_id: str
    channel: Literal["agent chat", "voice call"]
    language: Literal["RU", "KZ", "EN"]
    transcript: list[TranscriptEntry]
    audio_url: str | None = Field(default=None, repr=False)


class JudgeResult(BaseModel):
    evidence_indices: list[int] = Field(default_factory=list)
    checked_range: tuple[int, int] | None = None
    reasoning: str
    applicable: bool
    answer: Answer
    na_reason: str | None = None

