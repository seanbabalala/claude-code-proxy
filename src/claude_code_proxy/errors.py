from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ProxyError(Exception):
    message: str
    status_code: int = 400
    error_type: str = "invalid_request_error"

    def to_payload(self) -> dict[str, object]:
        return {
            "type": "error",
            "error": {
                "type": self.error_type,
                "message": self.message,
            },
        }
