"""Warning-sign backstop for portal-chat triage (Glen's ruling, 20 Oct 2026).

These signs must always reach Dr. Glen at high urgency, whatever the model says.
The backstop is a keyword check on the client's own message, scoped to reports
of something happening, so an education question does not trip it."""
import json

import pytest

from dashboard import portal_triage as pt


# Several real phrasings per sign. Each must trip exactly its own key (others may
# also trip when the sentence names two signs; only the named one is asserted).
TRIPS = {
    "dizziness": [
        "I suddenly got really dizzy and had to sit down.",
        "I've been so dizzy since this morning I can barely stand.",
        "Out of nowhere the room is spinning and I can't stand up.",
        "i fainted in the kitchen an hour ago",
    ],
    "stroke": [
        "I feel dizzy and my left arm is weak.",
        "My husband is slurring his words.",
        "My mom's face is drooping on one side since lunch.",
        "Weakness in my legs and I'm dizzy.",
    ],
    "fever": [
        "My baby has a high fever.",
        "I've had a fever for 4 days now.",
        "My temperature is 103 tonight.",
        "My fever won't go away.",
        "my son has had a temp of 39.5 since yesterday",
    ],
    "swelling": [
        "My lips are swelling after the drops.",
        "My tongue feels swollen and itchy.",
        "My throat is closing up!",
        "I woke up with a swollen lip and tongue today.",
    ],
    "hives-breathing": [
        "I broke out in hives after taking it.",
        "I'm having trouble breathing since this morning.",
        "I'm short of breath.",
        "My daughter is wheezing and has welts on her arms.",
        "I can't breathe properly tonight.",
    ],
    "jaundice": [
        "My eyes look yellow today.",
        "My skin has turned yellow.",
        "I think I'm jaundiced.",
        "The whites of my eyes have gone yellowish.",
    ],
    "right-rib-pain": [
        "I have severe pain under my right ribs.",
        "Sharp pain on my right side under the ribs since last night.",
        "My right rib area hurts so much I can't sleep.",
        "I think I'm having a gallbladder attack.",
    ],
    "healing-crisis": [
        "I think I'm having a healing crisis.",
        "Is it possible I'm having a healing crisis?",
        "I might be having a herx reaction.",
        "Since starting the cleanse I've had a strong healing reaction.",
        "If I have hives, what should I do? I'm in a healing crisis right now.",
    ],
}


@pytest.mark.parametrize("key,phrase", [(k, p) for k, ps in TRIPS.items() for p in ps])
def test_each_warning_sign_trips_the_backstop(key, phrase):
    assert key in pt.warning_signs(phrase), phrase


# Routine messages and education questions: none of these may trip it.
ROUTINE = [
    "Does this remedy help with fever?",
    "What is a healing crisis?",
    "Can this cause hives?",
    "What if I get hives?",
    "I don't have a fever anymore, thanks.",
    "When should I reorder my Terrain Restore?",
    "How many capsules do I take in the morning?",
    "Will this help my fever?",
    "Is dizziness a side effect of magnesium?",
    "Thanks, I feel much better today.",
    "My sleep is better since starting.",
    "Can I take it with food?",
    "I have a sore throat.",
    "No hives, no swelling, all good.",
    "What should I eat for my liver?",
    "Could I have a healing crisis on the cleanse?",
    "My right knee hurts.",
    "I had a mild fever last year.",
    "Where is my order? It was supposed to arrive yesterday.",
    "My portal link isn't working.",
    "Can I book a consultation with Dr. Glen?",
    "Is the liver cleanse safe to do with a cold?",
    "I feel a little tired in the afternoons.",
    "What does yellow mean in my scan colours?",
    "Never had hives, just curious about the allergy remedy.",
    "I don't have hives anymore, thanks.",
    "My lips are not swollen any more.",
    "I haven't had a healing crisis this time.",
    "My eyes aren't yellow now.",
    "I'm not short of breath since the change.",
]


@pytest.mark.parametrize("phrase", ROUTINE)
def test_routine_messages_do_not_trip(phrase):
    assert pt.warning_signs(phrase) == [], phrase


def test_at_least_fifteen_routine_messages():
    assert len(ROUTINE) >= 15


def test_backstop_reads_only_the_client_message():
    """A warning word in the AI answer must not trip it; triage runs on query."""
    res = pt.triage(lambda s, u: json.dumps({"needs_attention": False}),
                    "When should I reorder?", answer="If you ever get hives, seek care.")
    assert res is None


# --- triage(): the backstop wins over the model -----------------------------

SIGN_MSG = "My lips are swelling after the drops."


def _says(payload):
    return lambda system, user: payload


@pytest.mark.parametrize("model_out", [
    json.dumps({"needs_attention": False}),
    "not json at all",
    "",
    json.dumps({"needs_attention": True, "category": "question", "urgency": "low",
                "summary": "asks about drops", "recommendation": "none"}),
])
def test_backstop_forces_high_health_concern(model_out):
    res = pt.triage(_says(model_out), SIGN_MSG)
    assert res["needs_attention"] is True
    assert res["urgency"] == "high"
    assert res["category"] == "health-concern"
    assert res["warning_signs"] == ["swelling"]
    assert "swelling of the lips, tongue or throat" in res["summary"]


def test_backstop_survives_a_model_error():
    def boom(system, user):
        raise RuntimeError("provider down")
    res = pt.triage(boom, SIGN_MSG)
    assert res["urgency"] == "high" and res["category"] == "health-concern"
    assert res["recommendation"] == pt.WARNING_RECOMMENDATION


def test_model_error_without_a_sign_flags_nothing():
    def boom(system, user):
        raise RuntimeError("provider down")
    assert pt.triage(boom, "When should I reorder?") is None


def test_backstop_keeps_the_model_summary():
    res = pt.triage(_says(json.dumps({"needs_attention": True, "category": "other",
                                      "urgency": "medium", "summary": "lip swelling",
                                      "recommendation": "call her"})), SIGN_MSG)
    assert res["summary"].endswith("lip swelling")
    assert res["recommendation"] == "call her"


def test_no_sign_passes_model_result_through():
    res = pt.triage(_says(json.dumps({"needs_attention": True, "category": "request",
                                      "urgency": "medium", "summary": "wants consult",
                                      "recommendation": "book"})),
                    "Can I book a consultation?")
    assert res["urgency"] == "medium" and "warning_signs" not in res


# --- the prompt names each sign and frees it from the "when unsure" rule ------

PROMPT_SIGNS = [
    "sudden or severe new dizziness",
    "dizziness with weakness or slurred speech",
    "a high or lasting fever",
    "swelling of the lips, tongue or throat",
    "hives or trouble breathing",
    "yellow skin or eyes",
    "severe pain under the right ribs",
    "any healing crisis",
]


@pytest.mark.parametrize("sign", PROMPT_SIGNS)
def test_triage_prompt_lists_each_sign(sign):
    assert sign in pt._TRIAGE_SYSTEM


def test_triage_prompt_exempts_signs_from_when_unsure():
    p = pt._TRIAGE_SYSTEM
    assert "never holds back a warning sign" in p
    assert 'urgency \\"high\\"' in json.dumps(p) or 'urgency "high"' in p
    assert p.index("WARNING SIGNS") < p.index("When unsure")
