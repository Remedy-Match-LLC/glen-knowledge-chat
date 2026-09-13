#!/usr/bin/env python3
"""Carry a prepared mailing list into a Render one-off job without the addresses.

The weekly live invitation must mail only the addresses filter_audience.py
returns (it reads both consent stores; the sender reads neither fully).  A
one-off job cannot read a file on the Mac, and a start command is kept in
Render's job record, so the list travels as keyed fingerprints instead:

    8-byte HMAC-SHA256 of each normalised address, keyed with CONSOLE_SECRET

Without that secret a fingerprint cannot be matched to an address.  The
argument carries its own count, so a truncated command refuses rather than
mailing a shorter list.

Build the argument on the Mac, never printing an address:

    doppler run -p remedy-match -c prd -- \
        python3 scripts/live_invitation_allowlist.py mailable.txt

Stdlib only, so it runs without importing app.
"""

import base64
import hashlib
import hmac
import os
import re
import sys

VERSION = "v1"
DIGEST_BYTES = 8
KEY_ENV = "CONSOLE_SECRET"


def normalize_email(value):
    """Same rule as weekly_live_invitation._email; a test pins the two together."""
    value = (value or "").strip().lower()
    return value if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) else ""


def _key(key=None):
    key = key if key is not None else os.environ.get(KEY_ENV, "")
    if not key:
        # An absent key must refuse, never produce fingerprints that match nothing
        # or everything.
        raise RuntimeError(f"{KEY_ENV} is missing; cannot fingerprint the list")
    return key.encode()


def fingerprint(email, key=None):
    email = normalize_email(email)
    if not email:
        raise ValueError("not an email address")
    return hmac.new(_key(key), email.encode(), hashlib.sha256).digest()[:DIGEST_BYTES]


def encode(emails, key=None):
    """Return (argument, stats). Invalid lines are counted, never included."""
    digests, invalid = set(), 0
    seen = set()
    for raw in emails:
        if not (raw or "").strip():
            continue
        email = normalize_email(raw)
        if not email:
            invalid += 1
            continue
        seen.add(email)
        digests.add(fingerprint(email, key))
    blob = base64.urlsafe_b64encode(b"".join(sorted(digests))).decode()
    stats = {"unique_addresses": len(seen), "invalid_lines": invalid,
             "fingerprints": len(digests)}
    return f"{VERSION}.{len(digests)}.{blob}", stats


def decode(argument):
    """Return the set of fingerprints, refusing anything malformed or truncated."""
    try:
        version, count, blob = (argument or "").strip().split(".", 2)
        count = int(count)
        raw = base64.urlsafe_b64decode(blob.encode())
    except Exception as exc:
        raise RuntimeError("allowlist argument is malformed") from exc
    if version != VERSION:
        raise RuntimeError(f"allowlist version {version!r} is not {VERSION!r}")
    if len(raw) % DIGEST_BYTES or len(raw) // DIGEST_BYTES != count:
        raise RuntimeError("allowlist count does not match its contents; "
                           "the argument may be truncated")
    if count == 0:
        raise RuntimeError("allowlist is empty")
    return {raw[i:i + DIGEST_BYTES] for i in range(0, len(raw), DIGEST_BYTES)}


def allows(fingerprints, email, key=None):
    email = normalize_email(email)
    return bool(email) and fingerprint(email, key) in fingerprints


def main(argv):
    if len(argv) != 2:
        print("usage: live_invitation_allowlist.py <mailable.txt>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as handle:
        argument, stats = encode(handle)
    print(f"unique addresses {stats['unique_addresses']}, "
          f"invalid lines {stats['invalid_lines']}, "
          f"argument length {len(argument)}", file=sys.stderr)
    print(argument)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
