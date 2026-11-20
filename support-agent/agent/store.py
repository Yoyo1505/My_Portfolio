import json
import re
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "orders.json"


class NotFound(Exception):
    pass


class OrderStore:
    def __init__(self, path=DATA):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.today = date.fromisoformat(raw["today"])
        self._users = {u["user_id"]: u for u in raw["users"]}
        self._orders = raw["orders"]

    def user(self, user_id):
        if user_id not in self._users:
            raise NotFound("unknown user")
        return self._users[user_id]

    def _mine(self, user_id):
        self.user(user_id)
        return [o for o in self._orders if o["user_id"] == user_id]

    def list_orders(self, user_id):
        out = []
        for o in self._mine(user_id):
            nxt = self._next(o)
            row = {"order_id": o["order_id"], "merchant": o["merchant"], "item": o["item"], "total": o["total"],
                   "status": o["status"], "plan": o["plan"], "next_payment": nxt}
            for k in ("refund", "dispute"):
                if o.get(k):
                    row[k] = o[k]
            row["reschedule_used"] = bool(o.get("reschedule_used"))
            out.append(row)
        return out

    def get_order(self, user_id, order_id):
        mine = self._mine(user_id)
        for o in mine:
            if o["order_id"] == order_id:
                return self._detail(o)
        matches = self.find_order(user_id, order_id)
        if len(matches) == 1 and not re.fullmatch(r"ord_\d+", order_id.strip().lower()):
            return self._detail(matches[0])
        raise NotFound("order not found for this account")

    def find_order(self, user_id, text):
        text = text.lower().strip()
        out = []
        for o in self._mine(user_id):
            names = (o["order_id"], o["merchant"].lower(), o["item"].lower())
            if any(n in text for n in names) or (len(text) >= 4 and any(text in n for n in names[1:])):
                out.append(o)
        return out

    def next_payment(self, user_id):
        upcoming = []
        for o in self._mine(user_id):
            n = self._next(o)
            if n and o["status"] in ("active", "disputed"):
                upcoming.append({"order_id": o["order_id"], "merchant": o["merchant"], **n})
        upcoming.sort(key=lambda x: x["due"])
        return upcoming[0] if upcoming else None

    def _next(self, o):
        for i in o["installments"]:
            if i["status"] in ("scheduled", "failed", "late"):
                return {"installment": i["n"], "amount": i["amount"], "due": i["due"], "status": i["status"]}
        return None

    def _detail(self, o):
        d = {k: v for k, v in o.items() if k != "user_id"}
        d["paid_total"] = round(sum(i["amount"] for i in o["installments"] if i["status"] == "paid"), 2)
        d["remaining_total"] = round(o["total"] - d["paid_total"], 2)
        d["past_due"] = [i for i in o["installments"] if i["status"] in ("failed", "late")]
        d["reschedule_used"] = bool(o.get("reschedule_used"))
        return d
