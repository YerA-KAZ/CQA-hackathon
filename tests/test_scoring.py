import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from app.schemas import Criterion
from app.scoring import score_total


@pytest.fixture
def checklist():
    path = Path(__file__).resolve().parents[1] / "app/config/checklist.default.json"
    return TypeAdapter(list[Criterion]).validate_python(json.loads(path.read_text(encoding="utf-8")))


def test_default_checklist_redistributes_weight_and_excludes_flag(checklist):
    answers = dict(zip((item.id for item in checklist), ["yes", "n/a", "yes", 15, "yes", 7, "no", "yes"]))
    assert score_total(checklist, answers) == Decimal("82.94")
    answers["08"] = "no"
    assert score_total(checklist, answers) == Decimal("82.94")
    answers["02"] = "no"
    assert score_total(checklist, answers) == Decimal("70.50")


def test_all_na_and_one_applicable(checklist):
    criteria = [item for item in checklist if item.na_allowed]
    answers = {item.id: "n/a" for item in criteria}
    assert score_total(criteria, answers) is None
    answers["04"] = 15
    assert score_total(criteria, answers) == Decimal("75.00")


def test_rounds_exact_half_up(checklist):
    first = checklist[0].model_copy(update={"weight": 1})
    second = checklist[4].model_copy(update={"weight": 31})
    assert score_total([first, second], {"01": "yes", "05": "no"}) == Decimal("3.13")


@pytest.mark.parametrize("answer", [6, True, "7", "n/a"])
def test_rejects_invalid_scale_answer(checklist, answer):
    with pytest.raises(ValueError):
        score_total([checklist[5]], {"06": answer})


def test_rejects_missing_answer(checklist):
    with pytest.raises(ValueError):
        score_total(checklist, {})

