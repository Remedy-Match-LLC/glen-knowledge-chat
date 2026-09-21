"""A transcript must be written into the space the chat queries.

Knowledge classified all 81,151 vectors in `remedy-match-llc` on 2026-09-20, not a sample:
13,864 sit in the wrong space. 369 of those carry the exact signature of
`_ingest_to_pinecone` -- ids prefixed `consult`, `train` or `busine` (namespace[:6] of
"business") with metadata source "zoom-transcript".

The write used text-embedding-3-small while every query uses ada-002. A 3-small vector and
an ada-002 vector sit at cosine -0.003, where two UNRELATED ada vectors sit at 0.70 to 0.86.
So those chunks did not rank low against a question. They ranked at random, which is why
every transcript ingested through /ingest-transcript has been effectively unfindable.

WHY IT SURVIVED: both models are 1536 dimensions. Pinecone accepted every upsert. A
dimension mismatch would have raised on the first write and been fixed the same day. Nothing
validates a vector's SPACE, only its size.

So the test that matters is not "is the string ada-002". It is "does the writer use the same
model as the reader", which is the property that was actually violated.
"""
import importlib
import sys
from pathlib import Path

import pytest


def _app():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("app")
    except Exception as e:
        pytest.skip(f"app not importable: {e}")


class _Recorder:
    """Stands in for the OpenAI client and records the model each call asks for."""

    def __init__(self, dims=1536):
        self.models = []
        self._dims = dims
        outer = self

        class _Embeddings:
            def create(self, *, input, model):
                outer.models.append(model)
                n = len(input) if isinstance(input, list) else 1
                return type("R", (), {"data": [type("E", (), {"embedding": [0.0] * outer._dims})()
                                               for _ in range(n)]})()

        self.embeddings = _Embeddings()


class _Index:
    def __init__(self):
        self.upserts = []

    def upsert(self, *, vectors, namespace):
        self.upserts.append((namespace, vectors))


@pytest.fixture
def env(monkeypatch):
    appmod = _app()
    rec, idx = _Recorder(), _Index()
    monkeypatch.setattr(appmod, "_oa", rec)
    monkeypatch.setattr(appmod, "_idx", idx)
    monkeypatch.setattr(appmod._time, "sleep", lambda *_a, **_k: None)
    return appmod, rec, idx


def _long_text(words=400):
    return " ".join(f"word{i}" for i in range(words))


def test_ingest_writes_in_the_space_the_app_queries(env):
    """The property that was violated: the writer and the reader must agree."""
    appmod, rec, _ = env
    appmod.embed("what does MSM do")
    query_model = rec.models[-1]
    rec.models.clear()

    appmod._ingest_to_pinecone(_long_text(), "A Zoom Call", "business")
    write_models = set(rec.models)

    assert write_models, "the ingest made no embedding call"
    assert write_models == {query_model}, (
        f"ingest writes in {write_models} but the app queries in {query_model!r}; "
        "chunks written this way rank at random against a question")


def test_ingest_uses_ada_002_by_name(env):
    """Stated separately so a future change of BOTH models is a deliberate act that has to
    edit this line, rather than something the agreement test would silently allow."""
    appmod, rec, _ = env
    appmod._ingest_to_pinecone(_long_text(), "A Zoom Call", "business")
    assert set(rec.models) == {"text-embedding-ada-002"}


def test_the_wrong_space_signature_is_what_knowledge_traced(env):
    """Pins the id prefix and source string knowledge matched on, so the 369 vectors stay
    attributable to this function if anyone has to find them again."""
    appmod, _, idx = env
    appmod._ingest_to_pinecone(_long_text(), "A Zoom Call", "business")
    assert idx.upserts, "nothing was upserted"
    namespace, vectors = idx.upserts[0]
    assert namespace == "business"
    assert vectors[0]["id"].startswith("busine-a-zoom-call-")
    assert vectors[0]["metadata"]["source"] == "zoom-transcript"
