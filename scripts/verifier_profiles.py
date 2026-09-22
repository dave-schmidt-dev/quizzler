"""Approved high-capability verifier routes for hybrid certification."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerifierProfile:
    """A reviewed provider/model/effort tuple allowed to certify via hybrid."""

    name: str
    provider: str
    model: str
    reasoning_effort: str | None


PROFILES = {
    "codex-terra-high": VerifierProfile(
        "codex-terra-high", "codex", "gpt-5.6-terra", "high"),
    # Registered for explicit use when Claude capacity is available. It is not
    # the default and does not cause hybrid to fall back when unavailable.
    "claude-opus-high": VerifierProfile(
        "claude-opus-high", "claude", "opus", None),
    # Registered as the designated certifying authority for the IT 540 course.
    # Selected explicitly per run; it is not the default.
    "claude-sonnet-high": VerifierProfile(
        "claude-sonnet-high", "claude", "sonnet", None),
}
DEFAULT_PROFILE = "codex-terra-high"


def get_profile(name: str) -> VerifierProfile:
    """Return an approved route or raise a clear configuration error."""
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(
            f"unknown verifier profile {name!r}; known: {', '.join(PROFILES)}"
        ) from None
