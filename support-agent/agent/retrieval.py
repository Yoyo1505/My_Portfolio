import math
import re
from collections import Counter
from pathlib import Path

POLICY_DIR = Path(__file__).resolve().parent.parent / "data" / "policies"
STOP = set("a an the of to in on for and or is are be can i my me do does how what when will it this that with at by from as if you your".split())
SYNONYMS = {
    "late": ["overdue", "past", "missed"], "fail": ["declined", "failed"], "refund": ["return", "returned"],
    "reschedule": ["move", "postpone", "delay", "extend", "extension"], "fraud": ["unauthorized", "hacked", "stolen"],
    "fee": ["charge", "charges", "cost"], "limit": ["spending", "decline"], "card": ["virtual"],
}


def tokenize(text):
    toks = [t for t in re.findall(r"[a-z0-9$%]+", text.lower()) if t not in STOP]
    out = []
    for t in toks:
        t = re.sub(r"(ing|ed|es|s)$", "", t) if len(t) > 4 else t
        out.append(t)
    return out


def expand(tokens):
    extra = []
    for key, words in SYNONYMS.items():
        if key in tokens or any(w in tokens for w in words):
            extra.append(key)
            extra += [tokenize(w)[0] for w in words]
    return tokens + extra


class PolicyIndex:
    def __init__(self, directory=POLICY_DIR, k1=1.4, b=0.75):
        self.k1, self.b = k1, b
        self.docs = []
        for p in sorted(Path(directory).glob("*.md")):
            text = p.read_text(encoding="utf-8")
            title = text.splitlines()[0].lstrip("# ").strip()
            for i, chunk in enumerate(self._chunks(text)):
                toks = tokenize(title + " " + chunk) + tokenize(title) * 2
                self.docs.append({"id": f"{p.stem}#{i}", "policy": p.stem, "title": title, "text": chunk, "tf": Counter(toks), "len": len(toks)})
        self.avg = sum(d["len"] for d in self.docs) / len(self.docs)
        df = Counter()
        for d in self.docs:
            df.update(d["tf"].keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    @staticmethod
    def _chunks(text):
        lines = [l for l in text.splitlines()[2:] if l.strip()]
        intro = [l for l in lines if not l.startswith("-")]
        bullets = [l for l in lines if l.startswith("-")]
        chunks, cur = [], intro[:]
        for b in bullets:
            cur.append(b)
            if len(" ".join(cur)) > 420:
                chunks.append("\n".join(cur))
                cur = intro[:1]
        if cur:
            chunks.append("\n".join(cur))
        return chunks

    def search(self, query, k=3):
        q = expand(tokenize(query))
        scored = []
        for d in self.docs:
            s = 0.0
            for t in q:
                f = d["tf"].get(t, 0)
                if f:
                    s += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * d["len"] / self.avg))
            if s > 0:
                scored.append((s, d))
        scored.sort(key=lambda x: -x[0])
        seen, out = set(), []
        for s, d in scored:
            out.append({"policy": d["policy"], "title": d["title"], "score": round(s, 3), "text": d["text"]})
            if len(out) == k:
                break
        return out
