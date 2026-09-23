import copy
import json
from types import SimpleNamespace

import pytest

from src.core.llm_diagnostics import CallDiagnostics, tool_schema_diagnostics


@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic"])
def test_wire_schema_shape_without_content(protocol):
    schema = {"type": "object", "properties": {
        "query": {"type": "string", "description": "PRIVATE DESCRIPTION"},
        "scope_ref": {"anyOf": [{"type": "string"}, {"type": "null"}],
                      "default": "PRIVATE DEFAULT", "enum": ["PRIVATE ENUM"]},
    }, "required": ["query"]}
    definition = {"name": "search_document", "parameters": schema, "strict": False}
    if protocol == "chat":
        tool = {"type": "function", "function": definition}
    elif protocol == "responses":
        tool = {"type": "function", **definition}
    else:
        tool = {"name": "search_document", "input_schema": schema}
    original = copy.deepcopy(tool)
    result = tool_schema_diagnostics([tool])
    summary = result["tools"][0]
    assert summary["required"] == ["query"]
    assert summary["optional"] == ["scope_ref"]
    assert summary["properties"]["scope_ref"] == {"types": ["null", "string"], "nullable": True}
    assert summary["strict_present"] == (protocol != "anthropic")
    assert summary["strict"] == (False if protocol != "anthropic" else None)
    assert "PRIVATE" not in json.dumps(result)
    assert tool == original


def test_digest_stable_and_sensitive_to_actual_wire_definition():
    a = [{"name": "read", "parameters": {"type": "object", "properties": {}}, "description": "a"}]
    b = [{"description": "a", "parameters": {"properties": {}, "type": "object"}, "name": "read"}]
    assert tool_schema_diagnostics(a)["sha256"] == tool_schema_diagnostics(b)["sha256"]
    b[0]["description"] = "b"
    assert tool_schema_diagnostics(a)["sha256"] != tool_schema_diagnostics(b)["sha256"]


def test_summary_is_bounded_and_supports_type_array():
    props = {f"p{i:03}": {"type": ["string", "null"]} for i in range(100)}
    result = tool_schema_diagnostics([{"name": "read", "parameters": {
        "properties": props, "required": list(props)}}] * 40)
    assert result["truncated"]
    assert len(result["tools"]) == 32
    assert result["tools"][0]["truncated"]
    assert len(result["tools"][0]["properties"]) == 64
    assert result["tools"][0]["properties"]["p000"]["nullable"]


def test_request_logs_safe_schema_summary(caplog, monkeypatch):
    monkeypatch.setattr("src.core.llm_diagnostics.getproxies", lambda: {})
    diag = CallDiagnostics(SimpleNamespace(_api_key="test-api-secret", name="test", chat_model="model"),
                           "responses", "test", {})
    with caplog.at_level("INFO", logger="src.core.llm_diagnostics"):
        diag.request("https://example.com/v1/responses", {
            "input": [{"role": "user", "content": "PRIVATE USER CONTENT"}],
            "tools": [{"type": "function", "name": "read", "strict": True,
                       "description": "PRIVATE TOOL DESCRIPTION", "parameters": {
                           "type": "object", "properties": {"ref": {"type": "string"}},
                           "required": ["ref"]}}]})
    assert diag.data["tool_schema"]["tools"][0]["strict"] is True
    assert "tool_schema" in caplog.text
    assert "PRIVATE" not in caplog.text
    assert "test-api-secret" not in caplog.text
