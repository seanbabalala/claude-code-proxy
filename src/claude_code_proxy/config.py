from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            os.environ.setdefault(key, value)


@dataclass(slots=True)
class Settings:
    upstream_base_url: str
    upstream_api_key: str | None = None
    gateway_api_key: str | None = None
    default_upstream_model: str | None = None
    default_max_tokens: int = 32000
    force_upstream_model: bool = False
    pretty_logs: bool = True
    log_payload_max_chars: int = 1400
    model_map: dict[str, str] = field(default_factory=dict)
    host: str = "127.0.0.1"
    port: int = 8000
    upstream_timeout_seconds: int = 180

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        raw_model_map = os.environ.get("MODEL_MAP_JSON", "").strip()
        model_map: dict[str, str] = {}
        if raw_model_map:
            loaded = json.loads(raw_model_map)
            if not isinstance(loaded, dict):
                raise ValueError("MODEL_MAP_JSON must be a JSON object.")
            model_map = {str(key): str(value) for key, value in loaded.items()}

        upstream_base_url = os.environ.get("UPSTREAM_BASE_URL", "").strip()
        if not upstream_base_url:
            raise ValueError("UPSTREAM_BASE_URL is required.")

        return cls(
            upstream_base_url=upstream_base_url,
            upstream_api_key=os.environ.get("UPSTREAM_API_KEY", "").strip() or None,
            gateway_api_key=os.environ.get("GATEWAY_API_KEY", "").strip() or None,
            default_upstream_model=os.environ.get("DEFAULT_UPSTREAM_MODEL", "").strip() or None,
            default_max_tokens=int(os.environ.get("DEFAULT_MAX_TOKENS", "32000")),
            force_upstream_model=os.environ.get("FORCE_UPSTREAM_MODEL", "").strip().lower() in {"1", "true", "yes", "on"},
            pretty_logs=os.environ.get("PRETTY_LOGS", "true").strip().lower() in {"1", "true", "yes", "on"},
            log_payload_max_chars=int(os.environ.get("LOG_PAYLOAD_MAX_CHARS", "1400")),
            model_map=model_map,
            host=os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=int(os.environ.get("PORT", "8000")),
            upstream_timeout_seconds=int(os.environ.get("UPSTREAM_TIMEOUT_SECONDS", "180")),
        )

    def resolve_upstream_model(self, requested_model: str | None) -> str:
        if self.force_upstream_model and self.default_upstream_model:
            return self.default_upstream_model
        if requested_model and requested_model in self.model_map:
            return self.model_map[requested_model]
        if requested_model:
            return requested_model
        if self.default_upstream_model:
            return self.default_upstream_model
        raise ValueError("No model was provided and DEFAULT_UPSTREAM_MODEL is not configured.")

    def advertised_models(self) -> list[str]:
        models: list[str] = []
        for public_name in self.model_map:
            if public_name not in models:
                models.append(public_name)
        if self.default_upstream_model and self.default_upstream_model not in models:
            models.append(self.default_upstream_model)
        return models or ["claude-opus-4-6-v1"]
