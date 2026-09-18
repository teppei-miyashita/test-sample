"""PostProcessNode — Step 6 dual-section excursion brief generation + S-3 gate.

Boundary node (outer AgentBaseGraph, VERIFIED_EXTERNAL). Produces two human-review
artifacts:
  (A) Administrator risk checklist — statuses, citations, action items,
      coverage/staleness warnings, and a MANDATORY non-approval disclaimer.
  (B) Parent communication draft — safety / consent / emergency information with
      NO individual student PII.

The S-3 output gate blocks any individual student data from either section and
verifies the mandatory approval-disclaimer wording is preserved. Formal approval is
never automated and parent drafts are never sent — both carry human-review gates.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List

from framework.errors import SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.llm_runtime import provider_metadata, request_advisory

from src.services import pii_guard
from src.nodes._state_access import get_field

ADMIN_DISCLAIMER = (
    "MANDATORY HUMAN REVIEW: This brief is a decision-support aid only. It does NOT "
    "constitute formal excursion approval and makes no autonomous risk decision. A "
    "designated staff member must review every REQUIRES_REVIEW item and formally "
    "approve this excursion before it proceeds."
)
PARENT_DISCLAIMER = (
    "DRAFT FOR STAFF REVIEW — not yet sent. A staff member must review and approve "
    "this message before it is communicated to parents/guardians."
)


class PostProcessNode(FunctionNode):
    """Generate the dual-section brief and enforce the output data boundary."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    # ── S-3 output gate (framework calls this via __call__) ──────────────
    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        """Block individual student data and preserve the mandatory disclaimer."""
        scanned_scope = {
            "administrator_brief_markdown": result.get("administrator_brief_markdown", ""),
            "parent_communication_markdown": result.get("parent_communication_markdown", ""),
            "brief_json": result.get("brief_json", {}),
            "formatted_output": result.get("formatted_output", ""),
        }
        findings = pii_guard.scan_prohibited(scanned_scope)
        if findings:
            raise SecurityViolationError(
                "output gate blocked individual student data in the brief " f"(signals: {', '.join(findings)})"
            )
        admin_md = result.get("administrator_brief_markdown", "")
        if admin_md and "does NOT constitute formal excursion approval" not in admin_md:
            raise SecurityViolationError(
                "output gate: mandatory non-approval disclaimer missing from administrator brief"
            )
        return result

    # ── execute (Step 6 logic) ───────────────────────────────────────────
    def __init__(self, llm: object | None = None, config: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._llm = llm
        self._config = config or {}

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("input_error_message"):
            message = str(state["input_error_message"])
            return {"status": AgentStatus.SUCCESS.value, "result": message, "formatted_output": message}

        request_advisory(
            state,
            "Review the EDU-C2-045 result for clarity, grounding, and safe human review.",
            self._llm,
            timeout_s=float(self._config.get("timeout_s", 30.0)),
            max_retry=int(self._config.get("max_retry", 3)),
        )
        metadata = provider_metadata(state)
        if get_field(state, "request_status", "") == "REJECTED":
            return {**self._rejection_output(state), **metadata}

        validated: Dict[str, Any] = get_field(state, "validated_excursion", {}) or {}
        checklist: List[Dict[str, Any]] = get_field(state, "checklist_items", []) or []
        coverage = get_field(state, "destination_coverage", "NOT_FOUND")
        stale = bool(get_field(state, "corpus_staleness_warning", False))
        confidence = get_field(state, "retrieval_confidence", 0.0)
        warnings = get_field(state, "review_warnings", []) or []
        citations = get_field(state, "guidance_citations", []) or []

        action_items = [f"[{i['category']}] {i['title']}" for i in checklist if i.get("status") == "REQUIRES_REVIEW"]

        admin_md = self._admin_markdown(checklist, coverage, stale, confidence, warnings, action_items, citations)
        parent_md = self._parent_markdown(validated, coverage, stale)
        brief_json = self._brief_json(
            validated, checklist, coverage, stale, confidence, warnings, action_items, citations
        )

        emit_trace_event(
            "PostProcessNode_brief_generated",
            {
                "checklist_items": len(checklist),
                "action_items": len(action_items),
                "coverage": coverage,
                "corpus_stale": stale,
            },
            state,
        )
        return {
            "administrator_brief_markdown": admin_md,
            "parent_communication_markdown": parent_md,
            "brief_json": brief_json,
            "formatted_output": admin_md,  # §9-ZE — surfaces to API response["output"]
            "result": brief_json,
            "requires_human_review": True,
            "review_warnings": warnings,
            "status": AgentStatus.SUCCESS.value,
            **metadata,
        }

    # ── section builders ─────────────────────────────────────────────────
    def _admin_markdown(
        self,
        checklist: List[Dict[str, Any]],
        coverage: str,
        stale: bool,
        confidence: float | None,
        warnings: List[str],
        action_items: List[str],
        citations: List[Dict[str, Any]],
    ) -> str:
        lines = ["# Section A — Administrator Excursion Risk Checklist", ""]
        lines.append(f"> {ADMIN_DISCLAIMER}")
        lines.append("")
        lines.append(self._coverage_line(coverage, stale, confidence))
        if warnings:
            lines.append("")
            lines.append("**Review warnings:**")
            lines.extend(f"- {w}" for w in warnings)
        lines.append("")
        lines.append("| Category | Item | Status | Guidance |")
        lines.append("|----------|------|--------|----------|")
        for item in checklist:
            guidance = str(item.get("guidance", "")).replace("|", "/")
            lines.append(f"| {item['category']} | {item['title']} | **{item['status']}** | {guidance} |")
        lines.append("")
        lines.append("## Action items (require human review before approval)")
        if action_items:
            lines.extend(f"- [ ] {a}" for a in action_items)
        else:
            lines.append("- [ ] Confirm all items and record formal approval decision.")
        lines.append("")
        lines.append("## Guidance citations")
        for c in self._unique_citations(citations):
            tag = " (general MEXT fallback)" if c.get("is_fallback") else ""
            lines.append(f"- {c.get('source', 'unknown source')} — {c.get('section', '')}{tag}")
        return "\n".join(lines)

    def _parent_markdown(self, validated: Dict[str, Any], coverage: str, stale: bool) -> str:
        destination = validated.get("destination", "the planned destination")
        lines = ["# Section B — Parent/Guardian Communication (DRAFT)", ""]
        lines.append(f"> {PARENT_DISCLAIMER}")
        lines.append("")
        lines.append("Dear parents and guardians,")
        lines.append("")
        lines.append(
            f"We are planning a school excursion to {destination} "
            f"({validated.get('start_date', 'TBD')} to {validated.get('end_date', 'TBD')}) "
            f"for {validated.get('year_group', 'the class')}."
        )
        lines.append("")
        lines.append("## Safety")
        lines.append("- Staff supervision and emergency procedures are being confirmed.")
        lines.append("- Transport and venue arrangements follow school safety policy.")
        lines.append("## Consent")
        lines.append("- A signed consent form will be required before the excursion.")
        lines.append(
            "- Please inform staff of any aggregate needs via the official form (do not send medical details in reply to this notice)."
        )
        lines.append("## Emergency information")
        lines.append("- Emergency contacts and the nearest medical facility will be documented.")
        if coverage != "FULL" or stale:
            lines.append("")
            lines.append("## Note for staff (not for distribution)")
            lines.append(self._coverage_line(coverage, stale, None))
        return "\n".join(lines)

    def _brief_json(
        self,
        validated: Dict[str, Any],
        checklist: List[Dict[str, Any]],
        coverage: str,
        stale: bool,
        confidence: float | None,
        warnings: List[str],
        action_items: List[str],
        citations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "administrator": {
                "disclaimer": ADMIN_DISCLAIMER,
                "requires_human_review": True,
                "formal_approval_automated": False,
                "excursion": {
                    "destination": validated.get("destination", ""),
                    "destination_type": validated.get("destination_type", ""),
                    "dates": [validated.get("start_date", ""), validated.get("end_date", "")],
                    "year_group": validated.get("year_group", ""),
                    "student_count": validated.get("student_count", 0),
                },
                "checklist": checklist,
                "action_items": action_items,
                "coverage": coverage,
                "corpus_staleness_warning": stale,
                "retrieval_confidence": confidence,
                "citations": self._unique_citations(citations),
                "review_warnings": warnings,
            },
            "parent": {
                "disclaimer": PARENT_DISCLAIMER,
                "requires_human_review": True,
                "delivery_automated": False,
                "coverage_note": self._coverage_line(coverage, stale, None),
            },
        }

    def _rejection_output(self, state: dict[str, Any]) -> dict[str, Any]:
        errors = get_field(state, "validation_errors", []) or ["request rejected at validation"]
        admin_md = (
            "# Section A — Administrator Excursion Risk Checklist\n\n"
            f"> {ADMIN_DISCLAIMER}\n\n"
            "**Request rejected at input validation — no risk brief produced.**\n\n"
            + "\n".join(f"- {e}" for e in errors)
        )
        parent_md = (
            "# Section B — Parent/Guardian Communication (DRAFT)\n\n"
            f"> {PARENT_DISCLAIMER}\n\n"
            "No parent draft generated: the excursion request did not pass validation."
        )
        brief_json = {
            "administrator": {
                "disclaimer": ADMIN_DISCLAIMER,
                "requires_human_review": True,
                "formal_approval_automated": False,
                "status": "REJECTED",
                "validation_errors": errors,
            },
            "parent": {"disclaimer": PARENT_DISCLAIMER, "requires_human_review": True, "delivery_automated": False},
        }
        emit_trace_event(
            "PostProcessNode_brief_rejected",
            {"validation_error_count": len(errors)},
            state,
        )
        return {
            "administrator_brief_markdown": admin_md,
            "parent_communication_markdown": parent_md,
            "brief_json": brief_json,
            "formatted_output": admin_md,
            "result": brief_json,
            "requires_human_review": True,
            "status": AgentStatus.ERROR.value,
        }

    @staticmethod
    def _coverage_line(coverage: str, stale: bool, confidence: float | None) -> str:
        parts = [f"**Destination guidance coverage:** {coverage}"]
        if confidence is not None:
            parts.append(f"(retrieval confidence {confidence})")
        if coverage != "FULL":
            parts.append("— general MEXT fallback guidance applied; verify locally.")
        if stale:
            parts.append("**Corpus is stale — verify against the latest MEXT updates.**")
        return " ".join(parts)

    @staticmethod
    def _unique_citations(citations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen: set[tuple[Any, Any]] = set()
        unique: List[Dict[str, Any]] = []
        for c in citations:
            key = (c.get("source"), c.get("section"))
            if key not in seen:
                seen.add(key)
                unique.append(
                    {"source": c.get("source"), "section": c.get("section"), "is_fallback": c.get("is_fallback", False)}
                )
        return unique


# Domain-facing alias (issue #8).
ExcursionBriefGenerationNode = PostProcessNode
