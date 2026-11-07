import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas import Conversation, TranscriptEntry


Event = Literal[
    "identification_confirmed", "escalated", "closed_by_operator",
    "closed_by_client", "disconnected", "hold_started", "hold_ended",
]


class PreparedEntry(TranscriptEntry):
    index: int
    seconds: float
    is_duplicate: bool
    during_hold: bool


class EventFact(BaseModel):
    event: Event
    index: int


class EscalationReaction(BaseModel):
    escalation_index: int
    operator_index: int | None = None
    delay_seconds: float | None = None


class PreparedTranscript(BaseModel):
    entries: list[PreparedEntry] = Field(default_factory=list)
    events: list[EventFact] = Field(default_factory=list)
    escalation_reactions: list[EscalationReaction] = Field(default_factory=list)


def _timestamp_seconds(timestamp: str, channel: str) -> float:
    if channel == "agent chat":
        match = re.fullmatch(r"([0-9]+):([0-5][0-9])", timestamp)
        if match is None:
            raise ValueError("Expected a chat timestamp in MM:SS")
        minutes, seconds = map(int, match.groups())
        return minutes * 60 + seconds
    moment = datetime.fromisoformat(timestamp)
    if moment.utcoffset() is None:
        raise ValueError("Expected a call timestamp with a timezone")
    return moment.timestamp()


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def prepare_transcript(conversation: Conversation) -> PreparedTranscript:
    """Keep source order and extract contextual facts without making judgments."""
    result = PreparedTranscript()
    if not conversation.transcript:
        return result

    config = Path(__file__).with_name("config") / "event_patterns.json"
    patterns = json.loads(config.read_text(encoding="utf-8"))
    phrases = {
        event: tuple(_normalize(phrase) for phrase in languages.get(conversation.language, []))
        for event, languages in patterns.items()
    }
    first_timestamp = _timestamp_seconds(conversation.transcript[0].timestamp, conversation.channel)
    origin = first_timestamp if conversation.channel == "voice call" else 0
    seen = set()
    pending_reactions = []
    on_hold = False

    for index, entry in enumerate(conversation.transcript):
        seconds = round(_timestamp_seconds(entry.timestamp, conversation.channel) - origin, 6)
        normalized = _normalize(entry.text)
        fingerprint = (seconds, entry.speaker, normalized)
        result.entries.append(PreparedEntry(
            **entry.model_dump(),
            index=index,
            seconds=seconds,
            is_duplicate=fingerprint in seen,
            during_hold=on_hold and entry.speaker != "system",
        ))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)

        if entry.speaker == "operator":
            for reaction in pending_reactions:
                reaction.operator_index = index
                reaction.delay_seconds = seconds - result.entries[reaction.escalation_index].seconds
            pending_reactions.clear()
        if entry.speaker != "system":
            continue

        for event, variants in phrases.items():
            if not any(variant in normalized for variant in variants):
                continue
            result.events.append(EventFact(event=event, index=index))
            if event == "escalated":
                reaction = EscalationReaction(escalation_index=index)
                result.escalation_reactions.append(reaction)
                pending_reactions.append(reaction)
            if conversation.channel == "voice call":
                if event == "hold_started":
                    on_hold = True
                elif event == "hold_ended":
                    on_hold = False

    return result
