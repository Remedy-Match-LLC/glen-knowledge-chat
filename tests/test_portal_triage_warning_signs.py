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


# Any fever in a baby or a child under about 2, no "high" or "lasting" needed.
TRIPS["fever"] += [
    "My baby has a fever.",
    "My infant feels warm and has a temperature tonight.",
    "Our newborn has a slight fever.",
    "My toddler woke up with a fever.",
    "My little one has a fever.",
    "My 18-month-old has a low fever.",
    "My one-year-old is running a temp.",
    "Fever in my 6 month old since lunch.",
]
# Swelling and breathing need no first-person or time word.
TRIPS["swelling"] += [
    "Swollen lips and tongue!",
    "Throat closing up.",
    "Lips swelling, help",
]
TRIPS["hives-breathing"] += [
    "Trouble breathing after the drops.",
    "Can't breathe properly.",
    "Short of breath and wheezing.",
]

# Review rounds 1 and 2 (20 Oct 2026): every phrase that must flag.
TRIPS["swelling"] += [
    "Not sure if this is related but my lips are swollen and I have hives",
    "Wondering if I should stop the drops because my throat is swelling",
    "If it helps, my lips and tongue are swollen",
    "my throat feels tight",
    "my throat is tight",
    "my tongue feels thick",
    "my lips are swolen",
    "Can you explain why my lips are swelling?",
    "I don't know why my lips are swollen",
    "my tounge is swelling",
]
TRIPS["hives-breathing"] += [
    "Not sure if this is related but my lips are swollen and I have hives",
    "I don't know if this matters but I'm short of breath",
    "No, I'm short of breath",
    "Should I keep taking the drops if I have hives right now?",
    "I had no hives earlier, but now I have hives",
    "I'm having a hard time breathing",
    "I can barely breathe",
    "I can hardly breathe",
    "my breathing is really hard",
    "can't catch my breath",
    "i cant breth",
    "hives! help",
    "i have hivs all over",
    "i broke out in a rash and itchy bumps everywhere",
]
TRIPS["fever"] += [
    "I have a 102.5 fever",
    "I have a 39.5 fever",
    "my temp is 101.5 since yesterday",
    "I've had a fever 4 days now",
    "i have a feaver of 103",
    "the baby has a 101 fever",
    "baby has a fever",
    "the baby has a fever",
]
TRIPS["right-rib-pain"] += [
    "I have pain in my upper right abdomen",
    "my upper right side hurts bad",
    "pain under my right ribs",
]
TRIPS["dizziness"] += [
    "if this is a herx why am I so dizzy since this morning",
    "im realy dizy",
    "Not sure why I'm so dizzy since this morning",
    "I feel faint and lightheaded",
    "im so dizzzy",
    "my mom is really dizzy and cant stand up",
    "dizzy af since this morning",
]
TRIPS["stroke"] += ["I'm numb on one side and dizzy"]
TRIPS["jaundice"] += [
    "my eyes look yellow",
    "my skin is yellow",
    "I have yellow skin",
    "I think I have jaundise",
]

# Glen, via the coordinator, 20 Oct 2026: in this chat a bare fragment is a
# report. No warning sign needs a report word ("I", "my", a time word).
TRIPS["jaundice"] += ["eyes look yellow"]
TRIPS["fever"] += ["102.5 fever", "fever 40C", "high fever"]
TRIPS["dizziness"] += ["room is spinning", "fainted", "the world keeps spinning"]
TRIPS["hives-breathing"] += ["got hives all over"]
TRIPS["right-rib-pain"] += ["sharp pain under right rib cage"]


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
    # Baby fever: negation and hypothetical rules still apply.
    "My baby doesn't have a fever anymore.",
    "If my baby gets a fever, what should I do?",
    "What if my toddler gets a fever?",
    "Can a baby have a fever from teething?",
    "My baby is teething and fussy.",
    # Swelling/breathing without a marker: negation and hypothetical rules apply.
    "Can this cause swollen lips?",
    "What if my throat swells?",
    "No trouble breathing today.",
    "Lips not swollen anymore.",
    "Could this cause trouble breathing?",
    "Hives can come from histamine.",
    # Review rounds 1 and 2: false alarms that must not flag.
    "how do I take the drops for hives?",
    "I want to order more of the hives formula",
    "I had hives as a kid",
    "my hives are better",
    "my hives are gone now, thank you",
    "my hives cleared up",
    "I no longer get hives",
    "I can't breathe through my nose, stuffy",
    "the yellow capsules make my skin itch",
    "yellow capsules make my skin itch",
    "My mom had jaundice years ago",
    "I'm reading your article on jaundice in babies",
    "I was so dizzy last year before I found you",
    "Can I use this for my baby's fever?",
    "My baby is 6 months old, can she take the fever drops?",
    "I take the wheeze formula daily",
    "my lips are chapped and puffy from the cold",
    "I've been reading about healing crisis",
    "What causes swollen lips?",
    "Can you explain hives to me?",
    "I don't currently have any hives",
    "I have mild pain under my right ribs",
    "I have slight pain under my right ribs",
    "I had a fever as a child",
    "Tell me how hives work.",
    "Does this help with hives?",
    "Can the drops help my breathing?",
    "Remind me which one is for hives.",
    # Fragments with no report word: the exclusions still apply.
    "no fever",
    "fever drops",
    "high fever as a child",
    "high fever years ago",
    "high fever is gone now",
    "what causes a high fever",
    "Hives are caused by histamine.",
    "Will these drops help hives?",
    "which remedy for hives",
    "the room is not spinning",
    "How long does a healing crisis last?",
    "high fever in kids",
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
    # The model's recommendation never survives a forced flag (review round 2).
    assert res["recommendation"] == pt.WARNING_RECOMMENDATION


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


def test_contradicting_model_recommendation_is_replaced():
    res = pt.triage(_says(json.dumps({"needs_attention": True, "category": "question",
                                      "urgency": "low", "summary": "s",
                                      "recommendation": "wait until your next appointment"})),
                    SIGN_MSG)
    assert res["recommendation"] == pt.WARNING_RECOMMENDATION


def test_add_warning_item_dedupes_within_ten_minutes_only():
    import sqlite3
    from datetime import datetime, timedelta, timezone
    cx = sqlite3.connect(":memory:")
    t0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    a, made = pt.add_warning_item(cx, "A@x.com", "A", ["swelling"], "My lips are swelling!", now=t0)
    assert made
    b, made2 = pt.add_warning_item(cx, "a@x.com", "A", ["swelling"], "my lips are  SWELLING",
                                   now=t0 + timedelta(minutes=9))
    assert (b, made2) == (a, False)
    c, made3 = pt.add_warning_item(cx, "a@x.com", "A", ["swelling"], "My lips are swelling!",
                                   now=t0 + timedelta(minutes=11))
    assert made3 and c != a
    d, made4 = pt.add_warning_item(cx, "other@x.com", "O", ["swelling"], "My lips are swelling!",
                                   now=t0 + timedelta(minutes=1))
    assert made4


def test_claim_resend_is_exclusive():
    import sqlite3
    cx = sqlite3.connect(":memory:")
    item, _ = pt.add_warning_item(cx, "a@x.com", "A", ["swelling"], "My lips are swelling!")
    pt.record_email(cx, item, "console-log", now="2026-10-20T00:00:00Z")
    assert pt.claim_resend(cx, item, "2026-10-20T00:00:00Z", now="2026-10-20T01:00:00Z")
    assert not pt.claim_resend(cx, item, "2026-10-20T00:00:00Z", now="2026-10-20T01:00:01Z")
