# Contributing to Claude Code Proxy

First off, thank you for considering contributing! 🎉

## Quick setup

```bash
git clone https://github.com/seanbabalala/claude-code-proxy.git
cd claude-code-proxy
uv sync --dev
```

## Development workflow

1. **Create a branch** from `main`:
   ```bash
   git checkout -b my-feature
   ```

2. **Make your changes** in `src/claude_code_proxy/`.

3. **Run the tests** to make sure nothing is broken:
   ```bash
   uv run pytest -v
   ```

4. **Open a Pull Request** against `main`.

## Project structure

```
src/claude_code_proxy/
├── app.py          # FastAPI app & route handlers
├── translator.py   # Anthropic ↔ OpenAI format conversion
├── config.py       # Settings (from environment variables)
├── logger.py       # Rich terminal logging
├── errors.py       # Error types
├── __main__.py     # CLI entry point
└── __init__.py
tests/
├── test_app.py
└── test_translator.py
```

## Guidelines

- **Keep changes focused.** One PR per logical change.
- **Write tests** for new translation logic or API behavior.
- **Match existing style.** The codebase uses type hints, `from __future__ import annotations`, and standard FastAPI patterns.
- **Update the README** if you change user-facing behavior or add environment variables.

## Reporting bugs

Please use the [Bug Report template](https://github.com/seanbabalala/claude-code-proxy/issues/new?template=bug_report.yml) on GitHub Issues.

## Questions?

Open an [issue](https://github.com/seanbabalala/claude-code-proxy/issues) — happy to help.
