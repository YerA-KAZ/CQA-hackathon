import httpx
import pytest

from app.loader import DatasetLoadError, load_dataset
from app.preprocess import prepare_transcript
from app.schemas import Conversation, TranscriptEntry


def run_mock_request(coroutine):
    # MockTransport completes inline, without a Windows loopback socket.
    with pytest.raises(StopIteration) as completed:
        coroutine.send(None)
    return completed.value.value



def make_conversation(rows, channel="agent chat", language="KZ"):
    return Conversation(
        conversation_id="fixture-1", channel=channel, language=language,
        transcript=[TranscriptEntry(timestamp=t, speaker=s, text=m) for t, s, m in rows],
    )


def test_system_events_and_escalation_ignore_bot_and_preserve_duplicates():
    conversation = make_conversation([
        ("00:02", "system", "Операторға жіберілді."),
        ("00:03", "bot", "Подождите"),
        ("00:04", "operator", "Сәлеметсіз бе!"),
        ("00:04", "operator", "Сәлеметсіз бе!"),
        ("00:04", "system", "Жеке басы расталды"),
        ("00:30", "system", "Байланыс үзілді"),
    ])
    prepared = prepare_transcript(conversation)
    assert [entry.index for entry in prepared.entries] == list(range(6))
    assert prepared.entries[3].is_duplicate
    assert prepared.escalation_reactions[0].operator_index == 2
    assert prepared.escalation_reactions[0].delay_seconds == 2
    assert [(fact.event, fact.index) for fact in prepared.events] == [
        ("escalated", 0), ("identification_confirmed", 4), ("disconnected", 5),
    ]


def test_call_hold_and_timezones():
    conversation = make_conversation([
        ("2026-01-01T12:00:00+05:00", "system", "Вызов поставлен на удержание"),
        ("2026-01-01T07:00:01Z", "operator", "Внутренняя реплика"),
        ("2026-01-01T12:00:02+05:00", "system", "Вызов снят с удержания"),
        ("2026-01-01T12:00:03+05:00", "operator", "Спасибо за ожидание"),
    ], channel="voice call", language="RU")
    entries = prepare_transcript(conversation).entries
    assert [entry.seconds for entry in entries] == [0, 1, 2, 3]
    assert [entry.during_hold for entry in entries] == [False, True, False, False]


def test_loader_keeps_signature_and_replaces_group():
    def handler(request):
        assert request.method == "GET"
        assert request.url.params.get_list("group_id") == ["CONTROL"]
        assert request.url.params["sp"] == "/triggers/manual/run"
        assert request.url.params["sig"] == "fixture+signature"
        return httpx.Response(200, json={
            "isSuccess": True,
            "data": {
                "dataset_group_id": "CONTROL", "total_records": 1,
                "records": [make_conversation([]).model_dump()],
            },
        })

    async def run():
        async with httpx.AsyncClient(trust_env=False, verify=False, transport=httpx.MockTransport(handler)) as client:
            return await load_dataset(
                client, "CONTROL",
                api_url="https://example.invalid/api?sp=%2Ftriggers%2Fmanual%2Frun&sig=fixture%2Bsignature&group_id=DEV",
            )

    assert run_mock_request(run()).records[0].conversation_id == "fixture-1"


def test_loader_error_omits_signed_url():
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(403))
        async with httpx.AsyncClient(trust_env=False, verify=False, transport=transport) as client:
            await load_dataset(client, "DEV", api_url="https://example.invalid/?sig=secret")

    with pytest.raises(DatasetLoadError, match="^Dataset API returned HTTP 403$"):
        run_mock_request(run())



def test_assessment_api_retries_and_persists(tmp_path):
    import json

    from app.api import create_app
    from app.llm.stub import StubProvider
    from app.storage import Storage

    def reply(answer):
        return json.dumps({
            "evidence_indices": [0], "reasoning": "fixture",
            "applicable": True, "answer": 0 if type(answer) is int else answer,
            "evidence": [{"index": 0, "quote": "Hello"}],
            "sub_answers": [
                {"id": f"level_{value}", "answer": value == answer,
                 "evidence": [{"index": 0, "quote": "Hello"}]}
                for value in ([0, 5, 10, 15, 20] if answer == 20 else [0, 4, 7, 10])
            ] if type(answer) is int else [],
        })

    storage = Storage(tmp_path / "cqa.sqlite3")
    storage.import_records([make_conversation([("00:01", "operator", "Hello")])])
    replies = ["invalid JSON"] + [reply(value) for value in (
        "yes", "yes", "yes", 20, "yes", 10, "yes", "no",
    )]
    app = create_app(storage, StubProvider(replies))
    route = next(route for route in app.routes if route.path.endswith("/assess"))
    result = run_mock_request(route.endpoint("fixture-1"))
    assert result["total"] == "100.00"
    assert result["flag"] == "no"
    assert not result["unreliable"]
    assert json.loads(storage.get_record("fixture-1")[1]) == result

    from app.appeals import AppealDecision, AppealRequest

    def endpoint(method, suffix):
        return next(
            route.endpoint for route in app.routes
            if route.path.endswith(suffix) and method in route.methods
        )

    endpoint("POST", "/appeals/{criterion_id}")(
        "fixture-1", "02", AppealRequest(comment="Check applicability"),
    )
    reviewed = endpoint("PUT", "/appeals/{criterion_id}")(
        "fixture-1", "02", AppealDecision(answer="n/a", reason="Enough details"),
    )
    assert reviewed["criteria"][1]["result"]["answer"] == "yes"
    assert reviewed["final_answers"]["02"] == "n/a"
    assert reviewed["total"] == "100.00"

    proposed = endpoint("GET", "/api/checklist")()
    proposed[0] = proposed[0].model_copy(update={"version": 2})
    app_with_one_reply = create_app(storage, StubProvider([reply("no")]))
    apply_route = next(
        route for route in app_with_one_reply.routes
        if route.path == "/api/checklist" and "PUT" in route.methods
    )
    applied = run_mock_request(apply_route.endpoint(proposed))
    assert applied["diff"] == [{
        "conversation_id": "fixture-1", "before": "100.00",
        "after": "88.24", "delta": "-11.76",
    }]
    saved = json.loads(storage.get_record("fixture-1")[1])
    assert saved["appeals"]["02"]["status"] == "resolved"


def test_invalid_assessment_is_marked_unreliable():
    from pathlib import Path

    from pydantic import TypeAdapter

    from app.judge.judge import assess_criterion
    from app.llm.stub import StubProvider
    from app.schemas import Criterion

    config = Path(__file__).resolve().parents[1] / "app/config/checklist.default.json"
    criterion = TypeAdapter(list[Criterion]).validate_json(config.read_bytes())[0]
    result = run_mock_request(assess_criterion(
        StubProvider(["{}", "{}"]), criterion, prepare_transcript(make_conversation([])),
    ))
    assert result.unreliable
    assert result.result is None


def test_quotes_must_match_original_turn():
    from app.judge.judge import validate_evidence
    from app.schemas import EvidenceQuote

    transcript = prepare_transcript(make_conversation([("00:00", "operator", "Hello")]))
    with pytest.raises(ValueError, match="Quote"):
        validate_evidence([EvidenceQuote(index=0, quote="Invented")], None, transcript)
    with pytest.raises(ValueError, match="required"):
        validate_evidence([], None, transcript)
    validate_evidence([], (0, 0), transcript)
