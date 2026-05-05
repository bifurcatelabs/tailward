"""Unit tests for ``tailward.config`` helpers."""

from __future__ import annotations

from tailward.config import is_loopback_bind


def test_loopback_canonical_ipv4() -> None:
    assert is_loopback_bind("127.0.0.1") is True


def test_loopback_localhost_dns_name() -> None:
    assert is_loopback_bind("localhost") is True


def test_loopback_ipv6() -> None:
    assert is_loopback_bind("::1") is True


def test_loopback_extended_ipv4_range() -> None:
    """``127.0.0.0/8`` is the full loopback range, not just .1.

    Some users alias a loopback hostname to e.g. 127.0.0.2 to avoid
    port conflicts; the helper should still recognize it as loopback.
    """
    assert is_loopback_bind("127.0.0.2") is True
    assert is_loopback_bind("127.255.255.254") is True


def test_non_loopback_zero_zero_zero_zero() -> None:
    """The realistic v3 foot-gun: user binds to 0.0.0.0 thinking it
    enables LAN access. Helper must report this as non-loopback so
    the warning fires."""
    assert is_loopback_bind("0.0.0.0") is False


def test_non_loopback_explicit_lan_ip() -> None:
    assert is_loopback_bind("192.168.1.50") is False
    assert is_loopback_bind("10.0.0.1") is False


def test_non_loopback_empty_string() -> None:
    """An empty bind host is not loopback — the daemon wouldn't bind
    cleanly with this value, but the helper shouldn't crash on it."""
    assert is_loopback_bind("") is False


def test_non_loopback_garbage() -> None:
    """Unparseable host strings are non-loopback. The startup warning
    will fire, which is the conservative default."""
    assert is_loopback_bind("not-an-address") is False
    assert is_loopback_bind("256.300.400.500") is False


def test_loopback_handles_whitespace_and_case() -> None:
    """Config files sometimes carry incidental whitespace or case
    quirks; the helper should be forgiving."""
    assert is_loopback_bind("  127.0.0.1  ") is True
    assert is_loopback_bind("LOCALHOST") is True
