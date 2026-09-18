"""guidance_rag_service — Step 4 VectorRAG over approved safety-guidance corpora.

Queries a Qdrant collection holding MEXT school-safety guidance, school policy, and
destination-risk sources, one query per applicable risk category. Emits citations,
an aggregate retrieval confidence, a destination-coverage verdict
(FULL/PARTIAL/NOT_FOUND), and a corpus-staleness signal.

When a category is not covered by the corpus, a general MEXT fallback citation is
substituted (issue #6) so guidance is never silently empty. When the connector is
not wired, ``GuidanceUnavailable`` is raised and the node degrades to a review-only
state carrying MEXT fallback guidance.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.services.runtime_config import load_config, mock_mode

# MEXT = Japan Ministry of Education, Culture, Sports, Science and Technology.
_MEXT_GENERAL = "MEXT School Excursion Safety Guidelines (general)"


class GuidanceRAGError(Exception):
    """Base error for the guidance retrieval service."""


class GuidanceUnavailable(GuidanceRAGError):
    """Raised when the vector store is unreachable or not wired."""


def build_queries(risk_category_map: Dict[str, Any], destination_type: str = "") -> List[Dict[str, str]]:
    """Build one query per *applicable* risk category (issue #6)."""
    queries: List[Dict[str, str]] = []
    for category, info in (risk_category_map or {}).items():
        if not isinstance(info, dict) or not info.get("applicable"):
            continue
        text = f"school excursion safety guidance for {category.replace('_', ' ')}"
        if destination_type:
            text += f" at {destination_type.replace('_', ' ')}"
        queries.append({"category": category, "text": text})
    return queries


def mext_fallback(categories: List[str]) -> List[Dict[str, Any]]:
    """General MEXT fallback citations for PARTIAL / NOT_FOUND coverage."""
    return [
        {
            "category": category,
            "source": _MEXT_GENERAL,
            "section": f"General safety principles — {category.replace('_', ' ')}",
            "corpus_version": "general",
            "uri": "mext://guidelines/general",
            "is_fallback": True,
        }
        for category in categories
    ]


def _synthetic_hits(queries: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    for i, q in enumerate(queries):
        hits.append(
            {
                "category": q["category"],
                "source": "MEXT School Safety Guidance 2024",
                "section": f"Section {i + 1} — {q['category'].replace('_', ' ')}",
                "corpus_version": "2024.09",
                "uri": f"qdrant://guidance/{q['category']}",
                "score": 0.72,
                "is_fallback": False,
            }
        )
    return hits


def assess_coverage(
    hits: List[Dict[str, Any]],
    applicable_categories: List[str],
    partial_threshold: float,
) -> Dict[str, Any]:
    """Compute destination coverage verdict + aggregate confidence."""
    covered = {h["category"] for h in hits if not h.get("is_fallback")}
    applicable = set(applicable_categories)
    if not applicable:
        return {"coverage": "NOT_FOUND", "confidence": 0.0, "ratio": 0.0}
    ratio = len(covered & applicable) / len(applicable)
    scores = [float(h.get("score", 0.0)) for h in hits if not h.get("is_fallback")]
    confidence = round(sum(scores) / len(scores), 3) if scores else 0.0
    if ratio >= 1.0:
        coverage = "FULL"
    elif ratio >= partial_threshold or covered:
        coverage = "PARTIAL"
    else:
        coverage = "NOT_FOUND"
    return {"coverage": coverage, "confidence": confidence, "ratio": round(ratio, 3)}


def retrieve(
    queries: List[Dict[str, str]],
    credential_handle: str,
    *,
    collection: str = "",
    top_k: int = 5,
    cutoff: float = 0.35,
    corpus_refreshed_days_ago: int | None = None,
    mock: bool = False,
) -> Dict[str, Any]:
    """Run retrieval and return hits + corpus metadata.

    Raises ``GuidanceUnavailable`` when no vector-store client is wired.
    """
    if not credential_handle:
        raise GuidanceUnavailable("no credential handle resolved for guidance corpus")

    if mock or mock_mode():
        hits = [h for h in _synthetic_hits(queries) if h["score"] >= cutoff][: top_k * max(len(queries), 1)]
        age = corpus_refreshed_days_ago if corpus_refreshed_days_ago is not None else 30
        return {
            "hits": hits,
            "corpus_version": "2024.09",
            "corpus_age_days": age,
            "collection": collection or "excursion_guidance",
            "mode": "mock",
        }

    raise GuidanceUnavailable(
        "guidance vector store is not wired in this environment; " "provision Qdrant per the operation guide"
    )


def is_stale(corpus_age_days: int | None) -> bool:
    """True when the corpus is older than the configured staleness threshold."""
    if corpus_age_days is None:
        return True
    threshold = int(load_config()["corpus_staleness_threshold_days"])
    return corpus_age_days > threshold
