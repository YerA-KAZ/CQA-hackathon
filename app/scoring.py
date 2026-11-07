from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from app.schemas import Answer, AnswerType, Criterion


def score_total(
    criteria: Sequence[Criterion], answers: Mapping[str, Answer]
) -> Decimal | None:
    """Return the normalized score for a complete set of criterion answers."""
    ids = {criterion.id for criterion in criteria}
    if len(ids) != len(criteria) or ids != set(answers):
        raise ValueError("Answers must match a checklist with unique criterion IDs")

    earned = Fraction(0)
    applicable_weight = 0
    for criterion in criteria:
        answer = answers[criterion.id]
        if answer == "n/a":
            if not criterion.na_allowed:
                raise ValueError(f"N/A is not allowed for {criterion.id}")
            continue

        if criterion.answer_type == AnswerType.SCALE:
            steps = {step.value for step in criterion.scale_steps}
            if type(answer) is not int or answer not in steps:
                raise ValueError(f"Invalid scale answer for {criterion.id}: {answer!r}")
            points = Fraction(answer * criterion.weight, max(steps))
        else:
            if answer not in ("yes", "no"):
                raise ValueError(f"Invalid binary answer for {criterion.id}: {answer!r}")
            points = criterion.weight if answer == "yes" else 0

        earned += points
        applicable_weight += criterion.weight

    if not applicable_weight:
        return None

    # Round exact rational scores to hundredths without intermediate rounding.
    cents = earned * 10_000 / applicable_weight
    whole, remainder = divmod(cents.numerator, cents.denominator)
    rounded = Decimal(whole + (2 * remainder >= cents.denominator)).scaleb(-2)
    return rounded.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

