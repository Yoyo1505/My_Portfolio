import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.agent import SupportAgent  # noqa: E402
from agent.llm import MockLLM, OllamaLLM  # noqa: E402


def check(case, out):
    ans = out["answer"].lower()
    inc = all(re.search(p, ans, re.I) for p in case["must_include"])
    exc = not any(re.search(re.escape(p), ans, re.I) for p in case["must_not_include"])
    return {"route_ok": out["route"] == case["expected_route"], "include_ok": inc, "exclude_ok": exc,
            "grounded": bool(out["grounding"].get("grounded"))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="ollama", choices=["ollama", "mock"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--cases", default=str(ROOT / "eval" / "golden.jsonl"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    llm = MockLLM() if a.backend == "mock" else OllamaLLM(**({"model": a.model} if a.model else {}))
    agent = SupportAgent(llm)
    cases = [json.loads(l) for l in Path(a.cases).read_text(encoding="utf-8").splitlines() if l.strip()]
    if a.limit:
        cases = cases[:a.limit]
    rows = []
    for c in cases:
        out = agent.respond(c["user_id"], c["question"])
        r = check(c, out)
        r["passed"] = all(r.values())
        row = {"id": c["id"], "question": c["question"], "expected_route": c["expected_route"], "route": out["route"], "answer": out["answer"],
               "tool_calls": [t["name"] for t in out["tool_calls"]], "flags": out["flags"], "latency_s": out["latency_s"], **r}
        rows.append(row)
        print(f"{c['id']} {'PASS' if r['passed'] else 'FAIL'} route={out['route']}/{c['expected_route']} {out['latency_s']}s flags={out['flags']}", flush=True)
    n = len(rows)
    summary = {"backend": llm.name, "model": getattr(llm, "model", "mock"), "cases": n, "passed": sum(r["passed"] for r in rows),
               "route_accuracy": sum(r["route_ok"] for r in rows) / n, "include_rate": sum(r["include_ok"] for r in rows) / n,
               "exclude_rate": sum(r["exclude_ok"] for r in rows) / n, "grounded_rate": sum(r["grounded"] for r in rows) / n,
               "latency_p50_s": sorted(r["latency_s"] for r in rows)[n // 2], "latency_max_s": max(r["latency_s"] for r in rows),
               "ran_at": time.strftime("%Y-%m-%d %H:%M")}
    print(json.dumps(summary, indent=1))
    out_path = Path(a.out) if a.out else ROOT / "eval" / "results" / f"{summary['backend']}-{time.strftime('%Y%m%d-%H%M')}.json"
    out_path.parent.mkdir(exist_ok=True, parents=True)
    out_path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
