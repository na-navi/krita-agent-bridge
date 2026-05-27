from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EndpointResult:
    ok: bool
    url: str
    status: int | None
    data: Any | None
    error: str | None = None


class JsonEndpointClient:
    """Tiny stdlib JSON client for local-only bridge experiments."""

    def __init__(self, base_url: str, timeout: float = 3.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get_json(self, path: str) -> EndpointResult:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                data = json.loads(body) if body else None
                return EndpointResult(True, url, response.status, data)
        except urllib.error.HTTPError as exc:
            return EndpointResult(False, url, exc.code, None, str(exc))
        except Exception as exc:  # noqa: BLE001 - CLI diagnostics should not crash here.
            return EndpointResult(False, url, None, None, str(exc))
