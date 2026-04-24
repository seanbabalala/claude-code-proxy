from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from typing import Any

from .errors import ProxyError

ANTHROPIC_VERSION = "2023-06-01"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _content_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.append(_content_to_text(item))
        return "".join(part for part in parts if part)
    if isinstance(value, dict):
        block_type = str(value.get("type") or "")
        if block_type == "text":
            return str(value.get("text") or "")
        if block_type == "tool_result":
            return _content_to_text(value.get("content"))
        if block_type == "tool_use":
            name = str(value.get("name") or "tool")
            return f"[tool_use:{name}]"
        for key in ("text", "content", "value", "input", "output"):
            if key in value:
                return _content_to_text(value[key])
        return _json_dumps(value)
    return str(value)


def normalize_system_prompt(system: Any) -> str | None:
    text = _content_to_text(system).strip()
    return text or None


def anthropic_message_to_openai_messages(message: dict[str, Any]) -> list[dict[str, Any]]:
    role = str(message.get("role") or "user")
    content = message.get("content", "")

    if isinstance(content, str):
        return [{"role": role, "content": content}]

    if not isinstance(content, list):
        return [{"role": role, "content": _content_to_text(content)}]

    if role == "assistant":
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                text_parts.append(_content_to_text(block))
                continue
            block_type = str(block.get("type") or "")
            if block_type == "text":
                text_parts.append(str(block.get("text") or ""))
                continue
            if block_type == "tool_use":
                tool_calls.append(
                    {
                        "id": str(block.get("id") or f"toolu_{uuid.uuid4().hex}"),
                        "type": "function",
                        "function": {
                            "name": str(block.get("name") or "tool"),
                            "arguments": _json_dumps(block.get("input") or {}),
                        },
                    }
                )
                continue
            text_parts.append(_content_to_text(block))

        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(text_parts),
        }
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        return [assistant_message]

    openai_messages: list[dict[str, Any]] = []
    pending_text: list[str] = []

    def flush_text() -> None:
        if pending_text:
            openai_messages.append({"role": "user", "content": "".join(pending_text)})
            pending_text.clear()

    for block in content:
        if not isinstance(block, dict):
            pending_text.append(_content_to_text(block))
            continue
        block_type = str(block.get("type") or "")
        if block_type == "text":
            pending_text.append(str(block.get("text") or ""))
            continue
        if block_type == "tool_result":
            flush_text()
            tool_content = _content_to_text(block.get("content"))
            if block.get("is_error"):
                tool_content = f"ERROR\n{tool_content}".strip()
            openai_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(block.get("tool_use_id") or f"toolu_{uuid.uuid4().hex}"),
                    "content": tool_content,
                }
            )
            continue
        pending_text.append(_content_to_text(block))

    flush_text()
    return openai_messages or [{"role": "user", "content": ""}]


def build_openai_messages(system: Any, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    openai_messages: list[dict[str, Any]] = []
    normalized_system = normalize_system_prompt(system)
    if normalized_system:
        openai_messages.append({"role": "system", "content": normalized_system})
    for message in messages:
        openai_messages.extend(anthropic_message_to_openai_messages(message))
    return openai_messages


def build_openai_tools(tools: Any) -> list[dict[str, Any]] | None:
    if not isinstance(tools, list):
        return None
    converted: list[dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(item.get("description") or ""),
                    "parameters": item.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    return converted or None


def build_openai_tool_choice(tool_choice: Any) -> Any:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        return tool_choice
    if not isinstance(tool_choice, dict):
        return None
    choice_type = str(tool_choice.get("type") or "")
    if choice_type == "auto":
        return "auto"
    if choice_type == "none":
        return "none"
    if choice_type == "any":
        return "required"
    if choice_type == "tool":
        return {
            "type": "function",
            "function": {"name": str(tool_choice.get("name") or "")},
        }
    return None


def build_openai_request(payload: dict[str, Any], upstream_model: str, *, default_max_tokens: int = 32000) -> dict[str, Any]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ProxyError("messages must be a non-empty array.")

    openai_payload: dict[str, Any] = {
        "model": upstream_model,
        "messages": build_openai_messages(payload.get("system"), messages),
        "stream": bool(payload.get("stream", False)),
        "max_completion_tokens": int(payload.get("max_tokens") or default_max_tokens),
    }

    if payload.get("temperature") is not None:
        openai_payload["temperature"] = payload["temperature"]
    if payload.get("stop_sequences"):
        openai_payload["stop"] = payload["stop_sequences"]

    tools = build_openai_tools(payload.get("tools"))
    if tools:
        openai_payload["tools"] = tools

    tool_choice = build_openai_tool_choice(payload.get("tool_choice"))
    if tool_choice is not None:
        openai_payload["tool_choice"] = tool_choice

    return openai_payload


def estimate_tokens(payload: dict[str, Any]) -> int:
    serialized = _json_dumps(payload)
    return max(1, math.ceil(len(serialized.encode("utf-8")) / 4))


def _openai_content_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "".join(parts)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("content") or "")
    if value is None:
        return ""
    return str(value)


def _parse_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not isinstance(raw_arguments, str):
        return {}
    stripped = raw_arguments.strip()
    if not stripped:
        return {}
    try:
        loaded = json.loads(stripped)
    except json.JSONDecodeError:
        return {"raw": stripped}
    return loaded if isinstance(loaded, dict) else {"value": loaded}


def map_finish_reason_to_stop_reason(finish_reason: str | None, has_tool_calls: bool) -> str:
    normalized = (finish_reason or "").strip().lower()
    if has_tool_calls or normalized in {"tool_calls", "tool_call"}:
        return "tool_use"
    if normalized in {"length", "max_tokens"}:
        return "max_tokens"
    if normalized == "stop_sequence":
        return "stop_sequence"
    return "end_turn"


def build_anthropic_message_response(
    data: dict[str, Any],
    *,
    requested_model: str,
    input_tokens: int,
) -> dict[str, Any]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProxyError("Upstream response did not contain choices.", status_code=502, error_type="api_error")

    first_choice = choices[0] if isinstance(choices[0], dict) else {}
    message = first_choice.get("message") if isinstance(first_choice.get("message"), dict) else {}
    text = _openai_content_to_text(message.get("content"))
    tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []

    content_blocks: list[dict[str, Any]] = []
    if text:
        content_blocks.append({"type": "text", "text": text})
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        content_blocks.append(
            {
                "type": "tool_use",
                "id": str(tool_call.get("id") or f"toolu_{uuid.uuid4().hex}"),
                "name": str(function.get("name") or "tool"),
                "input": _parse_tool_arguments(function.get("arguments")),
            }
        )

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    output_seed = text + "".join(
        str(tool_call.get("function", {}).get("arguments") or "")
        for tool_call in tool_calls
        if isinstance(tool_call, dict)
    )
    output_tokens = int(usage.get("completion_tokens") or estimate_tokens({"output": output_seed}))
    response_id = str(data.get("id") or f"msg_{uuid.uuid4().hex}")
    if not response_id.startswith("msg_"):
        response_id = f"msg_{response_id}"

    return {
        "id": response_id,
        "type": "message",
        "role": "assistant",
        "model": requested_model,
        "content": content_blocks,
        "stop_reason": map_finish_reason_to_stop_reason(first_choice.get("finish_reason"), bool(tool_calls)),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or input_tokens),
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


@dataclass(slots=True)
class ToolStreamState:
    openai_index: int
    anthropic_index: int
    tool_id: str
    tool_name: str
    input_parts: list[str] = field(default_factory=list)
    open: bool = True


@dataclass(slots=True)
class StreamTranslator:
    requested_model: str
    input_tokens: int
    message_id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex}")
    next_block_index: int = 0
    output_fragments: list[str] = field(default_factory=list)
    finish_reason: str | None = None
    text_block_index: int | None = None
    text_block_open: bool = False
    tool_blocks: dict[int, ToolStreamState] = field(default_factory=dict)

    def start_events(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "message_start",
                "message": {
                    "id": self.message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": self.requested_model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": self.input_tokens,
                        "output_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            }
        ]

    def consume_chunk(self, chunk: dict[str, Any]) -> list[dict[str, Any]]:
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return []
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        events: list[dict[str, Any]] = []

        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason:
            self.finish_reason = finish_reason

        text_delta = _openai_content_to_text(delta.get("content"))
        if text_delta:
            events.extend(self._ensure_text_block())
            events.append(
                {
                    "type": "content_block_delta",
                    "index": self.text_block_index,
                    "delta": {"type": "text_delta", "text": text_delta},
                }
            )
            self.output_fragments.append(text_delta)

        raw_tool_calls = delta.get("tool_calls")
        if isinstance(raw_tool_calls, list) and raw_tool_calls:
            if self.text_block_open:
                events.append({"type": "content_block_stop", "index": self.text_block_index})
                self.text_block_open = False
            for fallback_index, item in enumerate(raw_tool_calls):
                if not isinstance(item, dict):
                    continue
                openai_index = int(item.get("index", fallback_index))
                tool_state = self.tool_blocks.get(openai_index)
                function = item.get("function") if isinstance(item.get("function"), dict) else {}
                tool_id = str(item.get("id") or (tool_state.tool_id if tool_state else f"toolu_{uuid.uuid4().hex}"))
                tool_name = str(function.get("name") or (tool_state.tool_name if tool_state else "tool"))
                if tool_state is None:
                    tool_state = ToolStreamState(
                        openai_index=openai_index,
                        anthropic_index=self.next_block_index,
                        tool_id=tool_id,
                        tool_name=tool_name,
                    )
                    self.tool_blocks[openai_index] = tool_state
                    self.next_block_index += 1
                    events.append(
                        {
                            "type": "content_block_start",
                            "index": tool_state.anthropic_index,
                            "content_block": {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": tool_name,
                                "input": {},
                            },
                        }
                    )
                arguments_delta = str(function.get("arguments") or "")
                if arguments_delta:
                    tool_state.input_parts.append(arguments_delta)
                    self.output_fragments.append(arguments_delta)
                    events.append(
                        {
                            "type": "content_block_delta",
                            "index": tool_state.anthropic_index,
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": arguments_delta,
                            },
                        }
                    )

        return events

    def finish_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if self.text_block_open:
            events.append({"type": "content_block_stop", "index": self.text_block_index})
            self.text_block_open = False
        for tool_state in sorted(self.tool_blocks.values(), key=lambda item: item.anthropic_index):
            if tool_state.open:
                events.append({"type": "content_block_stop", "index": tool_state.anthropic_index})
                tool_state.open = False
        events.append(
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": map_finish_reason_to_stop_reason(self.finish_reason, bool(self.tool_blocks)),
                    "stop_sequence": None,
                },
                "usage": {"output_tokens": estimate_tokens({"output": "".join(self.output_fragments)})},
            }
        )
        events.append({"type": "message_stop"})
        return events

    def _ensure_text_block(self) -> list[dict[str, Any]]:
        if self.text_block_open:
            return []
        events: list[dict[str, Any]] = []
        for tool_state in sorted(self.tool_blocks.values(), key=lambda item: item.anthropic_index):
            if tool_state.open:
                tool_state.open = False
                events.append({"type": "content_block_stop", "index": tool_state.anthropic_index})
        return events + self._start_text_block()

    def _start_text_block(self) -> list[dict[str, Any]]:
        self.text_block_index = self.next_block_index
        self.next_block_index += 1
        self.text_block_open = True
        return [
            {
                "type": "content_block_start",
                "index": self.text_block_index,
                "content_block": {"type": "text", "text": ""},
            }
        ]
