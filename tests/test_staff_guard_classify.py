import pytest

from dashboard import staff_guard as sg


@pytest.mark.parametrize("method,path,want", [
    ("GET", "/api/portal/T/view", "pass"),
    ("PUT", "/api/portal/T/folds", "exempt"),
    ("POST", "/api/portal/T/cards/abc/open", "background"),
    ("POST", "/api/portal/T/process-request", "background"),
    ("POST", "/api/intake/save-draft", "background"),
    ("POST", "/api/portal/T/scene-pref", "background"),
    ("POST", "/api/onboarding/book", "guard"),
    ("POST", "/api/portal/T/chat", "guard"),
    ("DELETE", "/api/portal/T/remedies/x", "guard"),
    ("PATCH", "/api/anything", "guard"),
])
def test_classify(method, path, want):
    assert sg.classify(method, path) == want


@pytest.mark.parametrize("path,needle", [
    ("/api/onboarding/book", "books a real appointment and emails Mel and Rae"),
    ("/api/consult/book", "books a real appointment and emails Mel and Rae"),
    ("/calendar/register", "registers Mel for a live session with Zoom"),
    ("/api/portal/T/checkout", "starts a payment as Mel"),
    ("/api/portal/T/family-plan/cancel", "cancels Mel's subscription or plan"),
    ("/api/portal/T/chat", "sends a chat message as Mel"),
    ("/api/intake/submit", "submits Mel's intake form"),
    ("/api/portal/T/cart/set-qty", "changes Mel's cart or invoice"),
    ("/api/portal/T/share-consent", "changes Mel's consent or preferences"),
    ("/api/portal/T/photo", "uploads a file to Mel's record"),
    ("/api/coach-thread/member/message", "sends a message or request as Mel to another member"),
    ("/api/portal/T/something-new", "This changes Mel's account."),
])
def test_describe(path, needle):
    assert needle in sg.describe(path, "Mel")


def test_describe_without_a_name():
    assert sg.describe("/api/portal/T/chat", "") == "This sends a chat message as the client."


# Review round 1, 2026-09-26: two background patterns matched no real route, and some
# wording understated what a route does.
@pytest.mark.parametrize("method,path,want", [
    ("POST", "/api/portal/T/recommendation/section", "background"),
    ("POST", "/api/portal/T/eye-vision-report/state", "background"),
    ("POST", "/api/portal/T/recommendation/click", "background"),
    ("POST", "/api/portal/T/open", "background"),       # read receipt (round 3)
    ("POST", "/api/invoice/T/open", "background"),
    ("POST", "/chat/tts", "exempt"),
    ("POST", "/portal/logout", "exempt"),
    ("POST", "/api/portal/T/recommendation/accept", "guard"),
    ("POST", "/api/portal/T/eye-vision-report/request-review", "guard"),
])
def test_classify_real_routes(method, path, want):
    assert sg.classify(method, path) == want


@pytest.mark.parametrize("path,needle", [
    ("/api/portal/T/appointment-proposals/4/confirm", "confirms an appointment for Mel and books it"),
    ("/api/portal/T/appointment-proposals/4/messages", "sends an appointment request or reply to the team"),
    ("/api/portal/T/pay-consent", "lets a caregiver pay Mel's orders"),
    ("/api/peer/optin", "changes Mel's peer connection setting"),
    ("/api/peer-thread/3/block", "blocks or reports a member as Mel"),
    ("/api/coach-thread/member/report", "blocks or reports a member as Mel"),
    ("/api/portal/T/request-analysis", "sends Mel's request for an analysis to the team"),
    ("/api/portal/T/remedies/request-review", "sends Mel's request for a review to the team"),
    ("/api/portal/T/recommendation/dismiss", "changes Mel's recommendations"),
])
def test_describe_says_what_happens(path, needle):
    assert needle in sg.describe(path, "Mel")
