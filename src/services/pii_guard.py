"""pii_guard — detection/masking of prohibited individual student data.

Two boundaries rely on this module:
  * Step 1 (input gate) — REJECT individual health/identity data; MASK school-internal
    financial data before it reaches state.
  * Step 6 (output gate) — verify neither output section can emit individual data,
    scanning nested structures, JSON and Markdown alike.

Design notes:
  * Only *individual* signals are matched (specific conditions/medications attached
    to a person, contact identifiers, financial account numbers). Generic domain
    vocabulary — "medical facility", "first-aid", "health/medical category" — is
    intentionally NOT flagged, so aggregate flags and checklist titles pass.
  * Aggregate health flags are controlled enums (NONE/PRESENT/UNKNOWN) and never
    match these patterns.
"""

from __future__ import annotations

import re
from typing import Any, List, Tuple

# Individual health / disability signals (prohibited at every boundary).
# Matches specific conditions/medications/devices attached to a person — NOT
# generic domain vocabulary ("medical facility", "consent form", "health policy"),
# which legitimately appears in the guidance/output.
_HEALTH_PATTERNS = {
    "medical_condition": re.compile(
        r"\b(asthma|diabet\w*|epilep\w*|seizure|allerg\w*|anaphyla\w*|"
        r"peanut|autis\w*|adhd|wheelchair|insulin|epi-?pen|inhaler|"
        r"medication|prescri\w*|dosage|blood type|disabilit\w*)\b",
        re.IGNORECASE,
    ),
}

# Individual identity / contact signals. Phone/my-number require 10+ digits so
# structured ISO dates (8 digits) and small counts never false-match.
_PII_PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "phone": re.compile(r"(?<!\d)(?:\+?\d[ \-]?){10,}\d(?!\d)"),
    "my_number": re.compile(r"(?<!\d)\d{4}[ \-]?\d{4}[ \-]?\d{4}(?!\d)"),  # JP individual number (12 digits)
    "student_id": re.compile(r"\b(?:student|pupil)\s*id\s*[:#]?\s*\w+\b", re.IGNORECASE),
}

# School-internal financial signals — MASKED (not rejected) at input.
_FINANCIAL_PATTERNS = {
    "card_number": re.compile(r"\b(?:\d[ \-]?){13,16}\b"),
    "bank_account": re.compile(r"\b(?:account|acct)\s*(?:no\.?|number|#)?\s*[:#]?\s*\d{5,}\b", re.IGNORECASE),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,}\b"),
}

_FINANCIAL_MASK = "[MASKED_FINANCIAL]"


def scan_health(text: str) -> List[str]:
    """Return labels of individual health/consent signals found (empty if none)."""
    if not isinstance(text, str):
        return []
    return [label for label, pat in _HEALTH_PATTERNS.items() if pat.search(text)]


def scan_pii(text: str) -> List[str]:
    """Return labels of individual identity/contact signals found (empty if none)."""
    if not isinstance(text, str):
        return []
    return [label for label, pat in _PII_PATTERNS.items() if pat.search(text)]


def mask_financial(text: str) -> Tuple[str, List[str]]:
    """Mask school-internal financial data. Returns (masked_text, matched_labels)."""
    if not isinstance(text, str):
        return text, []
    matched: List[str] = []
    masked = text
    for label, pat in _FINANCIAL_PATTERNS.items():
        if pat.search(masked):
            matched.append(label)
            masked = pat.sub(_FINANCIAL_MASK, masked)
    return masked, matched


def scan_prohibited(value: Any) -> List[str]:
    """Recursively scan a value (str/dict/list/nested) for prohibited individual
    health or identity signals. Returns a de-duplicated list of ``kind:label``
    findings. Used by the Step-6 output gate and the proof-of-boundary tests to
    prove individual data never reaches an output/state boundary.
    """
    findings: List[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            findings.extend(f"health:{lbl}" for lbl in scan_health(node))
            findings.extend(f"pii:{lbl}" for lbl in scan_pii(node))
        elif isinstance(node, dict):
            for k, v in node.items():
                _walk(k)
                _walk(v)
        elif isinstance(node, (list, tuple, set)):
            for item in node:
                _walk(item)

    _walk(value)
    # de-duplicate, preserve order
    seen: set[str] = set()
    unique: List[str] = []
    for f in findings:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    return unique
