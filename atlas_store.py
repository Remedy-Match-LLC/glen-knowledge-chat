"""Knowledge Atlas store: load/validate/approve concept data. No Flask/Pinecone deps."""
import json
import os
from pathlib import Path

# The git-committed copies (shipped with every deploy) — the build pipeline writes here.
REPO_DATA = Path(__file__).resolve().parent / "data"


def _persist_dir():
    """The mutable concept/pending files live on the persistent disk (DATA_DIR=/data on
    Render) so admin approvals survive redeploys. Falls back to the repo dir locally / in
    tests where no persistent disk exists."""
    d = Path(os.environ.get("DATA_DIR") or "/data")
    if d.is_dir() and os.access(d, os.W_OK):
        return d
    return REPO_DATA


DATA_DIR = _persist_dir()
CONCEPTS_PATH = DATA_DIR / "atlas-concepts.json"      # mutable (approve/reject) -> persistent
PENDING_PATH = DATA_DIR / "atlas-pending.json"        # mutable -> persistent
VIDEOS_PATH = REPO_DATA / "atlas-videos.json"         # read-only link catalog -> ship with repo


def reseed_from_repo(force=False):
    """Copy the git-committed atlas files onto the persistent dir. Seeds them on first boot
    (force=False, won't clobber live curation); force=True republishes a fresh build."""
    if DATA_DIR == REPO_DATA:
        return False
    seeded = False
    for fname in ("atlas-concepts.json", "atlas-pending.json"):
        dst, src = DATA_DIR / fname, REPO_DATA / fname
        if src.exists() and (force or not dst.exists()):
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            seeded = True
    return seeded

_REQUIRED = ("id", "label", "summary", "cluster", "coords", "links", "status")


def validate_concept(c):
    """Return (ok, error_message_or_None)."""
    if not isinstance(c, dict):
        return False, "concept must be an object"
    for key in _REQUIRED:
        if key not in c:
            return False, f"missing required field: {key}"
        # Present-but-empty is not satisfied. Checking only for the KEY let a patch of
        # {"summary": None} through `upsert_concept`, writing a concept whose summary
        # and cluster were null while every required key was technically there.
        # Measured before tightening: 0 of the 732 live concepts carry an empty
        # required field, so nothing already published stops validating because of this.
        v = c[key]
        if v is None or (isinstance(v, str) and not v.strip()):
            return False, f"required field is empty: {key}"
    coords = c.get("coords") or {}
    for axis in ("x", "y"):
        v = coords.get(axis)
        if not isinstance(v, (int, float)) or not (0.0 <= float(v) <= 1.0):
            return False, f"coords.{axis} must be a number in [0,1]"
    if not isinstance(c.get("links"), list):
        return False, "links must be a list"
    return True, None


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_concepts():
    return _read(CONCEPTS_PATH, {"version": "", "concepts": []})


def load_pending():
    return _read(PENDING_PATH, {"version": "", "concepts": []})


def load_videos():
    return _read(VIDEOS_PATH, {"version": "", "videos": []}).get("videos", [])


def build_graph():
    """Public payload for /atlas/data: validated live concepts + hierarchy index."""
    concepts = [c for c in load_concepts().get("concepts", [])
                if c.get("status") == "live" and validate_concept(c)[0]]
    hierarchy = {}
    for c in concepts:
        hierarchy.setdefault(c.get("parent") or "ungrouped", []).append(c["id"])
    return {"concepts": concepts, "hierarchy": hierarchy}


def upsert_concept(concept=None, *, concept_id=None, patch=None):
    """Write one concept to the live graph. Returns (id, "created"|"updated").

    Two shapes. `concept=` replaces a whole record. `concept_id=` with `patch=` merges
    the given fields into the existing one, which is what a single correction usually
    needs: fetch nothing, send `{"summary": "..."}`.

    WHY THIS EXISTS. Until 2026-09-11 the only way to change one published fact was
    `/admin/atlas/reseed`, which republishes the entire committed build over the disk.
    A wrong price on one concept therefore had no safe fix at all: the repo build was
    76 concepts behind live, so a reseed would have deleted Accelerated Self Healing,
    Biofield Analysis, Biological Dentistry and 73 more.

    WHAT IT DOES NOT DO, and this matters. It writes to the PERSISTENT DISK only.
    Nothing carries the change back to git, exactly like `approve_concept`. Every call
    widens the gap between the disk and the committed build, and a wide enough gap is
    what made reseed dangerous in the first place. The mitigation is the scheduled
    drift check (`00 System/scripts/atlas-rebuild-from-live.py --check`) in the vault.
    If that job is not running, this function is quietly building the same trap again.

    A rejected concept is never written: validation runs on the MERGED result, so a
    patch cannot strip a required field or push coords out of range.
    """
    if (concept is None) == (concept_id is None):
        raise ValueError("pass either concept= or concept_id= with patch=")

    live = load_concepts()
    existing = {c.get("id"): c for c in live.get("concepts", [])}

    if concept is not None:
        cid = concept.get("id")
        if not cid:
            raise ValueError("concept needs an id")
        merged = dict(concept)
    else:
        cid = concept_id
        if cid not in existing:
            raise KeyError(cid)
        merged = {**existing[cid], **(patch or {})}
        merged["id"] = cid          # a patch must never rename the record it edits

    ok, err = validate_concept(merged)
    if not ok:
        raise ValueError(err)

    action = "updated" if cid in existing else "created"
    live["concepts"] = [c for c in live.get("concepts", []) if c.get("id") != cid]
    live["concepts"].append(merged)
    _write(CONCEPTS_PATH, live)
    return cid, action


def approve_concept(concept_id):
    pending = load_pending()
    live = load_concepts()
    keep, moved = [], None
    for c in pending.get("concepts", []):
        if c.get("id") == concept_id:
            moved = {**c, "status": "live"}
        else:
            keep.append(c)
    if moved is None:
        raise KeyError(concept_id)
    live_concepts = [c for c in live.get("concepts", []) if c.get("id") != concept_id]
    live_concepts.append(moved)
    live["concepts"] = live_concepts
    pending["concepts"] = keep
    _write(CONCEPTS_PATH, live)
    _write(PENDING_PATH, pending)


def reject_concept(concept_id):
    pending = load_pending()
    pending["concepts"] = [c for c in pending.get("concepts", []) if c.get("id") != concept_id]
    _write(PENDING_PATH, pending)
