"""PolicyAndGuidanceRAGNode — Step 4 VectorRAG over approved guidance corpora.

Inner domain node (ANONYMOUS). Retrieves applicable guidance sections per risk
category from the approved Qdrant corpus (MEXT safety guidance, school policy,
destination-risk sources), then emits citations, retrieval confidence, destination
coverage (FULL/PARTIAL/NOT_FOUND), and a corpus-staleness warning. A general MEXT
fallback citation is substituted for any category the corpus does not cover, and
for the whole set when the corpus is unavailable — guidance is never silently empty.
Qdrant credentials resolve via ``ctx.secrets.require()``.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import guidance_rag_service as rag
from src.services.runtime_config import load_config
from src.nodes._state_access import get_field


class PolicyAndGuidanceRAGNode(FunctionNode):
    """Retrieve policy/guidance citations per applicable risk category."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        config = load_config()
        risk_category_map: Dict[str, Any] = get_field(state, "risk_category_map", {}) or {}
        validated: Dict[str, Any] = get_field(state, "validated_excursion", {}) or {}
        applicable = [c for c, v in risk_category_map.items() if isinstance(v, dict) and v.get("applicable")]

        if not applicable:
            return self._no_categories(state)

        queries = rag.build_queries(risk_category_map, validated.get("destination_type", ""))
        ctx = InvocationContext.from_state(state)
        try:
            credential = ctx.secrets.require(config["guidance_secret_key"])
            retrieval = rag.retrieve(
                queries,
                credential,
                collection=config["guidance_collection"],
                top_k=config["rag_top_k"],
                cutoff=config["rag_ranking_cutoff"],
            )
        except rag.GuidanceRAGError as exc:
            return self._fallback_only(state, applicable, f"guidance corpus unavailable: {exc}")
        except Exception as exc:
            # Credential unavailable (MissingSecret) or unexpected retrieval error —
            # degrade to MEXT fallback rather than crashing the pipeline.
            return self._fallback_only(state, applicable, f"guidance retrieval error: {exc}")

        hits: List[Dict[str, Any]] = retrieval.get("hits", [])
        coverage = rag.assess_coverage(hits, applicable, config["rag_partial_coverage_threshold"])

        citations = list(hits)
        uncovered = [c for c in applicable if c not in {h["category"] for h in hits if not h.get("is_fallback")}]
        if uncovered:
            citations.extend(rag.mext_fallback(uncovered))

        stale = rag.is_stale(retrieval.get("corpus_age_days"))
        warnings = self._coverage_warnings(coverage["coverage"], uncovered, stale)

        emit_trace_event(
            "PolicyAndGuidanceRAGNode_guidance_retrieved",
            {
                "coverage": coverage["coverage"],
                "confidence": coverage["confidence"],
                "citation_count": len(citations),
                "fallback_categories": uncovered,
                "corpus_stale": stale,
            },
            state,
        )
        return {
            "guidance_citations": citations,
            "retrieval_confidence": coverage["confidence"],
            "destination_coverage": coverage["coverage"],
            "corpus_staleness_warning": stale,
            "corpus_metadata": {
                "corpus_version": retrieval.get("corpus_version"),
                "corpus_age_days": retrieval.get("corpus_age_days"),
                "threshold_days": config["corpus_staleness_threshold_days"],
                "collection": retrieval.get("collection"),
            },
            "guidance_source_status": "FALLBACK" if uncovered else "OK",
            "review_warnings": get_field(state, "review_warnings", []) + warnings,
            "status": AgentStatus.SUCCESS.value,
        }

    # ── degraded paths ───────────────────────────────────────────────────
    def _fallback_only(self, state: dict[str, Any], applicable: List[str], reason: str) -> dict[str, Any]:
        emit_trace_event(
            "PolicyAndGuidanceRAGNode_guidance_fallback",
            {"reason": reason, "fallback_categories": applicable},
            state,
        )
        return {
            "guidance_citations": rag.mext_fallback(applicable),
            "retrieval_confidence": 0.0,
            "destination_coverage": "NOT_FOUND",
            "corpus_staleness_warning": True,
            "corpus_metadata": {
                "corpus_version": None,
                "collection": None,
                "threshold_days": load_config()["corpus_staleness_threshold_days"],
            },
            "guidance_source_status": "UNAVAILABLE",
            "review_warnings": get_field(state, "review_warnings", [])
            + [f"policy/guidance corpus unavailable — using general MEXT fallback: {reason}"],
            "status": AgentStatus.SUCCESS.value,
        }

    def _no_categories(self, state: dict[str, Any]) -> dict[str, Any]:
        emit_trace_event(
            "PolicyAndGuidanceRAGNode_no_categories",
            {"applicable_categories": 0},
            state,
        )
        return {
            "guidance_citations": [],
            "retrieval_confidence": 0.0,
            "destination_coverage": "NOT_FOUND",
            "corpus_staleness_warning": False,
            "corpus_metadata": {},
            "guidance_source_status": "UNAVAILABLE",
            "review_warnings": get_field(state, "review_warnings", [])
            + ["no applicable risk categories — no guidance retrieved"],
            "status": AgentStatus.SUCCESS.value,
        }

    @staticmethod
    def _coverage_warnings(coverage: str, uncovered: List[str], stale: bool) -> List[str]:
        warnings: List[str] = []
        if coverage == "PARTIAL":
            warnings.append(
                "destination guidance coverage is PARTIAL — general MEXT fallback "
                f"applied for: {', '.join(uncovered)}"
            )
        elif coverage == "NOT_FOUND":
            warnings.append("destination guidance NOT_FOUND — general MEXT fallback applied")
        if stale:
            warnings.append("guidance corpus is stale — verify against latest MEXT updates")
        return warnings
