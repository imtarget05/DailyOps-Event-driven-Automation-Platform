"""Centralized destructive/dangerous content detection (ARCH-002).

Single source of truth for what counts as a destructive verb/phrase.
Every consumer classifies through this module instead of keeping a local
word list, so a new destructive term is added exactly once.

Two detection modes exist because the historical call sites matched in
different ways — both are preserved (functional equivalence is a
requirement of the refactor):

- Token mode (`is_destructive`): input is a machine-shaped action id such
  as ``delete_system_data``. Verbs match as whole alphanumeric tokens, so
  ``deadline``/``readme`` do not false-positive on ``delete``.
- Phrase mode (`contains_destructive_term`): input is free text (event
  text / recommendations). Single-letter tokens like ``rm`` or ``rf``
  would false-positive inside ordinary words there ("confi**rm**",
  "info**rm**ation"), so those are detected via the explicit phrase
  ``rm -rf``; the remaining verbs still match on word boundaries.

DAILYOPS invariant: destructive actions are ALWAYS denied by the policy
engine — this module only detects; verdicts live in ``policy_engine``.
"""
from __future__ import annotations

import re

# TASK-002: authoritative destructive list — union of the former local
# lists (policy_engine.DENY_TOKENS, operations_agent.DENY_KEYWORDS,
# specialist_validator._validate_action_fields "dangerous" list).
DESTRUCTIVE_VERBS: frozenset[str] = frozenset({
    "delete", "drop", "truncate", "shutdown", "destroy", "purge", "wipe",
})
# Token-only forms: unambiguous in token mode, but as free-text substrings
# they collide with ordinary words ("confirm", "information").
DESTRUCTIVE_TOKEN_ONLY_VERBS: frozenset[str] = frozenset({"rm", "rf"})
# Phrase form used by free-text mode for the rm/rf pair.
DESTRUCTIVE_PHRASES: tuple[str, ...] = ("rm -rf",)

DESTRUCTIVE_TOKENS: frozenset[str] = DESTRUCTIVE_VERBS | DESTRUCTIVE_TOKEN_ONLY_VERBS


def is_destructive(recommended_action: object) -> bool:
    """Token-boundary destructive check (no substring false-positives).

    Coerces non-string input (poisoned LLM output) instead of raising.
    Formerly ``policy_engine.is_destructive`` — re-exported there.
    """
    tokens = {t for t in re.split(r"[^a-z0-9]+", str(recommended_action or "").lower()) if t}
    return bool(tokens & DESTRUCTIVE_TOKENS)


def contains_destructive_term(text: object) -> bool:
    """Free-text destructive check for event text / recommendations.

    Word-boundary match for the multi-char verbs plus the explicit
    ``rm -rf`` phrase; single-letter tokens (rm/rf) are deliberately NOT
    substring-matched here — "confirm"/"information" would false-positive.
    Coerces non-string input instead of raising.
    """
    lower = str(text or "").lower()
    if any(phrase in lower for phrase in DESTRUCTIVE_PHRASES):
        return True
    return any(re.search(r"\b" + verb, lower) for verb in sorted(DESTRUCTIVE_VERBS))
