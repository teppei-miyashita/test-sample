"""school_data_service — Step 2 adapter for the approved school data source.

Retrieves prior-excursion counts, incident-history codes, standing-policy codes,
and the two permitted aggregate health signals (class-level risk flag +
health-record completeness). It NEVER returns or stores individual health records:
``_normalize_adapter_result`` strips any per-student structures and keeps only
aggregate counts and the two controlled flags.

The live connector is provisioned per deployment. When it is absent (local/offline)
or times out, the service raises ``SchoolDataUnavailable`` so the node can emit a
safe degraded / review-required state instead of guessing.
"""

from __future__ import annotations

from typing import Any, Dict

from src.services.runtime_config import load_config, mock_mode

_ALLOWED_HEALTH_FLAGS = {"NONE", "PRESENT", "UNKNOWN"}
_ALLOWED_COMPLETENESS = {"COMPLETE", "INCOMPLETE", "UNKNOWN"}

# Fields that, if present in a raw adapter payload, indicate individual-level data
# and MUST be dropped rather than propagated.
_INDIVIDUAL_KEYS = {
    "students",
    "student_records",
    "names",
    "medical_records",
    "health_records",
    "contacts",
    "emails",
    "phone_numbers",
    "consent_forms",
    "individuals",
}


class SchoolDataError(Exception):
    """Base error for the school data adapter."""


class SchoolDataUnavailable(SchoolDataError):
    """Raised when the source is unreachable, times out, or is not wired."""


def _scheme_of(source_ref: str) -> str:
    return source_ref.split("://", 1)[0].lower() if "://" in source_ref else ""


def _normalize_adapter_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Enforce the aggregate-only contract on a raw adapter payload.

    Permitted transformations (issue #4): keep aggregate counts, coded incident /
    policy flags, the class-level aggregate health risk flag, and the health-record
    completeness flag. Everything individual is discarded.
    """
    raw = raw or {}
    health_flag = str(raw.get("aggregate_health_risk_flag", "UNKNOWN")).upper()
    if health_flag not in _ALLOWED_HEALTH_FLAGS:
        health_flag = "UNKNOWN"
    completeness = str(raw.get("health_record_completeness", "UNKNOWN")).upper()
    if completeness not in _ALLOWED_COMPLETENESS:
        completeness = "UNKNOWN"

    summary_in = raw.get("excursion_history_summary", {}) or {}
    summary = {
        "prior_excursions": int(summary_in.get("prior_excursions", 0) or 0),
        "destinations_visited": int(summary_in.get("destinations_visited", 0) or 0),
    }

    def _codes(seq: Any) -> list[str]:
        if not isinstance(seq, (list, tuple)):
            return []
        return [str(x) for x in seq if isinstance(x, (str, int)) and str(x)]

    return {
        "excursion_history_summary": summary,
        "incident_history_flags": _codes(raw.get("incident_history_flags")),
        "standing_policy_flags": _codes(raw.get("standing_policy_flags")),
        "aggregate_health_risk_flag": health_flag,
        "health_record_completeness": completeness,
        # dropped-on-purpose evidence (aids tests / audit that no individual keys survive)
        "dropped_individual_keys": sorted(k for k in raw if k in _INDIVIDUAL_KEYS),
    }


def _synthetic_raw(destination_type: str) -> Dict[str, Any]:
    """Deterministic, non-personal synthetic payload for mock / STG smoke mode."""
    return {
        "excursion_history_summary": {"prior_excursions": 3, "destinations_visited": 2},
        "incident_history_flags": ["minor_delay", "weather_reschedule"],
        "standing_policy_flags": ["ratio_policy_v2", "allergy_awareness_policy"],
        "aggregate_health_risk_flag": "PRESENT",
        "health_record_completeness": "INCOMPLETE",
    }


def fetch_excursion_history(
    source_ref: str,
    credential_handle: str,
    *,
    destination_type: str = "",
    limit: int = 50,
    timeout_s: int = 10,
    mock: bool = False,
) -> Dict[str, Any]:
    """Fetch and normalize aggregate excursion history from the approved source.

    Raises ``SchoolDataError`` for a disallowed source scheme and
    ``SchoolDataUnavailable`` when no live connector is wired or a call times out.
    """
    config = load_config()
    scheme = _scheme_of(source_ref)
    if not source_ref or scheme not in config["allowed_school_source_schemes"]:
        raise SchoolDataError(f"disallowed or missing school source scheme: {scheme or '(none)'}")
    if not credential_handle:
        raise SchoolDataUnavailable("no credential handle resolved for school data source")

    if mock or mock_mode():
        result = _normalize_adapter_result(_synthetic_raw(destination_type))
        result["source_provenance"] = {
            "scheme": scheme,
            "source_id": source_ref.split("://", 1)[-1],
            "mode": "mock",
            "limit": int(limit),
        }
        result["status"] = "OK"
        return result

    # Live connector is provisioned per deployment; absent one we degrade safely
    # rather than fabricate history.
    raise SchoolDataUnavailable(
        "school data connector is not wired in this environment; "
        "provision the approved adapter per the operation guide"
    )
