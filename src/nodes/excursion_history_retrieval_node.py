"""ExcursionHistoryRetrievalNode — Step 2 config-driven school-data retrieval.

Inner domain node (ANONYMOUS — trust is verified at the PreProcessNode boundary).
Retrieves prior-excursion counts, incident-history codes, standing-policy codes,
and ONLY the class-level aggregate health-risk flag plus health-record completeness
flag. No LLM call. On any source outage the node returns a safe degraded /
review-required state (health flags default to UNKNOWN, forcing downstream review).
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import school_data_service
from src.services.runtime_config import load_config
from src.nodes._state_access import get_field


class ExcursionHistoryRetrievalNode(FunctionNode):
    """Retrieve aggregate-only excursion history from the approved school source."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        validated: Dict[str, Any] = get_field(state, "validated_excursion", {}) or {}
        if not validated:
            return self._degraded(state, "no validated excursion input available")

        config = load_config()
        ctx = InvocationContext.from_state(state)
        try:
            credential = ctx.secrets.require(config["school_data_secret_key"])
        except Exception:
            return self._degraded(state, "school data credential could not be resolved")

        try:
            result = school_data_service.fetch_excursion_history(
                config["school_data_source_ref"],
                credential,
                destination_type=validated.get("destination_type", ""),
                limit=config["school_data_page_limit"],
            )
        except school_data_service.SchoolDataError as exc:
            return self._degraded(state, f"school data source unavailable: {exc}")

        # Safety-first combination: any PRESENT signal (source or declared) wins.
        combined_flag = self._combine_health_flag(
            result["aggregate_health_risk_flag"],
            validated.get("declared_aggregate_medical_flag", "UNKNOWN"),
        )

        warnings = []
        if result["health_record_completeness"] != "COMPLETE":
            warnings.append(
                "health-record completeness is not COMPLETE — health/medical items " "will be forced to REQUIRES_REVIEW"
            )

        emit_trace_event(
            "ExcursionHistoryRetrievalNode_history_retrieved",
            {
                "status": result["status"],
                "prior_excursions": result["excursion_history_summary"]["prior_excursions"],
                "aggregate_health_risk_flag": combined_flag,
                "health_record_completeness": result["health_record_completeness"],
                "dropped_individual_keys": result.get("dropped_individual_keys", []),
            },
            state,
        )
        return {
            "excursion_history_summary": result["excursion_history_summary"],
            "incident_history_flags": result["incident_history_flags"],
            "standing_policy_flags": result["standing_policy_flags"],
            "aggregate_health_risk_flag": combined_flag,
            "health_record_completeness": result["health_record_completeness"],
            "data_source_status": result["status"],
            "data_source_provenance": result.get("source_provenance", {}),
            "review_warnings": get_field(state, "review_warnings", []) + warnings,
            "status": AgentStatus.SUCCESS.value,
        }

    @staticmethod
    def _combine_health_flag(source_flag: str, declared_flag: str) -> str:
        flags = {str(source_flag).upper(), str(declared_flag).upper()}
        if "PRESENT" in flags:
            return "PRESENT"
        if flags == {"NONE"}:
            return "NONE"
        return "UNKNOWN"

    def _degraded(self, state: dict[str, Any], reason: str) -> dict[str, Any]:
        emit_trace_event(
            "ExcursionHistoryRetrievalNode_history_degraded",
            {"reason": reason},
            state,
        )
        return {
            "excursion_history_summary": {"prior_excursions": 0, "destinations_visited": 0},
            "incident_history_flags": [],
            "standing_policy_flags": [],
            "aggregate_health_risk_flag": "UNKNOWN",
            "health_record_completeness": "UNKNOWN",
            "data_source_status": "UNAVAILABLE",
            "data_source_provenance": {},
            "review_warnings": get_field(state, "review_warnings", [])
            + [f"excursion history unavailable — proceeding review-only: {reason}"],
            "status": AgentStatus.SUCCESS.value,
        }
