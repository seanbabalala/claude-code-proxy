# Claude Code Proxy

A lightweight reverse proxy that lets [Claude Code](https://docs.anthropic.com/en/docs/claude-code) talk to any **OpenAI-compatible `chat/completions`** endpoint.

Many companies expose Claude models behind an OpenAI-compatible API gateway. Claude Code, however, speaks the **Anthropic Messages** protocol. This proxy sits in between and translates on the fly — no client patches required.

```
Claude Code  ──Anthropic Messages──▶  claude-code-proxy  ──OpenAI chat/completions──▶  Your Gateway
```

## Features

- `POST /v1/messages` — non-streaming & streaming (SSE)
- `POST /v1/messages/count_tokens` — heuristic token estimation
- `GET /v1/models` — advertise available models
- Full **tool_use / tool_result** bidirectional translation
- Model name remapping & force-pinning
- Rich terminal panels for request/response inspection
- Startup diagnostics that check `~/.claude/settings.json` for misconfiguration

## Quick start

```bash
# 1. Clone & install
git clone https://github.com/YOUR_USERNAME/claude-code-proxy.git
cd claude-code-proxy
uv sync          # or: pip install -e .

# 2. Configure
cp .env.example .env
# Edit .env — at minimum set UPSTREAM_BASE_URL to your gateway

# 3. Run
uv run python -m claude_code_proxy
```

The proxy listens on `http://127.0.0.1:8000` by default.

## Point Claude Code at the proxy

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8000
export ANTHROPIC_AUTH_TOKEN=local-dev-token
claude
```

Or persist in `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8000",
    "ANTHROPIC_AUTH_TOKEN": "local-dev-token"
  }
}
```

> **Tip:** `~/.claude/settings.json` takes precedence over shell exports in interactive sessions. If Claude Code seems to ignore the proxy, check that file first.

## Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `UPSTREAM_BASE_URL` | **Required.** Full URL of your `chat/completions` endpoint. | — |
| `UPSTREAM_API_KEY` | Token sent to the upstream. If empty, the inbound key is forwarded. | (empty) |
| `GATEWAY_API_KEY` | Token this proxy requires from callers. Empty = no auth. | (empty) |
| `DEFAULT_UPSTREAM_MODEL` | Fallback model name sent upstream. | — |
| `DEFAULT_MAX_TOKENS` | Fallback token budget. | `32000` |
| `FORCE_UPSTREAM_MODEL` | When `true`, always send `DEFAULT_UPSTREAM_MODEL` regardless of what the client requests. | `false` |
| `MODEL_MAP_JSON` | JSON object mapping client model names → upstream model names. | `{}` |
| `PRETTY_LOGS` | Rich request/response panels in the terminal. | `true` |
| `LOG_PAYLOAD_MAX_CHARS` | Truncate payload previews after this many chars. | `1400` |
| `HOST` | Bind host. | `127.0.0.1` |
| `PORT` | Bind port. | `8000` |
| `UPSTREAM_TIMEOUT_SECONDS` | Upstream HTTP timeout. | `180` |

## How it works

### Request translation (Anthropic → OpenAI)

| Anthropic Messages | OpenAI chat/completions |
|---|---|
| `system` (top-level) | `messages[0].role = "system"` |
| `messages[].content[].type = "tool_use"` | `messages[].tool_calls[].type = "function"` |
| `messages[].content[].type = "tool_result"` | `messages[].role = "tool"` |
| `max_tokens` | `max_completion_tokens` |
| `tools[].input_schema` | `tools[].function.parameters` |
| `tool_choice.type = "any"` | `tool_choice = "required"` |

### Response translation (OpenAI → Anthropic)

| OpenAI chat/completions | Anthropic Messages |
|---|---|
| `choices[0].message.content` | `content[].type = "text"` |
| `choices[0].message.tool_calls` | `content[].type = "tool_use"` |
| `finish_reason = "tool_calls"` | `stop_reason = "tool_use"` |
| SSE `choices[0].delta.content` | SSE `content_block_delta` / `text_delta` |
| SSE `choices[0].delta.tool_calls` | SSE `content_block_start` (tool_use) + `input_json_delta` |

## Running tests

```bash
uv run pytest
```

## Limitations

- `count_tokens` is a heuristic estimate (byte-length ÷ 4), not an exact tokenizer count.
- Non-text multimodal content blocks (images, etc.) are flattened to text placeholders.
- The proxy focuses on Claude Code compatibility rather than full Anthropic API surface coverage.

## License

[MIT](LICENSE)
