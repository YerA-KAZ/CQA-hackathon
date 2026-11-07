"""Generate an official CQA submission from an organizer dataset."""

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from pydantic import TypeAdapter

from app.judge.judge import assess_criterion
from app.llm.ollama import OllamaProvider
from app.loader import DatasetLoadError, load_dataset
from app.preprocess import prepare_transcript
from app.schemas import Criterion
from app.scoring import score_total


CODES = {
    "01": "contact", "02": "discovery", "03": "identification",
    "04": "consultation", "05": "empathy", "06": "dialogue",
    "07": "closure", "08": "critical",
}
COUNTS = {"dev": 65, "prod": 10}
ROOT = Path(__file__).resolve().parents[1]


def submission_value(answer):
    if isinstance(answer, int):
        return answer
    return {"yes": True, "no": False, "n/a": None}[answer]


async def evaluate(conversation, criteria, provider):
    transcript = prepare_transcript(conversation)
    answers = {}
    rows = []
    for criterion in criteria:
        assessment = await assess_criterion(provider, criterion, transcript)
        result = assessment.result
        if result is None or assessment.unreliable:
            raise ValueError(f"Не удалось подтвердить оценку критерия {CODES[criterion.id]}.")
        answers[criterion.id] = result.answer
        indices = set(result.evidence_indices)
        if result.checked_range is not None:
            start, end = result.checked_range
            indices.update(range(start, end + 1))
        rationale = " ".join(filter(None, (result.reasoning.strip(), result.na_reason)))
        if not rationale:
            raise ValueError(f"Нет обоснования для критерия {CODES[criterion.id]}.")
        rows.append({
            "criterion_id": CODES[criterion.id],
            "value": submission_value(result.answer),
            "rationale": rationale,
            "evidence_turns": [index + 1 for index in sorted(indices)],
        })
    return {
        "conversation_id": conversation.conversation_id,
        "status": "ok",
        "criteria": rows,
        "score": {
            "final_score": float(score_total(criteria, answers)),
            "critical_flag": answers["08"] == "yes",
        },
    }


def error_record(conversation_id, reason):
    return {
        "conversation_id": conversation_id,
        "status": "error",
        "criteria": [],
        "score": None,
        "error": reason,
    }


async def generate(args, model):
    criteria = TypeAdapter(list[Criterion]).validate_json(
        (ROOT / "app/config/checklist.default.json").read_bytes()
    )
    if [criterion.id for criterion in criteria] != list(CODES):
        raise ValueError("Стандартный чек-лист должен содержать критерии 01–08 по порядку.")

    async with httpx.AsyncClient(timeout=60) as client:
        dataset = await load_dataset(client, args.group_id)
    expected = COUNTS[args.split]
    ids = {conversation.conversation_id for conversation in dataset.records}
    if dataset.total_records != expected or len(dataset.records) != expected or len(ids) != expected:
        raise ValueError(f"Ожидалось {expected} диалогов с уникальными ID для {args.split}.")
    chats = sum(conversation.channel == "agent chat" for conversation in dataset.records)
    if chats != (60 if args.split == "dev" else 10):
        raise ValueError(f"Состав набора {args.split} не соответствует гайду.")

    results = []
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        provider = OllamaProvider(client, os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"), model)
        for index, conversation in enumerate(dataset.records, 1):
            try:
                result = await evaluate(conversation, criteria, provider)
            except httpx.HTTPError:
                result = error_record(conversation.conversation_id, "Не удалось получить ответ Ollama.")
            except (ValueError, KeyError, TypeError, OverflowError):
                result = error_record(conversation.conversation_id, "Не удалось обработать диалог или подтвердить оценку.")
            results.append(result)
            print(f"{index}/{expected} {conversation.conversation_id}: {result['status']}", flush=True)

    path = ROOT / "results" / args.split / "results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    payload = {
        "schema_version": "1.0",
        "team_name": args.team.strip(),
        "model": {"mode": "local", "name": model},
        "results": results,
    }
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    errors = sum(result["status"] == "error" for result in results)
    print(f"{path}: {len(results)} записей, {errors} ошибок")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("split", choices=COUNTS)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--team", required=True)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    model = os.getenv("OLLAMA_MODEL", "").strip()
    if not args.team.strip() or not model:
        parser.error("Укажите название команды и OLLAMA_MODEL в .env.")
    try:
        asyncio.run(generate(args, model))
    except (DatasetLoadError, ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
