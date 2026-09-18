"""Unit tests for RiskCategoryClassificationNode (Step 3 — deterministic classification).

All invocations use ``node(state)`` to exercise the security pipeline.
Fixtures are synthetic; no individual student data.
"""

from __future__ import annotations

def _make_state(destination_type="zoo", transport_mode="bus",
                activity_types=None) -> dict:
    if activity_types is None:
        activity_types = ["observation"]
    return {
        "caller_trust_level": "ANONYMOUS",
        "correlation_id": "test-classification",
        "validated_excursion": {
            "destination": "Ueno Zoo",
            "destination_type": destination_type,
            "transport_mode": transport_mode,
            "activity_types": activity_types,
        },
    }


class TestRiskCategoryClassificationNode:
    def test_baseline_categories_always_present(self):
        from src.nodes.risk_category_classification_node import RiskCategoryClassificationNode

        node = RiskCategoryClassificationNode()
        result = node(_make_state())
        cat_map = result.get("risk_category_map", {})
        for baseline in ("health_medical", "emergency_protocol", "insurance", "supervision_ratio"):
            assert cat_map.get(baseline, {}).get("applicable") is True, (
                f"baseline category {baseline!r} must always be applicable"
            )

    def test_transport_safety_for_bus(self):
        from src.nodes.risk_category_classification_node import RiskCategoryClassificationNode

        node = RiskCategoryClassificationNode()
        result = node(_make_state(transport_mode="bus"))
        cat_map = result.get("risk_category_map", {})
        assert cat_map.get("transport_safety", {}).get("applicable") is True

    def test_weather_outdoor_for_beach(self):
        from src.nodes.risk_category_classification_node import RiskCategoryClassificationNode

        node = RiskCategoryClassificationNode()
        result = node(_make_state(destination_type="beach", transport_mode="walking",
                                  activity_types=["observation"]))
        cat_map = result.get("risk_category_map", {})
        assert cat_map.get("weather_outdoor", {}).get("applicable") is True

    def test_unknown_value_uses_fallback(self):
        from src.nodes.risk_category_classification_node import RiskCategoryClassificationNode

        node = RiskCategoryClassificationNode()
        result = node(_make_state(destination_type="alien_planet"))
        unknowns = result.get("classification_unknowns", [])
        assert any("alien_planet" in u for u in unknowns)

    def test_no_branches_always_returns_success(self):
        from src.nodes.risk_category_classification_node import RiskCategoryClassificationNode

        node = RiskCategoryClassificationNode()
        for dest in ("museum", "park", "mountain", "factory"):
            result = node(_make_state(destination_type=dest))
            assert result.get("status") in ("success", "SUCCESS"), (
                f"classification must always succeed for destination {dest!r}"
            )

    def test_multi_activity_union(self):
        from src.nodes.risk_category_classification_node import RiskCategoryClassificationNode

        node = RiskCategoryClassificationNode()
        result = node(_make_state(destination_type="park", transport_mode="walking",
                                  activity_types=["hiking", "swimming"]))
        cat_map = result.get("risk_category_map", {})
        assert cat_map.get("weather_outdoor", {}).get("applicable") is True
        assert cat_map.get("supervision_ratio", {}).get("applicable") is True
