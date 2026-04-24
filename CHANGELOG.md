# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-04-24

### Added

- `POST /v1/messages` — non-streaming and streaming (SSE) translation
- `POST /v1/messages/count_tokens` — heuristic token estimation
- `GET /v1/models` — advertise available models to Claude Code
- Full bidirectional `tool_use` / `tool_result` translation
- Model name remapping via `MODEL_MAP_JSON`
- Force-pinning via `FORCE_UPSTREAM_MODEL`
- Rich terminal panels for request/response inspection
- Startup diagnostics for `~/.claude/settings.json` misconfiguration
- MIT license

[0.1.0]: https://github.com/seanbabalala/claude-code-proxy/releases/tag/v0.1.0
