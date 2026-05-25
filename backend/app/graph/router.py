"""ROUTE node — decide which client a test targets.

Cheap heuristic first (keyword search in the test summary + step text). Only
when ambiguous does it fall back to the LLM. This keeps cost near zero for the
~90% of tests that name their client outright.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ClientName = Literal["web", "unity", "studio", "manual"]


@dataclass
class RouteDecision:
    client: ClientName
    confidence: float  # 0-1
    reason: str


# Keyword groups — first match wins. Order matters: more specific phrases first.
KEYWORDS: dict[ClientName, list[str]] = {
    "studio": [
        r"\bonbase studio\b",
        r"\bstudio\b",
        r"\bworkflow studio\b",
        r"\bconfiguration\b",  # OnBase Configuration is a Studio sibling
        r"\bunity form designer\b",
    ],
    "unity": [
        r"\bunity client\b",
        r"\bunity\b",
        r"\bthick client\b",
        r"\bclient.exe\b",
    ],
    "web": [
        r"\bweb client\b",
        r"\bappnet\b",
        r"\bbrowser\b",
        r"\bweb interface\b",
        r"\bcore-based web client\b",
    ],
}


def route_from_text(summary: str, preconditions: str = "", first_step: str = "") -> RouteDecision:
    """Pure heuristic. Returns ``manual`` if nothing matches."""
    haystack = " ".join([summary or "", preconditions or "", first_step or ""]).lower()
    hits: dict[ClientName, int] = {"web": 0, "unity": 0, "studio": 0, "manual": 0}
    for client, patterns in KEYWORDS.items():
        for pat in patterns:
            if re.search(pat, haystack):
                hits[client] += 1
    best = max(hits.items(), key=lambda kv: kv[1])
    if best[1] == 0:
        return RouteDecision("manual", 0.0, "no client keyword matched")
    # Confidence scales with how many distinct patterns matched.
    confidence = min(1.0, 0.5 + 0.15 * best[1])
    return RouteDecision(best[0], confidence, f"{best[1]} keyword hit(s)")


async def route(summary: str, preconditions: str = "", first_step: str = "", min_confidence: float = 0.65) -> RouteDecision:
    """Heuristic first; LLM fallback below the confidence threshold.

    LLM fallback is wired in Phase E once the orchestrator is in place. For
    now we surface the low-confidence decision so the caller can prompt the
    user to confirm via ``ask_user``.
    """
    decision = route_from_text(summary, preconditions, first_step)
    if decision.confidence < min_confidence and decision.client != "manual":
        decision = RouteDecision(decision.client, decision.confidence, decision.reason + " — confirm with user")
    return decision
