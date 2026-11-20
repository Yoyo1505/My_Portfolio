import json
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
TODAY = date(2026, 11, 20)

POLICIES = {
    "01-pay-in-3.md": ("How Payly Pay-in-3 works", """Payly Pay-in-3 splits an eligible purchase of $30 to $600 into three payments.
- 34% of the order total is charged at checkout.
- The remaining two payments of 33% each are charged 14 and 28 days after checkout.
- There is no interest. Payly never charges more than the displayed schedule except for the late fee described in the Late Payments policy.
- Payment dates are fixed when the order is confirmed and are visible in the app under the order details.
- You can pay any installment early from the app at no cost."""),
    "02-pay-monthly.md": ("Payly Pay Monthly (6 months)", """Pay Monthly is available for orders between $250 and $2,500 at participating merchants.
- The order is split into six equal monthly installments; the first is charged at checkout.
- The APR is shown before you confirm and ranges from 0% to 24.9% depending on the merchant offer.
- Paying off the balance early never carries a prepayment penalty.
- A Pay Monthly order cannot be moved to Pay-in-3 after checkout."""),
    "03-rescheduling.md": ("Rescheduling a payment", """You may move the due date of one upcoming installment per order, up to 7 days later, from the app.
- A reschedule must be requested at least 24 hours before the original due date.
- The first installment charged at checkout cannot be rescheduled.
- A failed or late installment cannot be rescheduled; it must be paid or covered by the hardship program.
- Each order allows one reschedule in total. The reschedule is free."""),
    "04-late-payments.md": ("Late and failed payments", """When an installment payment fails, Payly retries it automatically after 2 days and again after 5 days.
- A late fee of $7 is applied once per installment if the payment is still unpaid 3 days after the due date. The fee never exceeds 25% of the installment amount.
- While an installment is past due, the account cannot start new Payly orders.
- Updating the payment method in the app and paying the overdue installment restores the account the same day.
- Payly never reports a single missed payment to credit bureaus."""),
    "05-refunds-returns.md": ("Refunds and returns", """When a merchant accepts a return, the refund is applied to your Payly order automatically.
- Refunds are processed within 5 business days after the merchant confirms the return to Payly.
- A refund first cancels upcoming installments, newest first, and then returns money for installments that were already paid to your original payment method.
- Payly cannot approve or reject a return; the merchant's return policy applies.
- If the merchant has not confirmed a return after 30 days, you can ask for a case review."""),
    "06-disputes.md": ("Disputes with a merchant", """If an item never arrived or is not as described, first contact the merchant.
- You may open a dispute 10 days after the expected delivery date, or within 60 days of the purchase if the merchant is unresponsive.
- While a dispute is open, upcoming installments for that order are paused. Installments already past due are not paused.
- A support specialist, not the assistant, decides disputes. The assistant can only collect the order and hand off to the disputes team.
- Decisions are communicated by email within 10 business days."""),
    "07-account-reactivation.md": ("Reactivating a suspended account", """An account is suspended when an installment is more than 10 days past due.
- To reactivate: pay all overdue installments and any late fee, then confirm a valid payment method.
- Reactivation is immediate after payment clears, usually within one hour.
- After reactivation the spending limit may start lower and is reviewed again after 30 days of on-time payments."""),
    "08-virtual-card.md": ("Payly Virtual Card", """The virtual card lets you use Pay-in-3 at merchants that are not integrated with Payly.
- The card number is generated in the app and is valid for a single purchase.
- The card can be used for purchases of $30 to $600 in the United States.
- Purchases with the virtual card follow the same Pay-in-3 schedule and late-payment rules.
- Never share a virtual card number. Payly support will never ask for it."""),
    "09-hardship.md": ("Hardship assistance", """Customers who lose income or face an emergency can ask for hardship assistance.
- Hardship review is performed by a human specialist. The assistant must always hand these cases off and must not promise any outcome.
- Possible measures include a longer payment extension and waiving the late fee. The measure is decided case by case.
- To start a review the customer only needs to describe the situation; no documents are needed at the first step."""),
    "10-fees.md": ("Fees overview", """Payly charges the merchant, not the shopper, for standard use.
- There is no sign-up fee, no monthly fee and no interest on Pay-in-3.
- The only shopper fee on Pay-in-3 is the $7 late fee described in the Late Payments policy.
- Pay Monthly may carry interest as described in its own policy.
- Fee waivers are decided by a human specialist; the assistant cannot waive or promise to waive a fee."""),
    "11-account-security.md": ("Account security and fraud", """If you see an order you did not make, or you suspect someone accessed your account, report it immediately.
- A security specialist reviews every report; the assistant must hand it off and never decide whether a charge is fraud.
- Payly will lock the account while the report is reviewed. Existing installments are paused during the review.
- Payly staff never ask for your password, one-time codes or full card numbers."""),
    "12-limits.md": ("Spending limits", """Each account has a spending limit that can change over time based on repayment history.
- The exact limit and the reasons for a decline are not disclosed in chat for security and fairness reasons; these questions go to a specialist.
- The Payly app shows the current available amount in the account tab.
- Participating merchants can be found in the app under Explore; availability varies by country."""),
}

USERS = [
    ("u001", "Maya Collins"), ("u002", "Diego Alvarez"), ("u003", "Priya Nair"), ("u004", "Tom Becker"),
    ("u005", "Lena Fischer"), ("u006", "Omar Haddad"), ("u007", "Sofia Rossi"), ("u008", "Kenji Mori"),
]


def schedule(order_date, total, plan, paid_n, failed_idx=None, late_idx=None):
    items = []
    if plan == "pay_in_3":
        shares = [0.34, 0.33, 0.33]
        offsets = [0, 14, 28]
    else:
        shares = [1 / 6] * 6
        offsets = [30 * i for i in range(6)]
    acc = 0.0
    for i, (s, off) in enumerate(zip(shares, offsets), 1):
        amt = round(total * s, 2) if i < len(shares) else round(total - acc, 2)
        acc += amt
        due = order_date + timedelta(days=off)
        status = "paid" if i <= paid_n else "scheduled"
        if failed_idx == i:
            status = "failed"
        if late_idx == i:
            status = "late"
        items.append({"n": i, "amount": amt, "due": due.isoformat(), "status": status,
                      "paid_on": due.isoformat() if status == "paid" else None})
    return items


def order(oid, user, merchant, item, total, days_ago, plan, paid_n, status="active", **kw):
    d = TODAY - timedelta(days=days_ago)
    o = {"order_id": oid, "user_id": user, "merchant": merchant, "item": item, "total": total, "plan": plan,
         "order_date": d.isoformat(), "status": status, "installments": schedule(d, total, plan, paid_n, kw.get("failed"), kw.get("late"))}
    for k in ("refund", "dispute", "late_fee", "reschedule_used"):
        if k in kw:
            o[k] = kw[k]
    return o


def build():
    (HERE / "policies").mkdir(exist_ok=True)
    for fn, (title, body) in POLICIES.items():
        (HERE / "policies" / fn).write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    orders = [
        order("ord_5001", "u001", "Northline Outdoors", "Trail jacket", 180.00, 9, "pay_in_3", 1),
        order("ord_5002", "u001", "Brightwave Audio", "Wireless earbuds", 120.00, 40, "pay_in_3", 3, status="paid"),
        order("ord_5003", "u002", "Atlas Furniture", "Standing desk", 840.00, 62, "pay_monthly", 3),
        order("ord_5004", "u002", "Pixel & Co", "Camera lens", 300.00, 20, "pay_in_3", 2, late_fee=None),
        order("ord_5005", "u003", "Verde Home", "Air purifier", 210.00, 18, "pay_in_3", 1, failed=2),
        order("ord_5006", "u003", "Northline Outdoors", "Tent", 330.00, 5, "pay_in_3", 1),
        order("ord_5007", "u004", "Luma Beauty", "Skincare set", 95.00, 33, "pay_in_3", 3, status="refunded",
              refund={"amount": 95.00, "status": "processed", "date": (TODAY - timedelta(days=4)).isoformat()}),
        order("ord_5008", "u004", "Brightwave Audio", "Speaker", 150.00, 11, "pay_in_3", 1,
              refund={"amount": 150.00, "status": "pending_merchant", "date": None}),
        order("ord_5009", "u005", "Tessera Games", "Console bundle", 450.00, 33, "pay_in_3", 2, late=3, late_fee=7.00),
        order("ord_5010", "u005", "Atlas Furniture", "Bookshelf", 260.00, 12, "pay_in_3", 1, status="disputed",
              dispute={"opened": (TODAY - timedelta(days=2)).isoformat(), "reason": "item not delivered", "status": "open"}),
        order("ord_5011", "u006", "Verde Home", "Blender", 85.00, 2, "pay_in_3", 1),
        order("ord_5012", "u007", "Pixel & Co", "Laptop sleeve", 55.00, 31, "pay_in_3", 3, status="paid"),
        order("ord_5013", "u007", "Aria Travel", "Luggage set", 520.00, 10, "pay_in_3", 1, reschedule_used=True),
        order("ord_5014", "u008", "Luma Beauty", "Hair dryer", 140.00, 15, "pay_in_3", 2),
    ]
    users = [{"user_id": u, "name": n, "email": n.lower().replace(" ", ".") + "@example.test"} for u, n in USERS]
    (HERE / "orders.json").write_text(json.dumps({"today": TODAY.isoformat(), "users": users, "orders": orders}, indent=1), encoding="utf-8")
    print(f"{len(POLICIES)} policies, {len(orders)} orders, {len(users)} users")


if __name__ == "__main__":
    build()
