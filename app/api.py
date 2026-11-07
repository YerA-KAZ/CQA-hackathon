import asyncio
import json
from hashlib import sha256
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import TypeAdapter

from app.judge.judge import CriterionAssessment, assess_criterion
from app.judge.prompts import SYSTEM_PROMPT
from app.llm.ollama import OllamaProvider
from app.llm.provider import LLMProvider
from app.loader import DatasetLoadError, load_dataset
from app.preprocess import prepare_transcript
from app.schemas import Conversation, Criterion
from app.appeals import AppealDecision, AppealRequest, score_diff, summarize, update_appeal
from app.storage import Storage


def create_app(
    storage: Storage | None = None, provider: LLMProvider | None = None
) -> FastAPI:
    app = FastAPI(title="CQA")
    storage = storage or Storage(Path(os.getenv("CQA_DB_PATH", "data/cqa.sqlite3")))
    checklist_path = Path(__file__).with_name("config") / "checklist.default.json"
    adapter = TypeAdapter(list[Criterion])
    default_criteria = adapter.validate_json(checklist_path.read_bytes())

    def current_checklist():
        version, saved = storage.checklist()
        return version, adapter.validate_json(saved) if saved else default_criteria

    def find_record(conversation_id):
        row = storage.get_record(conversation_id)
        if row is None:
            raise HTTPException(404, "Диалог не найден")
        return row

    @app.post("/api/datasets/{group_id}")
    async def import_dataset(group_id: str):
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                dataset = await load_dataset(client, group_id)
            except DatasetLoadError as error:
                raise HTTPException(502, str(error)) from None
        storage.import_records(dataset.records)
        return {"group_id": group_id, "imported": len(dataset.records)}

    @app.get("/api/checklist")
    def get_checklist():
        return current_checklist()[1]

    @app.get("/api/conversations")
    def list_conversations():
        items = []
        for payload, saved in storage.list_records():
            item = Conversation.model_validate_json(payload)
            assessment = json.loads(saved) if saved else {}
            items.append({
                "conversation_id": item.conversation_id,
                "channel": item.channel,
                "language": item.language,
                "total": assessment.get("total"),
                "flag": assessment.get("flag"),
                "unreliable": assessment.get("unreliable"),
            })
        return items

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation(conversation_id: str):
        payload, saved = find_record(conversation_id)
        return {"conversation": json.loads(payload), "assessment": json.loads(saved) if saved else None}

    async def evaluate_record(payload, criteria, previous):
        conversation = Conversation.model_validate_json(payload)
        try:
            transcript = prepare_transcript(conversation)
        except ValueError:
            raise HTTPException(422, "Некорректные метки времени") from None

        async def evaluate(active_provider):
            assessments, keys = [], {}
            for criterion in criteria:
                material = json.dumps([
                    sha256(transcript.model_dump_json().encode()).hexdigest(),
                    criterion.model_dump(mode="json"), SYSTEM_PROMPT, active_provider.model,
                ], sort_keys=True)
                key = sha256(material.encode()).hexdigest()
                keys[criterion.id] = key
                cached = storage.cached(key)
                if cached:
                    assessment = CriterionAssessment.model_validate_json(cached)
                else:
                    assessment = await assess_criterion(active_provider, criterion, transcript)
                    if not assessment.unreliable:
                        storage.cache(key, assessment.model_dump_json())
                assessments.append(assessment)
            return assessments, keys

        try:
            if provider is not None:
                assessments, keys = await evaluate(provider)
            else:
                model = os.getenv("OLLAMA_MODEL")
                if not model:
                    raise HTTPException(503, "Укажите OLLAMA_MODEL")
                url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
                async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
                    assessments, keys = await evaluate(OllamaProvider(client, url, model))
        except (httpx.HTTPError, KeyError, ValueError):
            raise HTTPException(502, "Ошибка ответа Ollama") from None

        appeals = {
            criterion_id: appeal for criterion_id, appeal in previous.get("appeals", {}).items()
            if criterion_id in keys and previous.get("keys", {}).get(criterion_id) == keys[criterion_id]
        }
        return summarize(criteria, {
            "criteria": [assessment.model_dump(mode="json") for assessment in assessments],
            "keys": keys,
            "appeals": appeals,
            "previous_appeals": previous.get("previous_appeals", []) + [
                {"criterion_id": criterion_id, **appeal}
                for criterion_id, appeal in previous.get("appeals", {}).items()
                if criterion_id not in appeals
            ],
        })

    @app.post("/api/conversations/{conversation_id}/assess")
    async def assess(conversation_id: str, request: Request = None):
        payload, saved = find_record(conversation_id)
        version, criteria = current_checklist()
        evaluation = evaluate_record(payload, criteria, json.loads(saved) if saved else {})
        if request is None:
            result = await evaluation
        else:
            task = asyncio.create_task(evaluation)
            try:
                while not task.done():
                    if await request.is_disconnected():
                        raise HTTPException(499, "Оценка остановлена")
                    await asyncio.wait({task}, timeout=0.2)
                if await request.is_disconnected():
                    raise HTTPException(499, "Оценка остановлена")
                result = task.result()
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                elif not task.cancelled():
                    task.exception()
        if current_checklist()[0] != version or not storage.save_assessment(
            conversation_id, payload, json.dumps(result, ensure_ascii=False), saved
        ):
            raise HTTPException(409, "Данные изменились во время оценки")
        return result

    @app.post("/api/conversations/{conversation_id}/appeals/{criterion_id}")
    def open_appeal(conversation_id: str, criterion_id: str, request: AppealRequest):
        return update_appeal(storage, current_checklist()[1], conversation_id, criterion_id, request)

    @app.put("/api/conversations/{conversation_id}/appeals/{criterion_id}")
    def decide_appeal(conversation_id: str, criterion_id: str, request: AppealDecision):
        return update_appeal(storage, current_checklist()[1], conversation_id, criterion_id, request)

    @app.put("/api/checklist")
    async def apply_checklist(proposed: list[Criterion]):
        if not proposed or len({criterion.id for criterion in proposed}) != len(proposed):
            raise HTTPException(422, "Укажите непустой чек-лист с уникальными ID")
        version, _ = current_checklist()
        updates, differences = [], []
        for payload, saved in storage.list_records():
            previous = json.loads(saved) if saved else {}
            result = await evaluate_record(payload, proposed, previous)
            conversation_id = Conversation.model_validate_json(payload).conversation_id
            updates.append((conversation_id, payload, saved, json.dumps(result, ensure_ascii=False)))
            difference = score_diff(previous, result)
            if difference["before"] != difference["after"]:
                differences.append({"conversation_id": conversation_id, **difference})
        if not storage.apply_checklist(version, adapter.dump_json(proposed).decode(), updates):
            raise HTTPException(409, "Данные изменились во время пересчёта")
        return {"version": version + 1, "diff": differences}

    app.mount("/", StaticFiles(directory=Path(__file__).resolve().parents[1] / "web", html=True), name="web")

    return app


app = create_app()
