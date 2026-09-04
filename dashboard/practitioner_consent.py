"""What a client agreed to when they let their practitioner see their data.

The consent was a bare 0/1. On 2026-09-03 Glen widened the wording from
"wellness results" to "wellness results and activity including purchases", and a
bare flag cannot tell the two cohorts apart: everyone who ticked the old box
agreed to results, not to their purchase history.

Glen's ruling: existing consenters keep the OLD scope until they re-affirm. That
only works if the version is recorded at the moment of consent, so a row written
before versioning reads as the narrowest scope rather than the newest.

Fails closed. An unrecognised version grants nothing beyond results, because an
unknown value is not a licence to share more, and this decides what one person
gets to see about another.
"""

# Ordered narrowest first. A later version must never describe LESS than an
# earlier one, or re-affirming would quietly shrink what a client agreed to.
VERSION_ORDER = ("legacy", "2026-09-results-activity-purchases")

SCOPES = {
    "legacy": ("results",),
    "2026-09-results-activity-purchases": ("results", "activity", "purchases"),
}

TEXTS = {
    "legacy":
        "I authorize sharing my wellness results with my enrolling practitioner.",
    "2026-09-results-activity-purchases":
        "I authorize sharing my wellness results and activity, including "
        "purchases, with my practitioner.",
}

CURRENT_VERSION = VERSION_ORDER[-1]
CURRENT_TEXT = TEXTS[CURRENT_VERSION]
LEGACY_VERSION = VERSION_ORDER[0]


def _normalize(version):
    """A stored version string, or LEGACY for anything blank or unrecognised."""
    if not isinstance(version, str):
        return LEGACY_VERSION
    version = version.strip()
    return version if version in SCOPES else LEGACY_VERSION


def scopes_for(version):
    """What this consent covers, as a tuple."""
    return SCOPES[_normalize(version)]


def text_for(version):
    """The wording THIS client agreed to.

    Never the current text for an older consent: a client must be shown what they
    actually agreed to, not what someone else agreed to later.
    """
    return TEXTS[_normalize(version)]


def covers(version, scope):
    return scope in scopes_for(version)


def covers_results(version):
    """True for every version, including unversioned rows.

    The continuity roster is gated on this, so widening the wording must not
    invalidate consent that already works.
    """
    return covers(version, "results")


def covers_purchases(version):
    """True only where the client agreed to wording that names purchases.

    Any read that would show a practitioner what a client BOUGHT must ask this,
    not the bare 0/1 flag.
    """
    return covers(version, "purchases")
