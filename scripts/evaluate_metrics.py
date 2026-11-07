"""Compare saved CQA assessments with independently reviewed reference labels."""

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from statistics import mean


def load_predictions(path, final):
    predictions = {}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for payload, saved in db.execute("SELECT payload, assessment FROM conversations"):
            conversation = json.loads(payload)
            result = json.loads(saved) if saved else {}
            answers = {
                item["criterion_id"]: item["result"]["answer"]
                for item in result.get("criteria", []) if item["result"] is not None
            }
            if final:
                answers.update(result.get("final_answers", {}))
            predictions[conversation["conversation_id"]] = answers
    return predictions


def evaluate(reference, predictions):
    groups = defaultdict(lambda: defaultdict(list))
    seen = set()
    for row in reference:
        identifier = row["conversation_id"]
        if identifier in seen:
            raise ValueError(f"Duplicate reference dialogue: {identifier}")
        seen.add(identifier)
        language = row["language"]
        if language not in {"RU", "KZ", "EN"}:
            raise ValueError(f"Unknown reference language: {language}")
        predicted = predictions.get(identifier, {})
        for criterion, expected in row["answers"].items():
            if type(expected) is not int and expected not in ("yes", "no", "n/a"):
                raise ValueError(f"Invalid reference answer for {identifier}/{criterion}")
            pair = (expected, predicted.get(criterion))
            groups["all"][criterion].append(pair)
            groups[language][criterion].append(pair)

    return {
        group: {criterion: summarize(pairs, criterion == "08") for criterion, pairs in sorted(items.items())}
        for group, items in sorted(groups.items())
    }


def summarize(pairs, flag):
    counts = Counter()
    scale_errors = []
    for expected, predicted in pairs:
        counts["reference"] += 1
        if predicted is None:
            counts["missing"] += 1
            continue
        counts["scored"] += 1
        counts["exact"] += expected == predicted
        counts["na_tp"] += expected == predicted == "n/a"
        counts["na_fp"] += predicted == "n/a" and expected != "n/a"
        counts["na_fn"] += expected == "n/a" and predicted != "n/a"
        if type(expected) is int and type(predicted) is int:
            scale_errors.append(abs(expected - predicted))
        if flag:
            counts["tp"] += expected == predicted == "yes"
            counts["fp"] += expected != "yes" and predicted == "yes"
            counts["fn"] += expected == "yes" and predicted != "yes"

    result = {
        "reference": counts["reference"],
        "scored": counts["scored"],
        "missing": counts["missing"],
        "exact": counts["exact"],
        "agreement": counts["exact"] / counts["reference"],
        "na": {key: counts["na_" + key] for key in ("tp", "fp", "fn")},
        "scale_mae": mean(scale_errors) if scale_errors else None,
        "scale_pairs": len(scale_errors),
    }
    if flag:
        result["flag"] = {key: counts[key] for key in ("tp", "fp", "fn")}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--db", type=Path, default=Path("data/cqa.sqlite3"))
    parser.add_argument("--final", action="store_true", help="Include reviewer decisions")
    args = parser.parse_args()
    try:
        reference = json.loads(args.reference.read_text(encoding="utf-8-sig"))
        if not isinstance(reference, list) or not reference:
            raise ValueError("Reference must be a non-empty JSON array")
        metrics = evaluate(reference, load_predictions(args.db, args.final))
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"Cannot evaluate: {error}\n")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
