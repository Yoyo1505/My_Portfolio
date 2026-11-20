import re

INJECTION = [
    r"ignore (all |any )?(previous|prior|above) (instructions|rules)", r"you are now", r"system prompt", r"developer (mode|message)",
    r"act as (an? )?(admin|administrator|supervisor|agent)", r"reveal (your )?(instructions|prompt)", r"pretend (that )?you",
]
ESCALATION = {
    "fraud": [r"fraud", r"unauthori[sz]ed", r"didn'?t (make|place|order)", r"never (placed|ordered|made)", r"hacked", r"someone (used|accessed)", r"stolen", r"account (was )?compromised"],
    "hardship": [r"hardship", r"lost my job", r"laid off", r"can'?t afford", r"cannot afford", r"unable to pay", r"medical emergency", r"no income"],
    "fee_waiver": [r"waive", r"remove (the )?(late )?fee", r"refund (the )?(late )?fee"],
    "limit_decline": [r"(exact|my) (spending )?limit", r"why (was|did) (i|my).*(declin|reject|denied)", r"declined at checkout"],
    "dispute": [r"never (arrived|received|delivered)", r"not delivered", r"didn'?t (arrive|receive)", r"open a dispute", r"file a dispute"],
    "legal": [r"lawyer", r"attorney", r"sue ", r"lawsuit", r"regulator", r"cfpb"],
}
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,16}\b")
MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?")
DATE_RE = re.compile(r"\b(?:20\d{2}-\d{2}-\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2}(?:, ?20\d{2})?)\b")


def injection_attempt(text):
    return any(re.search(p, text, re.I) for p in INJECTION)


def escalation_category(text):
    for cat, pats in ESCALATION.items():
        if any(re.search(p, text, re.I) for p in pats):
            return cat
    return None


def redact(text):
    return CARD_RE.sub("[redacted number]", text)


def _norm_money(s):
    return round(float(s.replace("$", "").replace(",", "").strip()), 2)


def amounts(text):
    return {_norm_money(m) for m in MONEY_RE.findall(text)}


def evidence_amounts(text):
    nums = {round(float(x), 2) for x in re.findall(r"\d+\.\d+", text)}
    return nums | amounts(text)


def grounding_report(answer, evidence_text):
    ev = evidence_amounts(evidence_text)
    bad = sorted(a for a in amounts(answer) if a not in ev)
    months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
    ev_dates = set(re.findall(r"20\d{2}-\d{2}-\d{2}", evidence_text))
    bad_dates = []
    for m in DATE_RE.findall(answer):
        iso = re.fullmatch(r"20\d{2}-\d{2}-\d{2}", m)
        if iso:
            ok = m in ev_dates
        else:
            parts = re.match(r"([A-Za-z]+)\.? (\d{1,2})", m)
            mon, day = months[parts.group(1)[:3].lower()], int(parts.group(2))
            ok = any(int(d[5:7]) == mon and int(d[8:10]) == day for d in ev_dates)
        if not ok:
            bad_dates.append(m)
    return {"ungrounded_amounts": bad, "ungrounded_dates": bad_dates, "grounded": not bad and not bad_dates}
