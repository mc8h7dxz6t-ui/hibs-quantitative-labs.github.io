"""HTTP webhook API for remote queue injection."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlparse

from media_suite.config import WEBHOOK_HOST, WEBHOOK_PORT, WEBHOOK_TOKEN
from media_suite.queue import enqueue_url, queue_depth

HandlerFactory = Callable[[], type[BaseHTTPRequestHandler]]


def _authorized(headers, body: dict) -> bool:
    if not WEBHOOK_TOKEN:
        return False
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer ") and auth[7:].strip() == WEBHOOK_TOKEN:
        return True
    token = headers.get("X-Webhook-Token", "") or body.get("token", "")
    return token == WEBHOOK_TOKEN


def create_handler() -> type[BaseHTTPRequestHandler]:
  class WebhookHandler(BaseHTTPRequestHandler):
    server_version = "ForensicMediaSuite/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return  # quiet default access log

    def _send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "queue_depth": queue_depth(),
                    "auth_required": bool(WEBHOOK_TOKEN),
                },
            )
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in {"/queue", "/api/queue"}:
            self._send_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON body"})
            return

        if not _authorized(self.headers, body):
            self._send_json(401, {"error": "unauthorized"})
            return

        url = (body.get("url") or "").strip()
        if not url:
            self._send_json(400, {"error": "url is required"})
            return

        fmt = (body.get("format") or "mp4").strip().lower()
        profile = (body.get("prores_profile") or body.get("profile") or "").strip().lower() or None

        try:
            queue_path = enqueue_url(url, fmt, prores_profile=profile)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        self._send_json(
            202,
            {
                "status": "queued",
                "url": url,
                "format": fmt,
                "prores_profile": profile,
                "queue_file": str(queue_path.resolve()),
                "queue_depth": queue_depth(),
            },
        )

  return WebhookHandler


def run_webhook_server(
    *,
    host: str | None = None,
    port: int | None = None,
    block: bool = True,
) -> ThreadingHTTPServer:
    bind_host = host or WEBHOOK_HOST
    bind_port = port or WEBHOOK_PORT
    handler = create_handler()
    server = ThreadingHTTPServer((bind_host, bind_port), handler)

    if block:
        print(f"[*] Webhook API listening on http://{bind_host}:{bind_port}")
        print("[*] POST /queue  — JSON: {\"url\": \"...\", \"format\": \"mp4\"}")
        print("[*] GET  /health — liveness probe")
        server.serve_forever()
    return server


def start_webhook_background() -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = run_webhook_server(block=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
