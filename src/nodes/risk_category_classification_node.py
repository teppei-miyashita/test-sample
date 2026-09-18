"""RiskCategoryClassificationNode — Step 3 deterministic risk classification.

Inner domain node (ANONYMOUS). Maps destination type, transport mode, and
activities to applicable risk categories using the configuration rule table. Fully
deterministic: one execute() call, no LLM, no graph branches. Health/medical stays
applicable for the downstream safety-first assessment. Never infers individual
medical risk and never makes an approval decision.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.runtime_config import (
    CATEGORY_PRECEDENCE,
    RISK_CATEGORIES,
    load_config,
)
from src.nodes._state_access import get_field


class RiskCategoryClassificationNode(FunctionNode):
    """Deterministically construct the applicable risk_category_map."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        validated: Dict[str, Any] = get_field(state, "validated_excursion", {}) or {}
        config = load_config()

        drivers: Dict[str, List[str]] = {cat: [] for cat in RISK_CATEGORIES}
        unknowns: List[str] = []

        # Baseline categories always apply (safety floor, includes health_medical).
        for cat in config["baseline_categories"]:
            drivers.setdefault(cat, []).append("baseline")

        destination_type = validated.get("destination_type", "")
        self._apply_rule(
            config["destination_rules"],
            destination_type,
            f"destination:{destination_type}",
            drivers,
            unknowns,
            config["unknown_fallback_categories"],
        )

        transport_mode = validated.get("transport_mode", "")
        self._apply_rule(
            config["transport_rules"],
            transport_mode,
            f"transport:{transport_mode}",
            drivers,
            unknowns,
            config["unknown_fallback_categories"],
        )

        # Multi-activity merging: union of every activity's categories.
        for activity in validated.get("activity_types", []) or []:
            self._apply_rule(
                config["activity_rules"],
                activity,
                f"activity:{activity}",
                drivers,
                unknowns,
                config["unknown_fallback_categories"],
            )

        risk_category_map = {
            cat: {
                "applicable": bool(drivers.get(cat)),
                "drivers": sorted(set(drivers.get(cat, []))),
                "precedence": CATEGORY_PRECEDENCE.get(cat, 99),
            }
            for cat in RISK_CATEGORIES
        }
        applicable = sorted(
            (c for c, v in risk_category_map.items() if v["applicable"]),
            key=lambda c: CATEGORY_PRECEDENCE.get(c, 99),
        )

        emit_trace_event(
            "RiskCategoryClassificationNode_categories_mapped",
            {"applicable_categories": applicable, "unknown_values": unknowns},
            state,
        )
        return {
            "risk_category_map": risk_category_map,
            "classification_unknowns": unknowns,
            "status": AgentStatus.SUCCESS.value,
        }

    @staticmethod
    def _apply_rule(
        rule_table: Dict[str, List[str]],
        value: str,
        driver: str,
        drivers: Dict[str, List[str]],
        unknowns: List[str],
        fallback_categories: List[str],
    ) -> None:
        """Apply one rule-table lookup. Unknown values engage the fallback."""
        value = str(value).strip().lower()
        if not value:
            return
        if value in rule_table:
            for cat in rule_table[value]:
                drivers.setdefault(cat, []).append(driver)
        else:
            unknowns.append(value)
            for cat in fallback_categories:
                drivers.setdefault(cat, []).append(f"unknown_fallback:{driver}")
