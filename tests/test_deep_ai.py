"""Regression coverage of the retained legacy deep-AI pipeline (not the live entry)."""
import asyncio
import json

import pytest

from src.core.config import settings
from src.qa import deep_ai, rag


HITS = [{"id": "h1", "text": "先登录，再订阅行情。失败时检查权限。", "title": "接入指南",
         "source_path": "guide.pdf", "source_name": "guide.pdf", "score": 0.8}]


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.closed = 0

    async def chat_stream(self, messages, **kwargs):
        self.calls.append(messages)
        try:
            value = next(self.responses)
            if isinstance(value, Exception):
                raise value
            if isinstance(value, dict):
                value = json.dumps(value, ensure_ascii=False)
            yield value, "test-model"
        finally:
            self.closed += 1


def setup(monkeypatch, responses, hits=None):
    client = Client(responses)
    searches = []
    monkeypatch.setattr(settings, "_config", {"qa": {}})
    monkeypatch.setattr(deep_ai.llm_client, "get_client", lambda: client)
    monkeypatch.setattr(deep_ai, "_background", lambda kb: None)

    def search(question, k, kb, trace, **kwargs):
        searches.append({"question": question, "kb": kb, **kwargs})
        return HITS if hits is None else hits, "test-embed"

    monkeypatch.setattr(deep_ai.retrieval, "search", search)
    return client, searches


async def collect(history=None, question="它的接入步骤是什么"):
    return [e async for e in deep_ai.ask_stream(question, history, "kb_one", top_k=None, session_id=None, turn_id=None)]


def done(events):
    assert events[-1]["type"] == "done"
    return events[-1]["data"]


@pytest.mark.asyncio
async def test_verified_answer_uses_history_and_streams_only_after_check(monkeypatch):
    client, searches = setup(monkeypatch, [
        {"question": "SDK 接入步骤", "aspects": ["登录", "订阅"], "answer_type": "procedure"},
        {"missing_aspects": [], "conflicts": [], "followup_query": ""},
        "先登录，再订阅行情。[1]", {"supported": True},
    ])
    events = await collect([{"role": "user", "content": "正在使用 SDK"}])
    result = done(events)
    assert result["verification"] == "checked"
    assert result["answer"] == "先登录，再订阅行情。[1]"
    assert result["mode"] == "deep_ai"
    assert len(searches) == 1 and searches[0]["kb"] == "kb_one"
    assert "SDK 接入步骤" in searches[0]["queries"]
    assert "正在使用 SDK" in client.calls[0][1]["content"]
    assert client.closed == 4
    assert not any(e["type"] == "token" for e in events)
    assert events[-3]["data"]["stage"] == "citation_verification"
    assert result["sources"][0]["content"] == HITS[0]["text"]


@pytest.mark.asyncio
async def test_gap_causes_exactly_one_followup_and_original_question_ranking(monkeypatch):
    client, searches = setup(monkeypatch, [
        {"question": "SDK 接入步骤"},
        {"missing_aspects": ["权限"], "followup_query": "SDK 订阅权限"},
        "先登录，失败时检查权限。[1]", {"supported": True},
    ])
    assert done(await collect())["verification"] == "checked"
    assert len(searches) == 2 and len(client.calls) == 4
    assert searches[1]["question"] == "SDK 订阅权限"
    assert searches[1]["ranking_question"] == "SDK 接入步骤"
    assert searches[1]["prior_hits"] == HITS


@pytest.mark.parametrize("checker,expected", [
    ({"supported": True}, "extractive"),
    ({"supported": False, "revised_answer": "必须先登录。[1]"}, "revised"),
    ({"supported": False, "revised_answer": "未知来源。[99]"}, "extractive"),
    (TimeoutError("checker timeout"), "extractive"),
])
@pytest.mark.asyncio
async def test_invalid_citations_and_failed_verification_never_publish_draft(monkeypatch, checker, expected):
    setup(monkeypatch, [{}, {}, "未经支持的结论。[99]", checker])
    result = done(await collect())
    assert result["verification"] == expected
    assert "未经支持的结论" not in result["answer"]
    assert "[99]" not in result["answer"]
    assert "[1]" in result["answer"]


@pytest.mark.asyncio
async def test_no_evidence_never_calls_generation(monkeypatch):
    client, searches = setup(monkeypatch, [{}], hits=[])
    result = done(await collect())
    assert result["verification"] == "insufficient"
    assert result["sources"] == []
    assert "未找到" in result["answer"]
    assert len(client.calls) == 1 and len(searches) == 1


@pytest.mark.asyncio
async def test_review_timeout_retains_answer_but_never_claims_verified(monkeypatch):
    setup(monkeypatch, [{}, {}, "先登录，再订阅行情。[1]", TimeoutError("review")])
    result = done(await collect())
    assert result["verification"] == "unverified"
    assert "先登录，再订阅行情。[1]" in result["answer"]
    assert "尚未完成自动复核" in result["answer"]


@pytest.mark.asyncio
async def test_rejected_draft_is_not_exposed_as_merely_unverified(monkeypatch):
    setup(monkeypatch, [{}, {}, "没有依据的配置。[1]", {"supported": False, "revised_answer": ""}])
    result = done(await collect())
    assert result["verification"] == "extractive"
    assert "没有依据的配置" not in result["answer"]


@pytest.mark.asyncio
async def test_adt_possible_causes_retrieves_despite_planner_clarification(monkeypatch):
    question = "安装adt失败，可能是什么原因"
    hits = [{**HITS[0], "text": "安装adt失败，缺少jdk；配置yum源后安装JDK。", "title": "问题事件记录"}]
    answer = "已有案例因缺少 JDK 导致 ADT 安装失败，可先检查依赖。[1] 具体原因还需结合安装报错确认。"
    client, searches = setup(monkeypatch, [
        {"question": "安装 ADT 时失败，可能有哪些原因，应该如何排查？",
         "aspects": ["确认产品版本", "分析错误日志"], "answer_type": "troubleshooting",
         "needs_clarification": True, "clarification_question": "ADT 指什么产品？请提供版本和完整错误信息。"},
        {}, answer, {"supported": True, "answerable_points": ["缺少 JDK"], "needs_clarification": False},
    ], hits=hits)
    result = done(await collect(question=question))
    assert result["verification"] == "checked" and result["answer"] == answer
    assert len(searches) == 1 and searches[0]["question"] == question
    assert len(client.calls) == 4
    assert result["sources"][0]["content"] == hits[0]["text"]
    coverage = json.loads(client.calls[1][1]["content"])
    assert coverage["original_question"] == question
    assert coverage["evidence"][0]["text"] == hits[0]["text"]


@pytest.mark.asyncio
async def test_irrelevant_clarification_is_revised_into_partial_answer(monkeypatch):
    answer = "可先检查权限。[1] 若要确定本次失败原因，请提供错误码。"
    _, searches = setup(monkeypatch, [
        {"needs_clarification": True, "clarification_question": "提供所有日志。"}, {},
        "请先提供所有日志。", {"supported": False, "revised_answer": answer,
                          "answerable_points": ["检查权限"], "needs_clarification": True},
    ])
    result = done(await collect(question="安装失败，可能是什么原因"))
    assert len(searches) == 1
    assert result["answer"] == answer and result["verification"] == "revised"


@pytest.mark.asyncio
async def test_genuine_ambiguity_is_clarified_only_after_retrieval_and_review(monkeypatch):
    question = "你使用的是哪一种 ADT 产品？"
    client, searches = setup(monkeypatch, [
        {"needs_clarification": True, "clarification_question": "请提供版本和所有日志。"}, {}, question,
        {"supported": True, "answerable_points": [], "needs_clarification": True,
         "clarification_question": question},
    ], hits=[{**HITS[0], "text": "ADT-A 与 ADT-B 是不同产品，其安装方式不同。"}])
    result = done(await collect(question="ADT 要执行哪个安装命令？"))
    assert len(searches) == 1 and len(client.calls) == 4
    assert result["answer"] == question and result["verification"] == "clarification"
    assert len(result["sources"]) == 1


@pytest.mark.parametrize("review", [
    {"needs_clarification": "true", "answerable_points": [], "clarification_question": "什么版本？"},
    {"needs_clarification": True, "clarification_question": "什么版本？"},
    {"needs_clarification": True, "answerable_points": [], "clarification_question": "  "},
    {"needs_clarification": True, "answerable_points": [], "clarification_question": "用不存在的资料[99]？"},
])
@pytest.mark.asyncio
async def test_malformed_clarification_cannot_bypass_evidence_check(monkeypatch, review):
    setup(monkeypatch, [{}, {}, "未经支持的结论。[99]", review])
    result = done(await collect())
    assert result["verification"] == "extractive"
    assert "未经支持的结论" not in result["answer"]


@pytest.mark.asyncio
async def test_no_hits_can_request_missing_details_but_only_after_search(monkeypatch):
    client, searches = setup(monkeypatch, [
        {"needs_clarification": True, "clarification_question": "具体是哪种 ADT 产品？"},
    ], hits=[])
    result = done(await collect(question="安装adt失败，可能是什么原因"))
    assert len(searches) == 1 and len(client.calls) == 1
    assert result["verification"] == "clarification"
    assert "未找到足以支持" in result["answer"]
    assert "具体是哪种 ADT 产品" in result["answer"]


@pytest.mark.asyncio
async def test_followup_can_resolve_planner_ambiguity(monkeypatch):
    initial = [{**HITS[0], "text": "ADT 是部署工具。"}]
    updated = [{**HITS[0], "text": "ADT 安装失败的已知原因是缺少 JDK。"}]
    client, _ = setup(monkeypatch, [
        {"needs_clarification": True, "clarification_question": "哪种产品？"},
        {"missing_aspects": ["安装失败案例"], "followup_query": "ADT 安装失败 JDK"},
        "一种可能原因是缺少 JDK。[1]", {"supported": True},
    ])
    searches = []
    def search(*args, **kwargs):
        searches.append(kwargs)
        return (initial if len(searches) == 1 else updated), "test"
    monkeypatch.setattr(deep_ai.retrieval, "search", search)
    result = done(await collect(question="安装adt失败，可能是什么原因"))
    assert result["verification"] == "checked" and len(searches) == 2
    assert updated[0]["text"] in client.calls[2][1]["content"]
    assert json.loads(client.calls[3][1]["content"])["evidence"][0]["text"] == updated[0]["text"]


@pytest.mark.asyncio
async def test_empty_followup_preserves_answerable_evidence(monkeypatch):
    setup(monkeypatch, [{}, {"missing_aspects": ["权限"], "followup_query": "SDK 权限"},
                        "失败时检查权限。[1]", {"supported": True}])
    calls = []
    def search(*args, **kwargs):
        calls.append(kwargs)
        return (HITS if len(calls) == 1 else []), "test"
    monkeypatch.setattr(deep_ai.retrieval, "search", search)
    result = done(await collect())
    assert result["verification"] == "checked" and len(result["sources"]) == 1
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_conflicting_versions_can_be_answered_conditionally(monkeypatch):
    hits = [{**HITS[0], "version": "1", "text": "ADT v1 依赖 Java 8。"},
            {**HITS[0], "id": "h2", "version": "2", "text": "ADT v2 依赖 Java 17。"}]
    answer = "v1 要求 Java 8，[1] v2 要求 Java 17。[2] 请确认你的版本以选择对应环境。"
    setup(monkeypatch, [{"needs_clarification": True, "clarification_question": "什么版本？"},
                        {"conflicts": ["不同版本的 Java 要求不同"]}, answer,
                        {"supported": True, "answerable_points": ["各版本要求"]}], hits=hits)
    result = done(await collect(question="ADT 的运行环境有什么要求"))
    assert result["answer"] == answer and result["verification"] == "checked"


@pytest.mark.asyncio
async def test_history_resolution_and_evidence_are_both_available_to_reviewer(monkeypatch):
    history = [{"role": "user", "content": "我用的是 ADT v2"},
               {"role": "assistant", "content": "之前的回答未必正确"}]
    client, searches = setup(monkeypatch, [
        {"question": "ADT v2 接入步骤", "needs_clarification": True, "clarification_question": "什么版本？"},
        {}, "先登录，再订阅。[1]", {"supported": True},
    ])
    result = done(await collect(history))
    assert result["verification"] == "checked"
    assert "ADT v2 接入步骤" in searches[0]["queries"]
    review = json.loads(client.calls[3][1]["content"])
    assert review["resolved_question"] == "ADT v2 接入步骤"
    assert review["history_for_reference_only"] == history
    assert review["evidence"][0]["text"] == HITS[0]["text"]


@pytest.mark.asyncio
async def test_planner_and_coverage_failures_still_allow_verified_answer(monkeypatch):
    _, searches = setup(monkeypatch, ["not json", TimeoutError(), "先登录。[1]", {"supported": True}])
    result = done(await collect())
    assert result["verification"] == "checked"
    assert searches[0]["question"] == "它的接入步骤是什么"


def test_query_rewrite_preserves_literal_identifiers():
    plan = deep_ai.parse_plan({"question": "QueryData v2.0 的限制"}, "QueryMDTick v1.2 ERR-001")
    assert plan.question == "QueryMDTick v1.2 ERR-001"


def test_evidence_budget_keeps_whole_chunks_and_citation_range():
    hits = [{"text": "a" * 10}, {"text": "b" * 20}, {"text": "c" * 5}]
    assert deep_ai.budget_evidence(hits, 15) == [hits[0], hits[2]]
    assert deep_ai.citations_valid("数组 `a[99]` 的用法。[1]", 1)
    assert not deep_ai.citations_valid("```py\na[1]\n```", 1)
    assert not deep_ai.citations_valid("结论。[0]", 1)


@pytest.mark.asyncio
async def test_disconnect_cancels_model_request_without_publishing(monkeypatch):
    closed = asyncio.Event()

    async def blocked(*args):
        emit = args[7]
        emit({"type": "stage", "data": {"stage": "waiting"}})
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(deep_ai, "_answer", blocked)
    stream = deep_ai.ask_stream("q", None, "default", top_k=None, session_id=None, turn_id=None)
    assert (await stream.__anext__())["type"] == "stage"
    await stream.aclose()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_total_timeout_returns_evidence_and_stops_work(monkeypatch):
    original = deep_ai._number
    monkeypatch.setattr(deep_ai, "_number", lambda cfg, key, *a:
                        0.01 if key == "total_timeout_seconds" else original(cfg, key, *a))

    async def blocked(question, history, kb, k, meta, trace, state, emit):
        state["hits"] = HITS
        await asyncio.Event().wait()

    monkeypatch.setattr(deep_ai, "_answer", blocked)
    result = done(await collect())
    assert result["verification"] == "timeout"
    assert "先登录" in result["answer"]
    assert result["used_chunks"] == 1
