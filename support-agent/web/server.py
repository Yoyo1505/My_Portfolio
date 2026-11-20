import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.agent import SupportAgent  # noqa: E402
from agent.llm import MockLLM, OllamaLLM  # noqa: E402
from agent.store import OrderStore  # noqa: E402

STORE = OrderStore()
AGENT = SupportAgent(MockLLM() if os.environ.get("SUPPORT_BACKEND") == "mock" else OllamaLLM(), STORE)
INDEX_HTML = (Path(__file__).parent / "index.html").read_bytes()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/users":
            data = [{"user_id": u["user_id"], "name": u["name"]} for u in STORE._users.values()]
            return self._send(200, json.dumps(data).encode())
        if self.path.startswith("/api/orders/"):
            uid = self.path.rsplit("/", 1)[1]
            try:
                return self._send(200, json.dumps(STORE.list_orders(uid)).encode())
            except Exception:
                return self._send(404, b"{}")
        return self._send(200, INDEX_HTML, "text/html; charset=utf-8")

    def do_POST(self):
        if self.path != "/api/chat":
            return self._send(404, b"{}")
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            out = AGENT.respond(body["user_id"], str(body["question"])[:600])
            slim = {k: out[k] for k in ("route", "answer", "tool_calls", "flags", "grounding", "latency_s") if k in out}
            return self._send(200, json.dumps(slim, default=str).encode())
        except Exception as e:
            return self._send(500, json.dumps({"error": str(e)}).encode())

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8060))
    print(f"Payly support agent on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
