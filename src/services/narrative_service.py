"""narrative_service — Step 5 LLM narrative seam (guidance text only).

Deterministic status selection lives in the node. The LLM is used ONLY to phrase
human-readable guidance for a checklist item. If the model times out or returns a
malformed response, the service falls back to a deterministic template so the
assessment is never blocked and never depends on the model for correctness
(issue #7).
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.services.llm_runtime import complete_text
from src.services.runtime_config import mock_mode


class NarrativeError(Exception):
    """Base error for narrative generation."""


class NarrativeUnavailable(NarrativeError):
    """Raised when no LLM client is wired for this environment."""


class NarrativeTimeout(NarrativeError):
    """Raised when the model call exceeds its timeout."""


def _template_narrative(item: Dict[str, Any], status: str, citations: List[Dict[str, Any]]) -> str:
    """Deterministic fallback guidance for a checklist item."""
    cites = ", ".join(sorted({c.get("source", "") for c in citations if c.get("source")}))
    cite_txt = f" See: {cites}." if cites else ""
    verb = {
        "COMPLETE": "Confirmed — retain evidence on file.",
        "NOT_APPLICABLE": "Not applicable to this excursion profile.",
        "REQUIRES_REVIEW": "Requires human review before approval.",
    }.get(status, "Requires human review before approval.")
    return f"{item.get('title', 'Checklist item')}: {verb}{cite_txt}"


def llm_narrative(
    state: dict[str, Any],
    prompt: str,
    llm: Any,
    timeout_s: int = 15,
    max_retry: int = 1,
) -> str:
    """Call the invocation-scoped Azure client without persisting it in State."""
    return complete_text(
        state,
        [{"role": "user", "content": prompt}],
        llm,
        timeout_s=float(timeout_s),
        max_retry=max_retry,
    )


def generate_item_narrative(
    item: Dict[str, Any],
    status: str,
    citations: List[Dict[str, Any]],
    state: dict[str, Any] | None = None,
    llm: Any = None,
    timeout_s: int = 15,
    max_retry: int = 1,
    mock: bool = False,
) -> Tuple[str, str]:
    """Return ``(narrative_text, source)`` where source is ``LLM`` or ``TEMPLATE_FALLBACK``.

    The status is passed in already-decided; the model never changes it.
    """
    if mock or mock_mode():
        return _template_narrative(item, status, citations), "TEMPLATE_FALLBACK"
    try:
        prompt = (
            f"Provide one concise, non-directive guidance sentence for the excursion "
            f"checklist item '{item.get('title')}' currently assessed as {status}. "
            f"Do not include any individual student information."
        )
        text = llm_narrative(
            state or {},
            prompt,
            llm,
            timeout_s=timeout_s,
            max_retry=max_retry,
        )
        if not isinstance(text, str) or not text.strip():
            raise NarrativeError("malformed model response")
        return text.strip(), "LLM"
    except (NarrativeError, Exception):
        # Any model failure (unavailable, timeout, malformed) → safe template.
        return _template_narrative(item, status, citations), "TEMPLATE_FALLBACK"
