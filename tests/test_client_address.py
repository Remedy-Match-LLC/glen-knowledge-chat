"""The caller's real address, against a chain the caller can partly write.

Every case below uses the REAL shape, read from production on 2026-09-12 rather than
inferred:

    no forged header    66.8.150.120, 172.68.129.174
    forged header       203.0.113.55,66.8.150.120, 172.68.129.174

    [ anything the client wrote ] , <the client's real address> , <Cloudflare edge>

An earlier attempt read the RIGHTMOST element. That is the Cloudflare edge, which varies by
which Cloudflare location served the request, so a rate limiter keyed on it did not bind:
35 distinct addresses produced 2 refusals and 61 produced 0.

Run:  python3 -m pytest tests/test_client_address.py -q
"""
import pytest

from dashboard.client_address import TRUSTED_PROXY_HOPS, client_address

CLIENT = "66.8.150.120"
CLOUDFLARE = "172.68.129.174"
FORGED = "203.0.113.55"


def test_the_real_production_chain_resolves_to_the_client():
    addr, source, _ = client_address(f"{CLIENT}, {CLOUDFLARE}", "")
    assert addr == CLIENT
    assert source == "xff"


def test_a_forged_hop_does_not_become_the_answer():
    """The exact header I sent on 2026-09-12, comma spacing included."""
    addr, _, _ = client_address(f"{FORGED},{CLIENT}, {CLOUDFLARE}", "")
    assert addr == CLIENT
    assert addr != FORGED


def test_a_long_forged_chain_cannot_push_the_client_out_of_reach():
    forged = ",".join(f"10.0.0.{i}" for i in range(60))
    addr, _, _ = client_address(f"{forged},{CLIENT}, {CLOUDFLARE}", "")
    assert addr == CLIENT


def test_the_rightmost_element_is_never_the_answer_when_a_proxy_appended():
    """The specific bug that made the rate limiter useless: the rightmost hop is the
    Cloudflare edge, which changes with the serving location."""
    addr, _, _ = client_address(f"{CLIENT}, {CLOUDFLARE}", "")
    assert addr != CLOUDFLARE


def test_the_first_element_is_never_the_answer_when_a_proxy_appended():
    """The bug in the three existing auth routes: `.split(",")[0]` is client-written."""
    addr, _, _ = client_address(f"{FORGED},{CLIENT}, {CLOUDFLARE}", "")
    assert addr != FORGED


def test_no_header_falls_back_to_remote_addr_and_says_so():
    addr, source, _ = client_address("", CLIENT)
    assert addr == CLIENT
    assert source == "remote_addr"
    assert client_address("", "")[0] == "anon"


def test_a_chain_shorter_than_the_expected_hops_falls_back_to_the_socket_peer():
    """Found by this test failing against the first implementation, which returned the
    rightmost element instead. If the request did not arrive through the expected proxies,
    EVERY element of the header is potentially client-written, so the only safe value is
    the socket peer, which a caller cannot choose."""
    addr, source, _ = client_address(FORGED, "10.0.0.9")
    assert addr == "10.0.0.9"
    assert addr != FORGED
    assert source == "remote_addr_short_chain", "the fallback must be visible, not silent"


def test_the_hop_count_is_self_checking():
    """A client that writes anything makes the chain LONGER, which moves the index with
    it. So a two-element chain means the client wrote nothing and element 0 is genuine,
    and a three-element chain means they wrote one thing and element 1 is genuine."""
    assert client_address(f"{CLIENT}, {CLOUDFLARE}", "")[0] == CLIENT
    assert client_address(f"{FORGED},{CLIENT}, {CLOUDFLARE}", "")[0] == CLIENT
    assert client_address(f"a,b,{CLIENT}, {CLOUDFLARE}", "")[0] == CLIENT


def test_ipv6_collapses_to_a_64():
    addr, _, _ = client_address(f"2001:db8::1, {CLOUDFLARE}", "")
    assert addr.endswith("/64")


def test_unparseable_junk_is_kept_not_dropped():
    """Dropping it would let a caller escape any keying by sending garbage."""
    addr, _, _ = client_address(f"not-an-ip, {CLOUDFLARE}", "")
    assert addr == "not-an-ip"


# ── the Cloudflare header, observed and not yet trusted ───────────────────────
def test_cloudflares_own_header_is_reported_as_agreeing():
    _, _, agrees = client_address(f"{CLIENT}, {CLOUDFLARE}", "", cf_connecting_ip=CLIENT)
    assert agrees is True


def test_a_disagreement_is_surfaced_rather_than_silently_preferred():
    """If these ever disagree, the hop count is wrong or a proxy changed. Worth seeing."""
    _, _, agrees = client_address(f"{CLIENT}, {CLOUDFLARE}", "", cf_connecting_ip="9.9.9.9")
    assert agrees is False


def test_an_absent_cloudflare_header_is_unknown_not_false():
    """None and False mean different things and must not collapse."""
    _, _, agrees = client_address(f"{CLIENT}, {CLOUDFLARE}", "")
    assert agrees is None


def test_the_cloudflare_header_does_not_change_the_answer():
    """It is observed, not relied on. This file's history is a lesson about acting on the
    unverified half of a measurement, so nothing depends on it until it is verified."""
    a, _, _ = client_address(f"{CLIENT}, {CLOUDFLARE}", "", cf_connecting_ip="9.9.9.9")
    b, _, _ = client_address(f"{CLIENT}, {CLOUDFLARE}", "")
    assert a == b == CLIENT
