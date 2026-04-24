from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import Settings
from .errors import ProxyError
from .logger import PrettyLogger, RequestLogContext, StartupDiagnostic
from .translator import (
    ANTHROPIC_VERSION,
    StreamTranslator,
    build_anthropic_message_response,
    build_openai_request,
    estimate_tokens,
)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _extract_api_key(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    for header_name in ("x-api-key", "api-key"):
        value = request.headers.get(header_name, "").strip()
        if value:
            return value
    return None


def _anthropic_headers() -> dict[str, str]:
    return {
        "anthropic-version": ANTHROPIC_VERSION,
    }


def _proxy_error_response(error: ProxyError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content=error.to_payload(), headers=_anthropic_headers())


def _sse_frame(event: dict[str, Any]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def _expected_local_base_url(settings: Settings) -> str:
    host = settings.host.strip()
    if host in {"127.0.0.1", "localhost", "::1", "0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{settings.port}"


def _looks_like_local_proxy_base_url(url: str, settings: Settings) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "http":
        return False
    if parsed.path not in {"", "/"}:
        return False
    if (parsed.port or 80) != settings.port:
        return False
    host = (parsed.hostname or "").strip().lower()
    return host in {"127.0.0.1", "localhost", "::1", settings.host.strip().lower()}


def collect_claude_code_startup_diagnostics(
    settings: Settings,
    claude_settings_path: Path | None = None,
) -> list[StartupDiagnostic]:
    settings_path = claude_settings_path or (Path.home() / ".claude" / "settings.json")
    if not settings_path.exists():
        return []

    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [
            StartupDiagnostic(
                level="warning",
                message=f"Could not parse {settings_path}: {exc}.",
            )
        ]

    if not isinstance(raw, dict):
        return [
            StartupDiagnostic(
                level="warning",
                message=f"{settings_path} must contain a JSON object.",
            )
        ]

    diagnostics: list[StartupDiagnostic] = []
    env = raw.get("env")
    if isinstance(env, dict):
        configured_base_url = str(env.get("ANTHROPIC_BASE_URL") or "").strip()
        expected_base_url = _expected_local_base_url(settings)
        if configured_base_url and not _looks_like_local_proxy_base_url(configured_base_url, settings):
            diagnostics.append(
                StartupDiagnostic(
                    level="warning",
                    message=(
                        f"{settings_path} sets ANTHROPIC_BASE_URL={configured_base_url}. "
                        f"Claude Code will bypass this proxy unless you change it to {expected_base_url}."
                    ),
                )
            )

        expected_token = settings.gateway_api_key
        configured_token = str(env.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
        if expected_token and not configured_token:
            diagnostics.append(
                StartupDiagnostic(
                    level="warning",
                    message=(
                        f"{settings_path} is missing ANTHROPIC_AUTH_TOKEN while GATEWAY_API_KEY is set. "
                        "Interactive Claude Code requests will fail with 401 until they match."
                    ),
                )
            )
        elif expected_token and configured_token != expected_token:
            diagnostics.append(
                StartupDiagnostic(
                    level="warning",
                    message=(
                        f"{settings_path} uses a different ANTHROPIC_AUTH_TOKEN than GATEWAY_API_KEY. "
                        "Interactive Claude Code requests will fail with 401 until both values match."
                    ),
                )
            )

    local_model = str(raw.get("model") or "").strip().lower()
    if settings.force_upstream_model and settings.default_upstream_model and local_model == "sonnet":
        upstream_model = settings.default_upstream_model.lower()
        if "opus" in upstream_model:
            diagnostics.append(
                StartupDiagnostic(
                    level="note",
                    message=(
                        f"{settings_path} still uses model=sonnet. Traffic is pinned upstream to "
                        f"{settings.default_upstream_model}, but Claude Code will label the session as Sonnet "
                        "until you switch the local alias to opus."
                    ),
                )
            )

    return diagnostics


def create_app(settings: Settings | None = None, upstream_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logger = PrettyLogger(enabled=settings.pretty_logs, payload_max_chars=settings.log_payload_max_chars)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.log_startup(settings, diagnostics=collect_claude_code_startup_diagnostics(settings))
        yield

    app = FastAPI(title="Claude Code Proxy", lifespan=lifespan)
    app.state.settings = settings
    app.state.upstream_transport = upstream_transport
    app.state.pretty_logger = logger

    @app.exception_handler(ProxyError)
    async def proxy_error_handler(request: Request, exc: ProxyError) -> JSONResponse:
        logger: PrettyLogger = request.app.state.pretty_logger
        if not getattr(request.state, "error_logged", False):
            started_at = getattr(request.state, "started_at", None)
            latency_ms = int((time.perf_counter() - started_at) * 1000) if isinstance(started_at, float) else None
            logger.log_request_error(
                getattr(request.state, "log_context", None),
                status_code=exc.status_code,
                message=exc.message,
                latency_ms=latency_ms,
            )
            request.state.error_logged = True
        return _proxy_error_response(exc)

    @app.get("/")
    async def root() -> dict[str, object]:
        return {"ok": True, "service": "claude-code-proxy"}

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True}

    @app.get("/v1/models")
    async def list_models() -> JSONResponse:
        models = settings.advertised_models()
        payload = {
            "data": [
                {
                    "type": "model",
                    "id": model,
                    "display_name": model,
                    "created_at": _utc_now(),
                }
                for model in models
            ],
            "has_more": False,
            "first_id": models[0] if models else None,
            "last_id": models[-1] if models else None,
        }
        return JSONResponse(payload, headers=_anthropic_headers())

    @app.post("/v1/messages/count_tokens")
    async def count_tokens(
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> JSONResponse:
        del x_api_key
        request.state.started_at = time.perf_counter()
        _authorize_request(request, settings)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ProxyError("Request body must be a JSON object.")
        input_tokens = estimate_tokens(payload)
        context = RequestLogContext(
            request_id=f"ctok_{uuid.uuid4().hex[:10]}",
            endpoint_name="count_tokens",
            inbound_method=request.method,
            inbound_url=str(request.url),
            upstream_url=None,
            requested_model=str(payload.get("model") or "").strip() or None,
            upstream_model=None,
            response_model=None,
            stream=False,
            input_tokens=input_tokens,
            max_tokens=None,
            inbound_payload=payload,
            upstream_payload=None,
        )
        request.state.log_context = context
        app.state.pretty_logger.log_request_start(context)
        latency_ms = int((time.perf_counter() - request.state.started_at) * 1000)
        app.state.pretty_logger.log_request_end(
            context,
            status_code=200,
            latency_ms=latency_ms,
            output_tokens=0,
            stop_reason="input_estimate",
        )
        return JSONResponse({"input_tokens": input_tokens}, headers=_anthropic_headers())

    @app.post("/v1/messages")
    async def messages(
        request: Request,
        x_api_key: str | None = Header(default=None),
        anthropic_version: str | None = Header(default=None),
    ):
        del x_api_key, anthropic_version
        request.state.started_at = time.perf_counter()
        inbound_api_key = _authorize_request(request, settings)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ProxyError("Request body must be a JSON object.")

        requested_model = str(payload.get("model") or "").strip()
        upstream_model = settings.resolve_upstream_model(requested_model or None)
        response_model = upstream_model if settings.force_upstream_model else (requested_model or upstream_model)
        openai_payload = build_openai_request(
            payload,
            upstream_model,
            default_max_tokens=settings.default_max_tokens,
        )
        input_tokens = estimate_tokens(payload)
        should_stream = bool(payload.get("stream", False))
        context = RequestLogContext(
            request_id=f"msg_{uuid.uuid4().hex[:10]}",
            endpoint_name="messages",
            inbound_method=request.method,
            inbound_url=str(request.url),
            upstream_url=settings.upstream_base_url,
            requested_model=requested_model or None,
            upstream_model=upstream_model,
            response_model=response_model,
            stream=should_stream,
            input_tokens=input_tokens,
            max_tokens=int(openai_payload.get("max_completion_tokens") or settings.default_max_tokens),
            inbound_payload=payload,
            upstream_payload=openai_payload,
        )
        request.state.log_context = context
        app.state.pretty_logger.log_request_start(context)

        if should_stream:
            return StreamingResponse(
                _stream_messages(
                    settings=settings,
                    upstream_payload=openai_payload,
                    inbound_api_key=inbound_api_key,
                    requested_model=response_model,
                    input_tokens=input_tokens,
                    transport=upstream_transport,
                    logger=app.state.pretty_logger,
                    context=context,
                    started_at=request.state.started_at,
                ),
                media_type="text/event-stream",
                headers=_anthropic_headers(),
            )

        response_data = await _post_upstream_json(
            settings=settings,
            payload=openai_payload,
            inbound_api_key=inbound_api_key,
            transport=upstream_transport,
        )
        anthropic_response = build_anthropic_message_response(
            response_data,
            requested_model=response_model,
            input_tokens=input_tokens,
        )
        latency_ms = int((time.perf_counter() - request.state.started_at) * 1000)
        app.state.pretty_logger.log_request_end(
            context,
            status_code=200,
            latency_ms=latency_ms,
            output_tokens=int(anthropic_response.get("usage", {}).get("output_tokens") or 0),
            stop_reason=str(anthropic_response.get("stop_reason") or "end_turn"),
        )
        return JSONResponse(anthropic_response, headers=_anthropic_headers())

    return app


def _authorize_request(request: Request, settings: Settings) -> str | None:
    inbound_api_key = _extract_api_key(request)
    expected = settings.gateway_api_key
    if expected and inbound_api_key != expected:
        raise ProxyError("Invalid API key.", status_code=401, error_type="authentication_error")
    return inbound_api_key


def _upstream_headers(settings: Settings, inbound_api_key: str | None) -> dict[str, str]:
    token = settings.upstream_api_key or inbound_api_key
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _post_upstream_json(
    *,
    settings: Settings,
    payload: dict[str, Any],
    inbound_api_key: str | None,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    headers = _upstream_headers(settings, inbound_api_key)
    async with httpx.AsyncClient(
        timeout=settings.upstream_timeout_seconds,
        transport=transport,
        trust_env=False,
    ) as client:
        try:
            response = await client.post(settings.upstream_base_url, headers=headers, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:1000]
            raise ProxyError(
                f"Upstream returned {exc.response.status_code}: {detail}",
                status_code=exc.response.status_code,
                error_type="api_error",
            ) from exc
        except httpx.RequestError as exc:
            raise ProxyError(
                f"Could not reach upstream: {exc}",
                status_code=502,
                error_type="api_error",
            ) from exc

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise ProxyError("Upstream returned invalid JSON.", status_code=502, error_type="api_error") from exc
    if not isinstance(data, dict):
        raise ProxyError("Upstream returned a non-object response.", status_code=502, error_type="api_error")
    return data


async def _stream_messages(
    *,
    settings: Settings,
    upstream_payload: dict[str, Any],
    inbound_api_key: str | None,
    requested_model: str,
    input_tokens: int,
    transport: httpx.AsyncBaseTransport | None,
    logger: PrettyLogger,
    context: RequestLogContext,
    started_at: float,
) -> AsyncIterator[str]:
    translator = StreamTranslator(requested_model=requested_model, input_tokens=input_tokens)
    for event in translator.start_events():
        yield _sse_frame(event)

    headers = _upstream_headers(settings, inbound_api_key)
    async with httpx.AsyncClient(
        timeout=settings.upstream_timeout_seconds,
        transport=transport,
        trust_env=False,
    ) as client:
        try:
            async with client.stream("POST", settings.upstream_base_url, headers=headers, json=upstream_payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    stripped = line.strip()
                    if not stripped.startswith("data:"):
                        continue
                    data_str = stripped[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    for event in translator.consume_chunk(chunk):
                        yield _sse_frame(event)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:1000]
            logger.log_request_error(
                context,
                status_code=exc.response.status_code,
                message=f"Upstream returned {exc.response.status_code}: {detail}",
                latency_ms=int((time.perf_counter() - started_at) * 1000),
            )
            error_event = {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": f"Upstream returned {exc.response.status_code}: {detail}",
                },
            }
            yield _sse_frame(error_event)
            return
        except httpx.RequestError as exc:
            logger.log_request_error(
                context,
                status_code=502,
                message=f"Could not reach upstream: {exc}",
                latency_ms=int((time.perf_counter() - started_at) * 1000),
            )
            error_event = {
                "type": "error",
                "error": {"type": "api_error", "message": f"Could not reach upstream: {exc}"},
            }
            yield _sse_frame(error_event)
            return

    for event in translator.finish_events():
        yield _sse_frame(event)
    logger.log_request_end(
        context,
        status_code=200,
        latency_ms=int((time.perf_counter() - started_at) * 1000),
        output_tokens=estimate_tokens({"output": "".join(translator.output_fragments)}),
        stop_reason=event.get("delta", {}).get("stop_reason") if isinstance(event, dict) else None,
    )


def get_app() -> FastAPI:
    """Module-level factory for ``uvicorn claude_code_proxy.app:app`` style usage.

    Falls back to a bare FastAPI instance when ``UPSTREAM_BASE_URL`` is not yet
    configured so that importing this module in tests never raises.
    """
    try:
        return create_app()
    except Exception:
        return FastAPI(title="Claude Code Proxy")


app = get_app()
