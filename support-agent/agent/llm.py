import json
import os
import time
import urllib.request

HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT_S", 180))


class OllamaLLM:
    name = "ollama"

    def __init__(self, model=MODEL, host=HOST, timeout=TIMEOUT):
        self.model, self.host, self.timeout = model, host, timeout
        self.calls = 0

    def chat(self, messages, tools=None):
        body = {"model": self.model, "messages": messages, "stream": False, "keep_alive": "30m",
                "options": {"temperature": 0, "num_ctx": 3072, "seed": 7, "num_predict": 200}}
        if tools:
            body["tools"] = tools
        req = urllib.request.Request(f"{self.host}/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    self.calls += 1
                    return json.load(r)["message"]
            except Exception:
                if attempt == 1:
                    raise
                time.sleep(2 * (attempt + 1))


class MockLLM:
    name = "mock"

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        names = [t["function"]["name"] for t in (tools or [])]
        user = [m for m in messages if m["role"] == "user"][-1]["content"]
        if not tool_msgs and names:
            calls = []
            if "search_policy" in names:
                calls.append({"function": {"name": "search_policy", "arguments": {"query": user}}})
            if "get_next_payment" in names and any(w in user.lower() for w in ("next", "when", "due")):
                calls.append({"function": {"name": "get_next_payment", "arguments": {}}})
            elif "list_orders" in names:
                calls.append({"function": {"name": "list_orders", "arguments": {}}})
            return {"role": "assistant", "content": "", "tool_calls": calls}
        lines = []
        for m in tool_msgs:
            try:
                data = json.loads(m["content"])
            except Exception:
                continue
            if isinstance(data, list) and data and "policy" in data[0]:
                lines.append(data[0]["text"].splitlines()[0])
            elif isinstance(data, dict) and "due" in data:
                lines.append(f"Your next payment is ${data['amount']:.2f} due {data['due']} for {data.get('merchant', 'your order')}.")
            elif isinstance(data, list) and data and "order_id" in data[0]:
                lines.append("You have " + ", ".join(f"{o['merchant']} ({o['order_id']}, ${o['total']:.2f})" for o in data) + ".")
        return {"role": "assistant", "content": " ".join(lines) or "I could not find that information."}
