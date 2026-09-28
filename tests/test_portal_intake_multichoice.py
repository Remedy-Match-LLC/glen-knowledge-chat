"""The portal intake must render every field type the form declares.

2026-09-27: "Common whole-body symptoms" (multi_choice) had no branch in the portal's
renderIntakeField, so it fell through to a text box. The text, even empty, was then refused by
the server's list check, and no portal client could submit the intake. The public intake page
(begin-intake.html) was unaffected."""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

from dashboard import intake

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = ROOT / "static" / "client-portal.html"
GENERIC_INPUT_TYPES = {"text", "email", "tel", "date", "number"}


def _renderer():
    page = PAGE.read_text()
    at = page.find("\nfunction renderIntakeField(")
    assert at != -1
    end = page.find("\n}\n", at)
    return page[at:end + 3]


def _fields():
    for sec in intake.INTAKE_FORM["sections"]:
        for f in sec.get("fields", []):
            yield f


def test_every_declared_field_type_has_a_portal_branch():
    src = _renderer()
    missing = sorted({f["type"] for f in _fields()
                      if f["type"] not in GENERIC_INPUT_TYPES
                      and f'ftype === "{f["type"]}"' not in src})
    assert missing == []


def test_an_empty_symptom_answer_never_blocks_submit():
    assert "systemic_symptoms" not in intake.validate_response({"systemic_symptoms": ""})
    assert "systemic_symptoms" not in intake.validate_response({"systemic_symptoms": None})
    assert "systemic_symptoms" not in intake.validate_response({"systemic_symptoms": []})
    assert "systemic_symptoms" not in intake.validate_response(
        {"systemic_symptoms": ["symptom-fatigue"]})
    assert "systemic_symptoms" in intake.validate_response({"systemic_symptoms": ["not-an-option"]})
    # Text from the old portal text box never blocks: it is moved, not refused (round 2).
    assert "systemic_symptoms" not in intake.validate_response({"systemic_symptoms": "tired"})


def test_typed_symptom_text_moves_to_other_symptoms_and_is_stored_as_a_list(tmp_path):
    """A draft saved from the old text box keeps the client's words (review round 2)."""
    import sqlite3
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    cx.row_factory = sqlite3.Row
    intake.init_intake_table(cx)
    intake.save_draft(cx, "a@x.com", {"systemic_symptoms": "tired all the time",
                                      "other_symptoms": "knee pain"}, "t")
    got = intake.get_response(cx, "a@x.com")["answers"]
    assert got["systemic_symptoms"] == []
    assert got["other_symptoms"] == "knee pain\ntired all the time"
    stored = json.loads(cx.execute("SELECT answers_json FROM intake_responses").fetchone()[0])
    assert stored["systemic_symptoms"] == [] and "tired all the time" in stored["other_symptoms"]
    intake.save_draft(cx, "b@x.com", {"systemic_symptoms": ""}, "t")
    assert intake.get_response(cx, "b@x.com")["answers"]["systemic_symptoms"] == []


def test_normalising_twice_does_not_repeat_the_text():
    once = intake.normalize_answers({"systemic_symptoms": "tired"})
    again = intake.normalize_answers(dict(once, systemic_symptoms="tired"))
    assert again["other_symptoms"] == "tired"


HARNESS = r"""
const assert = require('assert');
function el(tag){
  const e = {tagName: tag.toUpperCase(), children: [], listeners: {}, className: '', textContent: '',
    type: '', value: '', checked: false, dataset: {},
    appendChild(c){ this.children.push(c); return c; },
    addEventListener(t, f){ (this.listeners[t] = this.listeners[t] || []).push(f); },
    querySelectorAll(sel){
      const out = []; (function walk(n){ n.children.forEach(c => { out.push(c); walk(c); }); })(this);
      if (sel === 'input:checked') return out.filter(n => n.tagName === 'INPUT' && n.checked);
      if (sel === 'input') return out.filter(n => n.tagName === 'INPUT');
      return [];
    },
  };
  return e;
}
global.document = {createElement: el};
function inputs(n){ const out = []; (function walk(x){ x.children.forEach(c => { if (c.tagName === 'INPUT') out.push(c); walk(c); }); })(n); return out; }

RENDERER

const field = FIELD;
let changed = 0;
// 1. saved choices come back checked, and the getter returns option values
let getters = {}, els = {};
let w = renderIntakeField(field, ["symptom-fatigue"], getters, els, () => changed++, {}, null);
let boxes = inputs(w).filter(b => b.type === 'checkbox');
assert.strictEqual(boxes.length, field.options.length);
assert.strictEqual(boxes.find(b => b.value === 'symptom-fatigue').checked, true);
assert.deepStrictEqual(getters[field.id](), ["symptom-fatigue"]);
boxes[1].checked = true; boxes[1].listeners.change.forEach(f => f());
assert.deepStrictEqual(getters[field.id]().sort(), ["symptom-fatigue", boxes[1].value].sort());
assert.strictEqual(changed, 1);
assert.strictEqual(inputs(w).filter(b => b.type !== 'checkbox').length, 0);   // no text box
// 2. a draft saved as free text (the old text box) shows nothing checked and sends a list
getters = {}; els = {};
w = renderIntakeField(field, "tired all the time", getters, els, () => {}, {}, null);
assert.deepStrictEqual(getters[field.id](), []);
console.log('OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_portal_renders_symptoms_as_checkboxes_and_sends_a_list(tmp_path):
    field = next(f for f in _fields() if f["id"] == "systemic_symptoms")
    js = tmp_path / "h.js"
    js.write_text(HARNESS.replace("RENDERER", _renderer()).replace("FIELD", json.dumps(field)))
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr + out.stdout



@pytest.mark.parametrize("stored,want", [
    ("['symptom-fatigue', 'symptom-sleep']", ["symptom-fatigue", "symptom-sleep"]),  # PB import
    ("symptom-fatigue", ["symptom-fatigue"]),                    # health profile single select
    ("Fatigue or low energy", ["symptom-fatigue"]),              # an option's label
    ("symptom-fatigue, symptom-sleep", ["symptom-fatigue", "symptom-sleep"]),
])
def test_text_that_is_really_options_becomes_the_list(stored, want):
    """Older paths stored the list as text (round 1): turn it back, don't move it to notes."""
    got = intake.normalize_answers({"systemic_symptoms": stored})
    assert got["systemic_symptoms"] == want and not got.get("other_symptoms")


def test_an_old_draft_already_stored_as_text_loads_with_the_text_moved(tmp_path):
    """Five production drafts hold typed text today; they were written before this fix."""
    import sqlite3
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    cx.row_factory = sqlite3.Row
    intake.init_intake_table(cx)
    cx.execute("INSERT INTO intake_responses (email, form_version, status, answers_json, created_at) "
               "VALUES ('c@x.com', 'v', 'draft', ?, 't')",
               (json.dumps({"systemic_symptoms": "headaches most days"}),))
    cx.commit()
    got = intake.get_response(cx, "c@x.com")["answers"]
    assert got == {"systemic_symptoms": [], "other_symptoms": "headaches most days"}


def test_seeding_uses_the_normalized_answer(monkeypatch, tmp_path):
    """Round 3: a stale tab submitting "Trouble sleeping" was stored as the list but seeded
    nothing, because seeding read the raw text."""
    import sqlite3
    import app as appmod
    from dashboard import condition_triage as ct
    seeded = []
    monkeypatch.setattr(ct, "seed_from_triage", lambda cx, email, key, extra: seeded.append(key))
    monkeypatch.setattr(appmod, "_init_support_programs_tables", lambda cx: None)
    appmod._seed_intake_systemic_symptoms(sqlite3.connect(str(tmp_path / "t.db")), "a@x.com",
                                          {"systemic_symptoms": "Trouble sleeping"})
    assert seeded == ["symptom-sleep"]


@pytest.mark.parametrize("empty", ["[]", "''", '""', "[ ]"])
def test_an_empty_list_stored_as_text_is_no_answer(empty):
    got = intake.normalize_answers({"systemic_symptoms": empty})
    assert got["systemic_symptoms"] == [] and not got.get("other_symptoms")
