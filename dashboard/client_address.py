"""The caller's real address, from a forwarding chain the caller can partly write.

WHY THIS EXISTS. Three auth routes derived it as:

    (request.headers.get("X-Forwarded-For", "") or request.remote_addr or "").split(",")[0]

**The first element is whatever the CLIENT sent.** Measured against production on
2026-09-11: a request carrying `X-Forwarded-For: 203.0.113.77` was recorded by
`portal_auth._record_event` with `ip_hash` equal to sha256 of that value. So the address in
the login audit trail is fiction whenever someone wants it to be, and an attacker can
attribute their own failed logins to a real customer's address.

THE ACTUAL CHAIN, read from the raw header on 2026-09-12 rather than inferred:

    no forged header    66.8.150.120, 172.68.129.174
    forged header       203.0.113.55,66.8.150.120, 172.68.129.174

So the shape is:

    [ anything the client wrote ] , <the client's real address> , <Cloudflare edge>

**Two trusted hops append, not one.** Cloudflare appends the address it saw the client
connect from, then Render's balancer appends Cloudflare's own edge address. The real client
is therefore SECOND FROM THE RIGHT.

An earlier attempt read the rightmost element, which is the Cloudflare edge. That address
varies by which Cloudflare location served the request, so it is neither the client nor
stable, and a rate limiter keyed on it did not bind. See
[[feedback_i_used_half_a_measurement_to_confirm_what_i_expected]].

ON `CF-Connecting-IP`. Cloudflare sets it to the client address and strips any the client
sends, which would make it simpler and stronger than counting hops. It is NOT relied on
here, because this file's whole history is a lesson about acting on the unverified half of
a measurement. It is read and reported alongside, so we can see whether it agrees before
anything depends on it.
"""
import ipaddress

# Measured 2026-09-12. Cloudflare appends the client, Render appends Cloudflare.
# If a proxy is ever added or removed in front, this number changes with it, and getting
# it TOO HIGH is the dangerous direction: it reaches back into client-written text.
TRUSTED_PROXY_HOPS = 2


def _normalise(raw):
    if not raw:
        return ""
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return raw[:64]          # client-written junk, keyed verbatim rather than dropped
    if ip.version == 6:
        # One IPv6 allocation is enormous, so a per-address key is meaningless.
        return f"{ipaddress.ip_network(f'{raw}/64', strict=False).network_address}/64"
    return raw


def client_address(xff, remote_addr, cf_connecting_ip=""):
    """(address, source, cf_agrees) for the caller.

    `source` is "xff" or "remote_addr", so which path ran is visible in a log rather than
    assumed. `cf_agrees` is True when Cloudflare's own header matches what the hop count
    produced, None when that header is absent, False when they disagree. A False is worth
    investigating before trusting either.
    """
    parts = [p.strip() for p in (xff or "").split(",") if p.strip()]
    idx = len(parts) - TRUSTED_PROXY_HOPS if parts else -1
    if parts and 0 <= idx < len(parts):
        raw, source = parts[idx], "xff"
    elif parts:
        # A chain SHORTER than the expected hops means the request did not arrive the way
        # this file expects: a proxy was removed, or something reached the app directly.
        # Every element is then potentially client-written, so take the socket peer, which
        # is the one thing a caller cannot choose. The source says this happened.
        raw, source = (remote_addr or "").strip(), "remote_addr_short_chain"
    else:
        raw, source = (remote_addr or "").strip(), "remote_addr"

    # The hop count is what makes this safe, and it is self-checking: a client that writes
    # anything into the header makes the chain LONGER, which moves the index with it. A
    # two-element chain therefore means the client wrote nothing, and element 0 is genuine.

    addr = _normalise(raw) or "anon"
    cf = (cf_connecting_ip or "").strip()
    agrees = None if not cf else (_normalise(cf) == addr)
    return addr, source, agrees
