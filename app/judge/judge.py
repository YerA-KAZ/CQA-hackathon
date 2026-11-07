import json

from pydantic import BaseModel

from app.judge.prompts import SYSTEM_PROMPT
from app.llm.provider import LLMProvider
from app.preprocess import PreparedTranscript
from app.schemas import AnswerType, Criterion, JudgeResult


class CriterionAssessment(BaseModel):
    criterion_id: str
    result: JudgeResult | None = None
    unreliable: bool = False


async def assess_criterion(
    provider: LLMProvider, criterion: Criterion, transcript: PreparedTranscript
) -> CriterionAssessment:
    answers = (
        [step.value for step in criterion.scale_steps]
        if criterion.answer_type == AnswerType.SCALE else ["yes", "no"]
    )
    if criterion.na_allowed:
        answers.append("n/a")
    schema = JudgeResult.model_json_schema()
    schema["properties"]["answer"] = {"enum": answers}
    prompt = json.dumps({
        "criterion": criterion.model_dump(mode="json"),
        "transcript": transcript.model_dump(mode="json"),
    }, ensure_ascii=False)

    for _ in range(2):
        raw = await provider.generate(SYSTEM_PROMPT, prompt, schema)
        try:
            result = JudgeResult.model_validate_json(raw, strict=True)
            if result.answer not in answers or result.applicable != (result.answer != "n/a"):
                raise ValueError("Invalid answer or applicability")
            if not result.applicable and not (result.na_reason or "").strip():
                raise ValueError("Missing N/A reason")
            size = len(transcript.entries)
            if any(index < 0 or index >= size for index in result.evidence_indices):
                raise ValueError("Invalid evidence index")
            if result.checked_range is not None:
                start, end = result.checked_range
                if not 0 <= start <= end < size:
                    raise ValueError("Invalid evidence range")
            return CriterionAssessment(criterion_id=criterion.id, result=result)
        except ValueError:
            prompt += "\nПредыдущий ответ не прошёл проверку. Проверь допустимые ответы и индексы."
    return CriterionAssessment(criterion_id=criterion.id, unreliable=True)
