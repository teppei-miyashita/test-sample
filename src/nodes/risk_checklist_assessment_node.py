"""RiskChecklistAssessmentNode — Step 5 config-driven checklist assessment.

Inner domain node. Assigns each configured checklist item COMPLETE,
REQUIRES_REVIEW, or NOT_APPLICABLE. Status selection is fully deterministic and
independent of the LLM; the LLM is used ONLY to phrase human-readable guidance and
its failure falls back to a deterministic template. Enforces the safety-first health
default: absent/unconfirmed health completeness forces every health/medical item to
REQUIRES_REVIEW (never NOT_APPLICABLE). Makes no automatic approval decision.

Trust note (reconciling issue #7 "Require VERIFIED_EXTERNAL"): this is an *inner*
DomainWorkflowGraph node, so per CLAUDE.md §2-1 / IMPLEMENTATION §2-1 it declares
ANONYMOUS to avoid the trust-trap anti-pattern. The VERIFIED_EXTERNAL requirement
is enforced once at the PreProcessNode boundary before any LLM operation runs;
declaring it again here would falsely imply re-verification. The optional LLM
client is injected by the graph and is never stored in State.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import narrative_service
from src.services.runtime_config import CATEGORY_PRECEDENCE, load_config
from src.nodes._state_access import get_field

_HEALTH = "health_medical"


class RiskChecklistAssessmentNode(FunctionNode):
    """Assess checklist items with deterministic status + LLM narrative."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, llm: Any = None) -> None:
        super().__init__()
        self._llm = llm

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        config = load_config()
        templates: Dict[str, List[Dict[str, Any]]] = config["checklist_templates"]
        risk_category_map: Dict[str, Any] = get_field(state, "risk_category_map", {}) or {}
        baseline = set(config["baseline_categories"])

        health_completeness = get_field(state, "health_record_completeness", "UNKNOWN")
        health_flag = get_field(state, "aggregate_health_risk_flag", "UNKNOWN")
        coverage = get_field(state, "destination_coverage", "NOT_FOUND")
        citations: List[Dict[str, Any]] = get_field(state, "guidance_citations", []) or []
        covered_categories = {c.get("category") for c in citations if not c.get("is_fallback")}

        emit_trace_event(
            "RiskChecklistAssessmentNode_assessment_started",
            {
                "health_completeness": health_completeness,
                "health_flag": health_flag,
                "coverage": coverage,
                "category_count": len(templates),
            },
            state,
        )

        items: List[Dict[str, Any]] = []
        statuses: Dict[str, str] = {}
        narrative_sources: set[str] = set()

        ordered_categories = sorted(templates.keys(), key=lambda c: CATEGORY_PRECEDENCE.get(c, 99))
        for category in ordered_categories:
            active = self._is_active(category, risk_category_map, baseline)
            for template in templates[category]:
                status = self._decide_status(
                    category,
                    template,
                    active,
                    health_completeness,
                    health_flag,
                    coverage,
                    category in covered_categories,
                )
                cat_citations = [c for c in citations if c.get("category") == category]
                narrative, source = narrative_service.generate_item_narrative(
                    template,
                    status,
                    cat_citations,
                    state=state,
                    llm=self._llm,
                    timeout_s=config["llm_narrative_timeout_s"],
                    max_retry=int(config.get("max_retry", 1)),
                )
                narrative_sources.add(source)
                item = {
                    "id": template["id"],
                    "category": category,
                    "title": template["title"],
                    "status": status,
                    "evidence_required": template.get("evidence_required", False),
                    "guidance": narrative,
                    "citations": [
                        {
                            "source": c.get("source"),
                            "section": c.get("section"),
                            "is_fallback": c.get("is_fallback", False),
                        }
                        for c in cat_citations
                    ],
                }
                items.append(item)
                statuses[template["id"]] = status

        requires_review = [i["id"] for i in items if i["status"] == "REQUIRES_REVIEW"]
        narrative_source = "LLM" if "LLM" in narrative_sources else "TEMPLATE_FALLBACK"

        emit_trace_event(
            "RiskChecklistAssessmentNode_checklist_assessed",
            {
                "item_count": len(items),
                "requires_review_count": len(requires_review),
                "health_completeness": health_completeness,
                "narrative_source": narrative_source,
            },
            state,
        )
        warnings = get_field(state, "review_warnings", [])
        if requires_review:
            warnings = warnings + [f"{len(requires_review)} checklist item(s) REQUIRES_REVIEW before approval"]
        return {
            "checklist_items": items,
            "checklist_statuses": statuses,
            "assessment_narrative_source": narrative_source,
            "review_warnings": warnings,
            "status": AgentStatus.SUCCESS.value,
        }

    @staticmethod
    def _is_active(category: str, risk_category_map: Dict[str, Any], baseline: set[str]) -> bool:
        if category in baseline:
            return True
        info = risk_category_map.get(category)
        return bool(isinstance(info, dict) and info.get("applicable"))

    @staticmethod
    def _decide_status(
        category: str,
        template: Dict[str, Any],
        active: bool,
        health_completeness: str,
        health_flag: str,
        coverage: str,
        category_covered: bool,
    ) -> str:
        """Deterministic status selection (no LLM influence)."""
        # Safety-first health default — never NOT_APPLICABLE, and only COMPLETE when
        # completeness is confirmed AND no aggregate risk is present.
        if category == _HEALTH:
            if str(health_completeness).upper() == "COMPLETE" and str(health_flag).upper() == "NONE":
                return "COMPLETE"
            return "REQUIRES_REVIEW"

        if not active:
            return "NOT_APPLICABLE"

        # Evidence-required items cannot auto-complete without confirmed evidence.
        if template.get("evidence_required", False):
            return "REQUIRES_REVIEW"

        # Non-evidence items complete only with real (non-fallback) covered guidance.
        if category_covered and str(coverage).upper() != "NOT_FOUND":
            return "COMPLETE"
        return "REQUIRES_REVIEW"


# Domain-facing alias (issue #7).
ChecklistAssessmentNode = RiskChecklistAssessmentNode
