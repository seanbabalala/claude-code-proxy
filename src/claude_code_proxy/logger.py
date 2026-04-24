from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text


def _utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n... (truncated)"


def _content_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_content_to_text(item) for item in value)
    if isinstance(value, dict):
        block_type = str(value.get("type") or "")
        if block_type == "text":
            return str(value.get("text") or "")
        if block_type == "tool_result":
            return _content_to_text(value.get("content"))
        if block_type == "tool_use":
            return f"[tool_use:{value.get('name') or 'tool'}]"
        for key in ("text", "content", "input", "output", "value"):
            if key in value:
                return _content_to_text(value[key])
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _payload_preview(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    previews: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "unknown")
        content = _content_to_text(message.get("content")).strip()
        if not content:
            continue
        previews.append(f"{role}: {content}")
        if len(previews) >= 2:
            break
    return "\n".join(previews)


def _json_block(title: str, payload: dict[str, Any], limit: int) -> Panel:
    pretty = _truncate(json.dumps(payload, ensure_ascii=False, indent=2), limit)
    return Panel(
        Syntax(pretty, "json", theme="ansi_dark", word_wrap=True),
        title=title,
        border_style="bright_black",
        box=box.ASCII2,
        padding=(0, 1),
    )


@dataclass(slots=True)
class StartupDiagnostic:
    level: str
    message: str


@dataclass(slots=True)
class RequestLogContext:
    request_id: str
    endpoint_name: str
    inbound_method: str
    inbound_url: str
    upstream_url: str | None
    requested_model: str | None
    upstream_model: str | None
    response_model: str | None
    stream: bool
    input_tokens: int | None
    max_tokens: int | None
    inbound_payload: dict[str, Any] | None = None
    upstream_payload: dict[str, Any] | None = None


class PrettyLogger:
    def __init__(self, *, enabled: bool = True, payload_max_chars: int = 1400, console: Console | None = None) -> None:
        self.enabled = enabled
        self.payload_max_chars = payload_max_chars
        self.console = console or Console(highlight=False, soft_wrap=True)

    def log_startup(self, settings: Any, diagnostics: list[StartupDiagnostic] | None = None) -> None:
        if not self.enabled:
            return
        table = Table.grid(expand=True)
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column(style="white")
        table.add_row("Service", "Claude Code Proxy")
        table.add_row("Listen", f"{settings.host}:{settings.port}")
        table.add_row("Upstream", settings.upstream_base_url)
        table.add_row("Model", settings.default_upstream_model or "(passthrough)")
        table.add_row("Force model", "yes" if settings.force_upstream_model else "no")
        table.add_row("Default max_tokens", str(settings.default_max_tokens))
        table.add_row("Timeout", f"{settings.upstream_timeout_seconds}s")
        table.add_row("Pretty logs", "enabled" if settings.pretty_logs else "disabled")

        self.console.print(
            Panel(
                table,
                title="Proxy Startup",
                subtitle=_utc_timestamp(),
                border_style="bright_blue",
                box=box.ASCII_DOUBLE_HEAD,
                padding=(0, 1),
            )
        )
        if diagnostics:
            self.log_startup_diagnostics(diagnostics)

    def log_startup_diagnostics(self, diagnostics: list[StartupDiagnostic]) -> None:
        if not self.enabled or not diagnostics:
            return

        table = Table.grid(expand=True)
        table.add_column(style="bold", no_wrap=True)
        table.add_column(style="white")

        has_warning = False
        for diagnostic in diagnostics:
            level = diagnostic.level.strip().lower()
            if level == "warning":
                has_warning = True
                label = Text("Warning", style="bold yellow")
            else:
                label = Text("Note", style="bold cyan")
            table.add_row(label, diagnostic.message)

        self.console.print(
            Panel(
                table,
                title="Claude Code Checks",
                subtitle=_utc_timestamp(),
                border_style="yellow" if has_warning else "bright_blue",
                box=box.ASCII_DOUBLE_HEAD,
                padding=(0, 1),
            )
        )

    def log_request_start(self, context: RequestLogContext) -> None:
        if not self.enabled:
            return
        table = Table.grid(expand=True)
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column(style="white")
        table.add_row("Inbound", f"{context.inbound_method} {context.inbound_url}")
        table.add_row("Endpoint", context.endpoint_name)
        table.add_row("Upstream", context.upstream_url or "(local)")
        if context.requested_model or context.upstream_model:
            table.add_row(
                "Model map",
                f"{context.requested_model or '(none)'} -> {context.upstream_model or '(none)'}",
            )
        if context.response_model:
            table.add_row("Response model", context.response_model)
        table.add_row("Stream", "yes" if context.stream else "no")
        if context.input_tokens is not None:
            table.add_row("Input tokens", str(context.input_tokens))
        if context.max_tokens is not None:
            table.add_row("Max tokens", str(context.max_tokens))
        preview = _payload_preview(context.inbound_payload or {})
        if preview:
            table.add_row("Preview", preview)

        renderables: list[Any] = [table]
        if context.inbound_payload is not None:
            renderables.append(_json_block("Inbound Payload", context.inbound_payload, self.payload_max_chars))
        if context.upstream_payload is not None:
            renderables.append(_json_block("Upstream Payload", context.upstream_payload, self.payload_max_chars))

        self.console.print(
            Panel(
                Group(*renderables),
                title=f"Request {context.request_id}",
                subtitle=_utc_timestamp(),
                border_style="cyan",
                box=box.ASCII_DOUBLE_HEAD,
                padding=(0, 1),
            )
        )

    def log_request_end(
        self,
        context: RequestLogContext,
        *,
        status_code: int,
        latency_ms: int,
        output_tokens: int | None = None,
        stop_reason: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        table = Table.grid(expand=True)
        table.add_column(style="bold green", no_wrap=True)
        table.add_column(style="white")
        table.add_row("Status", str(status_code))
        table.add_row("Latency", f"{latency_ms} ms")
        if context.input_tokens is not None:
            table.add_row("Input tokens", str(context.input_tokens))
        if output_tokens is not None:
            table.add_row("Output tokens", str(output_tokens))
        if stop_reason:
            table.add_row("Stop reason", stop_reason)
        if context.upstream_url:
            table.add_row("Upstream", context.upstream_url)

        self.console.print(
            Panel(
                table,
                title=f"Response {context.request_id}",
                subtitle=_utc_timestamp(),
                border_style="green",
                box=box.ASCII_DOUBLE_HEAD,
                padding=(0, 1),
            )
        )

    def log_request_error(
        self,
        context: RequestLogContext | None,
        *,
        status_code: int,
        message: str,
        latency_ms: int | None = None,
    ) -> None:
        if not self.enabled:
            return
        table = Table.grid(expand=True)
        table.add_column(style="bold red", no_wrap=True)
        table.add_column(style="white")
        if context is not None:
            table.add_row("Request", context.request_id)
            table.add_row("Endpoint", context.endpoint_name)
            table.add_row("Inbound", f"{context.inbound_method} {context.inbound_url}")
            if context.upstream_url:
                table.add_row("Upstream", context.upstream_url)
        table.add_row("Status", str(status_code))
        if latency_ms is not None:
            table.add_row("Latency", f"{latency_ms} ms")
        table.add_row("Error", message)

        self.console.print(
            Panel(
                table,
                title="Proxy Error",
                subtitle=_utc_timestamp(),
                border_style="red",
                box=box.ASCII_DOUBLE_HEAD,
                padding=(0, 1),
            )
        )
