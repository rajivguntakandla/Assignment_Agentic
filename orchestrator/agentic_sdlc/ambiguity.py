"""Deterministic ambiguity detection.

Runs regardless of LLM output and can only *add* ambiguities: a model cannot talk the orchestrator out of
asking a human when the requirement is objectively under-specified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Ambiguity

VAGUE_TERMS: dict[str, tuple[str, str]] = {
    "safer": ("What specific threats must be mitigated, and how is 'safe' verified?",
              "Block links to known-malicious domains via a configurable denylist; reject with HTTP 400"),
    "secure": ("Which threats are in scope (abuse, phishing, SSRF, data exposure)?",
               "Focus on phishing/malware destinations; existing SSRF protections remain"),
    "faster": ("Which operation must be faster, and what is the target latency (p95)?",
               "Redirect p95 under 50 ms at current load"),
    "better": ("Better by which measurable criterion?", "Define acceptance criteria before implementation"),
    "improve": ("What outcome defines the improvement, and how is it measured?",
                "Scope to the smallest change with a testable acceptance criterion"),
    "scalable": ("What load (requests/s, data volume) must be supported?", "10x current traffic"),
    "robust": ("Which failure modes must be handled?", "Invalid input and dependency failures"),
    "user-friendly": ("Which users and which interactions?", "API consumers; clear RFC 9457 errors"),
    "some": ("Which ones exactly?", "Start with a configurable list, empty by default"),
    "appropriate": ("Appropriate according to which rule?", "Follow existing service conventions"),
    "etc": ("What else is included?", "Only the explicitly listed items"),
    "bad": ("How is 'bad' determined, and by whom is the list maintained?",
            "An operator-maintained configuration list; no external reputation service"),
}
_MEASURABLE = re.compile(r"\d|\bHTTP\b|\b[1-5]\d\d\b|\bms\b|\bseconds?\b|\bmax(imum)?\b|\bat most\b", re.I)


@dataclass
class AmbiguityReport:
    ambiguities: list[Ambiguity] = field(default_factory=list)
    has_measurable_criteria: bool = True
    score: int = 0

    @property
    def unresolved(self) -> bool:
        return bool(self.ambiguities)


def analyze(text: str, answered: dict[str, str] | None = None) -> AmbiguityReport:
    answered = {k.lower(): v for k, v in (answered or {}).items()}
    words = set(re.findall(r"[a-z][a-z\-]*", text.lower()))
    found = [
        Ambiguity(term=term, question=q, proposed_default=d)
        for term, (q, d) in VAGUE_TERMS.items()
        if term in words and term not in answered
    ]
    measurable = bool(_MEASURABLE.search(text))
    if not measurable and "acceptance" not in answered and (found or len(text.split()) < 25):
        found.append(Ambiguity(
            term="acceptance",
            question="What observable behaviour proves this requirement is met?",
            proposed_default="Automated API tests covering the success path and each rejection path"))
    return AmbiguityReport(found, measurable, len(found))
