#!/usr/bin/env python3
"""End-to-end test of the $297x12 course plan, in Stripe TEST mode. No real money.

What it runs, against Stripe's test API and this checkout's own webhook code:
  1. A test clock, a customer on it, and a test-mode $297/month price.
  2. A real Checkout Session built by the app's own _price_checkout_params, with the
     metadata a SIGNED-OUT buyer gets (no email). You complete it in a browser with
     Stripe's test card 4242 4242 4242 4242.
  3. Every real event Stripe emits (checkout.session.completed, invoice.paid x12,
     customer.subscription.deleted) is posted to /webhook/stripe through Flask's test
     client, signed, so the real route and handlers run.
  4. The clock advances a month at a time. After each charge the plan's access must
     reach the new period end. At the 12th the lifetime cert must exist and the app
     must have cancelled the subscription in Stripe. One more month must bring no
     13th invoice.

Safety: the script re-executes itself with a CLEAN environment holding only the test
key and fake values, so no live key, mail sender or database can be reached. It
refuses any key that is not sk_test_. The app database is a temporary file. Mail and
alert senders are replaced with recorders.

Run:  doppler run --project remedy-match --config prd -- \
          python3 scripts/stripe_plan_testmode_e2e.py
Add --keep to leave the test clock in Stripe for inspection (default: deleted).
"""
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLEAN_FLAG = "PLAN_E2E_CLEAN_ENV"
WEBHOOK_SECRET = "whsec_plan_e2e_local"
PRICE_LOOKUP_KEY = "mu_plan_e2e_testmode_29700_month"
MONTH = 31 * 86400


def _reexec_clean():
    key = os.environ.get("STRIPE_TEST_SECRET_KEY", "")
    if not key.startswith("sk_test_"):
        sys.exit("STRIPE_TEST_SECRET_KEY is missing or is not a test key (sk_test_). Refusing.")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", ""),
        CLEAN_FLAG: "1",
        "STRIPE_SECRET_KEY": key,
        "STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET,
        # Same fakes as ci/run-tests.sh: the app imports, nothing real is reachable.
        "PINECONE_API_KEY": "pcsk_fake",
        "OPENAI_API_KEY": "sk-fake",
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SECRET_KEY": "plan-e2e",
        "CONSOLE_SECRET": "plan-e2e-fake",
    }
    os.execve(sys.executable, [sys.executable, "-u", __file__, *sys.argv[1:]], env)


if os.environ.get(CLEAN_FLAG) != "1":
    _reexec_clean()

if not os.environ.get("STRIPE_SECRET_KEY", "").startswith("sk_test_"):
    sys.exit("not a test key. Refusing.")

sys.path.insert(0, str(REPO))
os.chdir(REPO)

import app as appmod  # noqa: E402
from dashboard import stripe_pay  # noqa: E402

SENT = {"setup_link": [], "provision": [], "alert": []}
appmod.LOG_DB = Path(tempfile.mkdtemp(prefix="plan-e2e-")) / "chat_log.db"
appmod.send_mentorship_setup_link = lambda *a, **k: SENT["setup_link"].append(a) or ("test", None)
appmod._autoprovision_course_access = lambda email: SENT["provision"].append(email)
appmod._send_token_alert = lambda *a, **k: SENT["alert"].append(a)
appmod._pp.modules_completed_for_email = lambda e: None
appmod.app.config["TESTING"] = True
CLIENT = appmod.app.test_client()
FAILURES = []


def check(ok, what):
    print(f"  {'PASS' if ok else 'FAIL'}  {what}")
    if not ok:
        FAILURES.append(what)


def post_event(event):
    body = json.dumps(event).encode()
    ts = int(time.time())
    sig = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    r = CLIENT.post("/webhook/stripe", data=body, content_type="application/json",
                    headers={"Stripe-Signature": f"t={ts},v1={sig}"})
    if r.status_code != 200:
        FAILURES.append(f"webhook returned {r.status_code} for {event['type']}")
    return r.status_code


def rows():
    cx = sqlite3.connect(str(appmod.LOG_DB))
    try:
        return cx.execute("SELECT kind, status, email, expires_at, stripe_ref "
                          "FROM course_entitlements ORDER BY id").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        cx.close()


def events_since(created_gte, types):
    out, url = [], f"/events?limit=100&created[gte]={created_gte}"
    for t in types:
        url += f"&types[]={t}"
    page = stripe_pay._get(url)
    out.extend(page.get("data") or [])
    while page.get("has_more"):
        page = stripe_pay._get(url + f"&starting_after={out[-1]['id']}")
        out.extend(page.get("data") or [])
    return sorted(out, key=lambda e: e["created"])


def wait_clock(clock_id):
    for _ in range(120):
        c = stripe_pay._get(f"/test_helpers/test_clocks/{clock_id}")
        if c.get("status") == "ready":
            return c
        time.sleep(2)
    sys.exit("test clock did not become ready in 4 minutes")


def test_price():
    found = stripe_pay._get(f"/prices?lookup_keys[]={PRICE_LOOKUP_KEY}&limit=1").get("data") or []
    if found:
        return found[0]["id"]
    p = stripe_pay._post("/prices", {
        "unit_amount": "29700", "currency": "usd", "recurring[interval]": "month",
        "lookup_key": PRICE_LOOKUP_KEY,
        "product_data[name]": "MentorshipU plan, test-mode end-to-end check"})
    return p["id"]


def main():
    keep = "--keep" in sys.argv
    t0 = int(time.time()) - 5
    seen = set()

    def deliver_new():
        n = 0
        for ev in events_since(t0, ["checkout.session.completed", "invoice.paid",
                                    "customer.subscription.deleted"]):
            obj = ev["data"]["object"]
            mine = (obj.get("customer") == cus or obj.get("id") == sub_id
                    or obj.get("subscription") == sub_id)
            if ev["id"] in seen or not mine:
                continue
            seen.add(ev["id"])
            post_event(ev)
            n += 1
        return n

    clock = stripe_pay._post("/test_helpers/test_clocks",
                             {"frozen_time": str(int(time.time())), "name": "mu plan e2e"})
    print(f"test clock {clock['id']}")
    sub_id = None
    try:
        cus = stripe_pay._post("/customers", {"test_clock": clock["id"],
                                              "email": "plan-e2e-buyer@example.com"})["id"]
        price = test_price()
        # Exactly what courses_checkout builds for a signed-out buyer: no email anywhere
        # in the metadata. Only the customer binding differs, because a test clock needs
        # a named customer.
        params = stripe_pay._price_checkout_params(
            price, mode="subscription", customer_email=None,
            metadata={"kind": "course_purchase", "email": None, "product": "plan"},
            subscription_metadata={"kind": "course_plan", "email": None},
            success_url="https://example.com/done", cancel_url="https://example.com/cancel")
        params["customer"] = cus
        sess = stripe_pay._post("/checkout/sessions", params)
        print(f"\nOpen this test-mode checkout and pay with 4242 4242 4242 4242:\n{sess['url']}\n")
        for _ in range(450):
            s = stripe_pay._get(f"/checkout/sessions/{sess['id']}")
            if s.get("status") == "complete":
                sub_id = s["subscription"]
                break
            time.sleep(2)
        else:
            sys.exit("checkout was not completed within 15 minutes")
        print(f"checkout complete, subscription {sub_id}")

        for month in range(1, 13):
            for _ in range(15):
                if deliver_new():
                    time.sleep(3)
                    deliver_new()
                    break
                time.sleep(2)
            cx = sqlite3.connect(str(appmod.LOG_DB))
            charges = cx.execute("SELECT COUNT(*) FROM course_plan_charges WHERE sub_id=?",
                                 (sub_id,)).fetchone()[0]
            cx.close()
            sub = stripe_pay.get_subscription(sub_id)
            plan = [r for r in rows() if r[0] == "plan"]
            print(f"month {month}: charges counted {charges}, stripe status {sub['status']}")
            check(charges == month, f"month {month}: {month} charge(s) counted")
            if month < 12:
                check(bool(plan) and plan[0][2] == "plan-e2e-buyer@example.com",
                      f"month {month}: plan row belongs to the buyer")
                check(bool(plan) and float(plan[0][3] or 0) == float(sub["current_period_end"]),
                      f"month {month}: access runs to the Stripe period end")
                stripe_pay._post(f"/test_helpers/test_clocks/{clock['id']}/advance",
                                 {"frozen_time": str(int(sub["current_period_end"]) + 3600)})
                wait_clock(clock["id"])

        time.sleep(3)
        deliver_new()
        sub = stripe_pay._get(f"/subscriptions/{sub_id}")
        r = rows()
        print("after the 12th charge:", [(k, s) for k, s, *_ in r])
        check(sub.get("status") == "canceled", "the app cancelled the subscription in Stripe")
        check(any(k == "cert_onetime" and s == "active" for k, s, *_ in r),
              "the lifetime certification is active")
        check(not SENT["alert"], "no overcharge alert fired")

        frozen = int(wait_clock(clock["id"])["frozen_time"])
        stripe_pay._post(f"/test_helpers/test_clocks/{clock['id']}/advance",
                         {"frozen_time": str(frozen + MONTH)})
        wait_clock(clock["id"])
        time.sleep(3)
        deliver_new()
        invs = stripe_pay._get(f"/invoices?subscription={sub_id}&limit=100").get("data") or []
        paid = [i for i in invs if i.get("status") == "paid"]
        check(len(paid) == 12, f"exactly 12 paid invoices a month later (found {len(paid)})")
        check(any(k == "cert_onetime" and s == "active" for k, s, *_ in rows()),
              "the certification is still active after the cancellation event")
        print(f"access emails the app tried to send: {len(SENT['provision'])} (recorded, not sent)")
    finally:
        if keep:
            print(f"kept test clock {clock['id']}")
        else:
            stripe_pay._delete(f"/test_helpers/test_clocks/{clock['id']}")
            print(f"deleted test clock {clock['id']} and its test customer")

    print("\nRESULT:", "PASS" if not FAILURES else f"FAIL ({len(FAILURES)})")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
