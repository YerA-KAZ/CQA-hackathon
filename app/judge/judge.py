import json

from pydantic import BaseModel, ValidationError

from app.judge.prompts import SYSTEM_PROMPT
from app.llm.provider import LLMProvider
from app.preprocess import PreparedTranscript
from app.schemas import AnswerType, Criterion, JudgeResult


class CriterionAssessment(BaseModel):
    criterion_id: str
    result: JudgeResult | None = None
    unreliable: bool = False
    failure_reason: str | None = None


def validate_evidence(evidence, checked_range, transcript):
    size = len(transcript.entries)
    if checked_range is not None:
        start, end = checked_range
        if not 0 <= start <= end < size:
            raise ValueError("Invalid checked range")
    if not evidence and checked_range is None:
        raise ValueError("Evidence or a checked range is required")
    for quote in evidence:
        if not 0 <= quote.index < size:
            raise ValueError("Invalid quote index")
        source = " ".join(transcript.entries[quote.index].text.split())
        text = " ".join(quote.quote.split())
        if not text or text not in source:
            raise ValueError("Quote does not match its source turn")


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
    schema["properties"].pop("evidence_indices")
    schema["properties"].pop("applicable")
    schema["required"].remove("applicable")
    if not criterion.sub_questions:
        schema["properties"]["sub_answers"] = {"type": "array", "maxItems": 0}
    schema["properties"]["checked_range"]["description"] = (
        "Inclusive [first_index, last_index] from the transcript when evidence is empty. "
        "Use null only when you provide a literal evidence quote."
    )
    schema["required"] = list(dict.fromkeys(
        schema.get("required", []) + ["evidence", "checked_range", "sub_answers"]
    ))
    if criterion.na_allowed:
        schema["required"].append("na_reason")
    prompt = json.dumps({
        "criterion": criterion.model_dump(mode="json"),
        "transcript": transcript.model_dump(mode="json"),
    }, ensure_ascii=False)

    for _ in range(2):
        raw = await provider.generate(SYSTEM_PROMPT, prompt, schema)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object")
            payload["applicable"] = payload.get("answer") != "n/a"
            result = JudgeResult.model_validate_json(json.dumps(payload), strict=True)
            validate_evidence(result.evidence, result.checked_range, transcript)
            if not result.reasoning.strip():
                raise ValueError("Missing explanation")
            result.evidence_indices = sorted({quote.index for quote in result.evidence})
            if criterion.sub_questions and result.applicable:
                responses = {answer.id: answer for answer in result.sub_answers}
                expected = {question.id for question in criterion.sub_questions}
                if set(responses) != expected or len(responses) != len(result.sub_answers):
                    raise ValueError("Answer every subquestion exactly once")
                matched_scores = []
                for question in criterion.sub_questions:
                    answer = responses[question.id]
                    if answer.answer or answer.evidence or answer.checked_range is not None:
                        validate_evidence(answer.evidence, answer.checked_range, transcript)
                    if answer.answer:
                        matched_scores.append(question.score_if_yes)
                if not matched_scores:
                    raise ValueError("No scale level is supported")
                result.answer = min(matched_scores)
            elif result.sub_answers:
                raise ValueError("Unexpected subquestion answers")
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
        except ValueError as error:
            if isinstance(error, ValidationError):
                fields = ", ".join(
                    ".".join(map(str, item["loc"])) or "JSON"
                    for item in error.errors(include_input=False)
                )
                reason = f"Неверный формат полей: {fields}"
            else:
                reason = str(error)
            prompt += f"\nИсправь конкретную ошибку предыдущего ответа: {reason}."
    return CriterionAssessment(criterion_id=criterion.id, unreliable=True, failure_reason=reason)
