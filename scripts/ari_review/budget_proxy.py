"""Root-owned, fixed-upstream Responses gate. No retries; reserve before send.

The real key never enters the Codex action. Each call reserves $0.50 against a
$5 ceiling. Only the pinned 400k-context mini model, <=8192 output tokens,
standard tier and unbilled local function/custom tools are accepted. The bound
includes full uncached input, 25% cache-write uplift and 10% regional uplift at
the documented rates; price-policy expiration fails closed.
"""

from __future__ import annotations

import argparse
import http.client
import json
import threading
from datetime import date
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Budget:
    def __init__(self, policy: dict):
        self.policy = policy
        self.calls = 0
        self.lock = threading.Lock()

    def admit(self, body: dict) -> dict:
        p = self.policy
        if not isinstance(body, dict):
            raise ValueError("invalid Responses request")
        if body.get("prompt_cache_retention") not in (None, "in-memory"):
            raise ValueError("extended cache-write billing forbidden")
        if date.today().isoformat() > p["pricing_valid_through"]:
            raise ValueError("pricing policy expired; review rates before enabling")
        if body.get("model") != "gpt-5.4-mini-2026-03-17" or p["model"] != body["model"]:
            raise ValueError("model is not covered by the cost bound")
        if body.get("service_tier", "default") not in ("default", "auto") or body.get("background"):
            raise ValueError("nonstandard billing/background work forbidden")
        if any(t.get("type") not in {"function", "custom"} for t in body.get("tools", [])):
            raise ValueError("hosted/paid tools forbidden")

        def text_only(value):
            if isinstance(value, dict):
                if value.get("type") in {"input_image", "input_file", "input_audio"}:
                    raise ValueError("non-text billing forbidden")
                for child in value.values():
                    text_only(child)
            elif isinstance(value, list):
                for child in value:
                    text_only(child)

        text_only(body)
        # Reserve rather than refund based on uncertain streaming usage. Failed
        # sends/timeouts count too. A caller cannot restart this root-owned gate.
        with self.lock:
            cap = min(10, p["max_api_requests"])
            charge = max(Decimal("0.50"), Decimal(p["reserved_usd_per_request"]))
            if self.calls >= cap or (self.calls + 1) * charge > min(
                Decimal("5"), Decimal(p["max_usd_per_review"])
            ):
                raise ValueError("review API/cost limit exhausted")
            self.calls += 1
        return {
            **body,
            "max_output_tokens": min(8192, p["max_output_tokens"]),
            "service_tier": "default",
            "store": False,
        }


def serve(policy: dict, key: str, capability: str, port: int) -> None:
    budget = Budget(policy)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *_args):
            pass  # No request, key, or candidate text in logs.

        def do_POST(self):
            if (
                self.path != "/responses"
                or self.headers.get("Authorization") != "Bearer " + capability
            ):
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8_000_000:
                    raise ValueError("request size limit")
                data = budget.admit(json.loads(self.rfile.read(length)))
            except (ValueError, TypeError, KeyError):
                self.send_error(429, "review policy or cost limit")
                return
            conn = http.client.HTTPSConnection("api.openai.com", timeout=120)
            try:
                conn.request(
                    "POST",
                    "/v1/responses",
                    json.dumps(data).encode(),
                    {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                )
                response = conn.getresponse()
                self.send_response(response.status)
                self.send_header(
                    "Content-Type", response.getheader("Content-Type", "application/json")
                )
                self.send_header("Connection", "close")
                self.end_headers()
                while chunk := response.read1(8192):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, http.client.HTTPException):
                self.close_connection = True  # Ambiguous sends are never retried here.
            finally:
                conn.close()

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    serve(config["policy"], config["key"], config["capability"], args.port)
