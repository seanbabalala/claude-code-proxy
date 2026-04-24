from __future__ import annotations

from claude_code_proxy.translator import (
    build_anthropic_message_response,
    build_openai_request,
    estimate_tokens,
)


def test_build_openai_request_with_tools_and_tool_results() -> None:
    payload = {
        "model": "claude-sonnet-4-5",
        "system": "You are helpful.",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "read_file",
                        "input": {"path": "/tmp/a.txt"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "hello",
                    },
                    {"type": "text", "text": "continue"},
                ],
            },
        ],
        "tools": [
            {
                "name": "read_file",
                "description": "Read a file",
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            }
        ],
        "tool_choice": {"type": "tool", "name": "read_file"},
        "max_tokens": 2048,
    }

    request = build_openai_request(payload, "claude-opus-4-6-v1")
    assert request["model"] == "claude-opus-4-6-v1"
    assert request["messages"][0] == {"role": "system", "content": "You are helpful."}
    assert request["messages"][1]["tool_calls"][0]["function"]["name"] == "read_file"
    assert request["messages"][2]["role"] == "tool"
    assert request["messages"][3] == {"role": "user", "content": "continue"}
    assert request["tool_choice"]["function"]["name"] == "read_file"


def test_build_anthropic_message_response_with_tool_use() -> None:
    upstream = {
        "id": "chatcmpl_123",
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": "{\"path\":\"/tmp/a.txt\"}",
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 12},
    }

    response = build_anthropic_message_response(upstream, requested_model="claude-sonnet-4-5", input_tokens=123)
    assert response["model"] == "claude-sonnet-4-5"
    assert response["stop_reason"] == "tool_use"
    assert response["content"][0]["type"] == "tool_use"
    assert response["content"][0]["input"]["path"] == "/tmp/a.txt"
    assert response["usage"]["input_tokens"] == 100


def test_estimate_tokens_returns_positive_value() -> None:
    payload = {"messages": [{"role": "user", "content": "hello"}], "model": "claude-sonnet-4-5"}
    assert estimate_tokens(payload) > 0


def test_build_openai_request_uses_default_max_tokens() -> None:
    payload = {
        "model": "claude-sonnet-4-5",
        "messages": [{"role": "user", "content": "hello"}],
    }
    request = build_openai_request(payload, "claude-opus-4-6-v1", default_max_tokens=32000)
    assert request["max_completion_tokens"] == 32000
