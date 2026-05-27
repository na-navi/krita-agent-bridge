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

    def _request(self, method: str, path: str, body: Any | None = None) -> EndpointResult:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            data_bytes: bytes | None = None
            headers: dict[str, str] = {}
            if body is not None:
                data_bytes = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                resp_body = response.read().decode("utf-8")
                resp_data = json.loads(resp_body) if resp_body else None
                return EndpointResult(True, url, response.status, resp_data)
        except urllib.error.HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8")
            except Exception:
                pass
            msg = f"{exc} — {error_body}" if error_body else str(exc)
            return EndpointResult(False, url, exc.code, None, msg)
        except Exception as exc:  # noqa: BLE001 - CLI diagnostics should not crash here.
            return EndpointResult(False, url, None, None, str(exc))

    def get_json(self, path: str) -> EndpointResult:
        return self._request("GET", path)

    def post_json(self, path: str, body: Any) -> EndpointResult:
        return self._request("POST", path, body)
