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


def select_part(records, part, first_count):
    if first_count is None:
        return records[part - 1::2]
    if not 0 < first_count < len(records):
        raise ValueError("Размер первой части должен быть меньше размера набора и больше нуля.")
    return records[:first_count] if part == 1 else records[first_count:]


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

    records = dataset.records
    if args.part:
        records = select_part(sorted(records, key=lambda item: item.conversation_id), args.part, args.first_count)
    results = []
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        provider = OllamaProvider(client, os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"), model)
        for index, conversation in enumerate(records, 1):
            try:
                result = await evaluate(conversation, criteria, provider)
            except httpx.HTTPError:
                result = error_record(conversation.conversation_id, "Не удалось получить ответ Ollama.")
            except (ValueError, KeyError, TypeError, OverflowError):
                result = error_record(conversation.conversation_id, "Не удалось обработать диалог или подтвердить оценку.")
            results.append(result)
            print(f"{index}/{len(records)} {conversation.conversation_id}: {result['status']}", flush=True)

    path = (
        ROOT / "data" / f"{args.split}-part-{args.part}.json"
        if args.part else ROOT / "results" / args.split / "results.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    payload = {
        "schema_version": "1.0",
        "team_name": args.team.strip(),
        "model": {"mode": "local", "name": model},
        "results": results,
    }
    if args.part:
        payload.update(group_id=dataset.dataset_group_id, part=args.part, source_ids=sorted(ids), first_count=args.first_count)
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    errors = sum(result["status"] == "error" for result in results)
    print(f"{path}: {len(results)} записей, {errors} ошибок")


def merge_parts(split):
    parts = [
        json.loads((ROOT / "data" / f"{split}-part-{index}.json").read_text(encoding="utf-8"))
        for index in (1, 2)
    ]
    first, second = parts
    header = ("schema_version", "team_name", "model", "group_id", "source_ids")
    if any(first[key] != second[key] for key in header):
        raise ValueError("Части должны иметь одинаковый набор, команду и модель.")
    first_count = first.get("first_count")
    if first_count != second.get("first_count"):
        raise ValueError("Обе части должны использовать одинаковое разделение.")
    source_ids = first["source_ids"]
    if len(source_ids) != COUNTS[split] or source_ids != sorted(set(source_ids)):
        raise ValueError("Неверный список исходных ID.")
    results = []
    for index, part in enumerate(parts):
        rows = part["results"]
        if part["part"] != index + 1 or [row["conversation_id"] for row in rows] != select_part(source_ids, index + 1, first_count):
            raise ValueError(f"В части {index + 1} есть пропуски, дубликаты или неверные ID.")
        results.extend(rows)
    payload = {key: first[key] for key in ("schema_version", "team_name", "model")}
    payload["results"] = sorted(results, key=lambda row: row["conversation_id"])
    path = ROOT / "results" / split / "results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    print(f"{path}: {len(results)} записей, {sum(row['status'] == 'error' for row in results)} ошибок")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("split", choices=COUNTS)
    parser.add_argument("--group-id")
    parser.add_argument("--team")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--part", type=int, choices=(1, 2))
    mode.add_argument("--merge", action="store_true")
    parser.add_argument("--first-count", type=int, help="Number of dialogues assigned to part 1")
    args = parser.parse_args()
    if args.first_count is not None and (not args.part or not 0 < args.first_count < COUNTS[args.split]):
        parser.error("--first-count requires --part and must be smaller than the dataset.")
    if args.merge:
        try:
            merge_parts(args.split)
        except (OSError, ValueError, KeyError, TypeError) as error:
            parser.exit(1, f"{error}\n")
        return
    if not args.group_id or not args.team:
        parser.error("Укажите --group-id и --team.")
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
