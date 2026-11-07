import json
from decimal import Decimal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.schemas import Answer
from app.scoring import score_total


class AppealRequest(BaseModel):
    comment: str = Field(min_length=1)


class AppealDecision(BaseModel):
    answer: Answer
    reason: str = Field(min_length=1)


def summarize(criteria, result):
    answers = {
        item["criterion_id"]: item["result"]["answer"]
        for item in result["criteria"] if item["result"] is not None
    }
    for criterion_id, appeal in result.get("appeals", {}).items():
        if "decision" in appeal:
            answers[criterion_id] = appeal["decision"]["answer"]
    reliable = len(answers) == len(criteria)
    total = score_total(criteria, answers) if reliable else None
    result.update(
        total=str(total) if total is not None else None,
        flag=answers.get("08"),
        unreliable=not reliable,
        final_answers=answers,
    )
    return result


def update_appeal(storage, criteria, conversation_id, criterion_id, request):
    row = storage.get_record(conversation_id)
    if row is None or row[1] is None:
        raise HTTPException(404, "Оценка не найдена")
    payload, saved = row
    result = json.loads(saved)
    if criterion_id not in {item["criterion_id"] for item in result["criteria"]}:
        raise HTTPException(404, "Критерий не найден")
    appeals = result.setdefault("appeals", {})
    current = appeals.get(criterion_id)
    if isinstance(request, AppealRequest):
        if current is not None:
            raise HTTPException(409, "Апелляция уже существует")
        appeals[criterion_id] = {"status": "pending", "comment": request.comment}
    else:
        if current is None or current["status"] != "pending":
            raise HTTPException(409, "Нет открытой апелляции")
        criterion = next((item for item in criteria if item.id == criterion_id), None)
        if criterion is None:
            raise HTTPException(409, "Чек-лист изменился")
        try:
            score_total([criterion], {criterion_id: request.answer})
        except ValueError:
            raise HTTPException(422, "Ответ не предусмотрен критерием") from None
        current.update(status="resolved", decision=request.model_dump())
    result = summarize(criteria, result)
    if not storage.save_assessment(conversation_id, payload, json.dumps(result, ensure_ascii=False), saved):
        raise HTTPException(409, "Оценка изменилась")
    return result


def score_diff(previous, current):
    before, after = previous.get("total"), current.get("total")
    delta = Decimal(after) - Decimal(before) if before is not None and after is not None else None
    return {"before": before, "after": after, "delta": str(delta) if delta is not None else None}
