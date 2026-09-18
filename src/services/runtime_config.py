"""runtime_config — configuration-driven rule tables and validation bounds.

This module is the single source of truth for the citizen-developer-tunable
configuration of EDU-C2-045: the risk-classification rule table, checklist item
templates, allowed source schemes, and retrieval/validation bounds. The baked-in
``DEFAULTS`` guarantee deterministic behaviour with no external file; an optional
``config/config.yaml`` may override any top-level key at deploy time (operation
guide §Rule-table configuration).

Nothing here is a secret — credentials are resolved at runtime via
``ctx.secrets.require()`` in the nodes, never from this module.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

# ── Canonical risk categories (issue #5) ─────────────────────────────────
RISK_CATEGORIES = (
    "weather_outdoor",
    "transport_safety",
    "health_medical",
    "venue_capacity",
    "emergency_protocol",
    "insurance",
    "supervision_ratio",
    "hygiene",
)

# Safety-first precedence: lower number = surfaced earlier in the brief.
# health_medical is first so unresolved medical items are always prominent.
CATEGORY_PRECEDENCE = {
    "health_medical": 0,
    "emergency_protocol": 1,
    "transport_safety": 2,
    "weather_outdoor": 3,
    "venue_capacity": 4,
    "supervision_ratio": 5,
    "hygiene": 6,
    "insurance": 7,
}

DEFAULTS: Dict[str, Any] = {
    # ── Input validation bounds (issue #2, #3) ───────────────────────────
    "max_request_bytes": 16384,
    "max_student_count": 2000,
    "allowed_transport_modes": ["bus", "train", "walking", "coach", "ferry", "car", "subway"],
    "allowed_destination_types": [
        "museum",
        "park",
        "beach",
        "mountain",
        "factory",
        "farm",
        "aquarium",
        "zoo",
        "science_center",
        "historical_site",
        "sports_facility",
    ],
    "allowed_activity_types": [
        "hiking",
        "swimming",
        "cycling",
        "observation",
        "workshop",
        "sports",
        "cooking",
        "boating",
        "climbing",
        "sightseeing",
    ],
    # ── Source scheme allowlist (issue #2, #4, #6) ───────────────────────
    "allowed_school_source_schemes": ["schooldata", "https"],
    "allowed_guidance_source_schemes": ["qdrant", "https"],
    # Approved source references (per-deployment; overridable in config.yaml).
    "school_data_source_ref": "schooldata://approved-school-records",
    "school_data_secret_key": "SCHOOL_DATA_TOKEN",
    "school_data_page_limit": 50,
    "guidance_source_ref": "qdrant://excursion_guidance",
    "guidance_collection": "excursion_guidance",
    "guidance_secret_key": "QDRANT_API_KEY",
    # ── Classification rule table (issue #5) ─────────────────────────────
    # Categories that always apply regardless of input (baseline safety set).
    "baseline_categories": [
        "health_medical",
        "emergency_protocol",
        "insurance",
        "supervision_ratio",
    ],
    "destination_rules": {
        "museum": ["venue_capacity", "hygiene"],
        "park": ["weather_outdoor", "hygiene"],
        "beach": ["weather_outdoor", "hygiene"],
        "mountain": ["weather_outdoor"],
        "factory": ["venue_capacity"],
        "farm": ["hygiene", "weather_outdoor"],
        "aquarium": ["venue_capacity"],
        "zoo": ["hygiene", "weather_outdoor"],
        "science_center": ["venue_capacity"],
        "historical_site": ["venue_capacity", "weather_outdoor"],
        "sports_facility": ["venue_capacity"],
    },
    "transport_rules": {
        "bus": ["transport_safety"],
        "coach": ["transport_safety"],
        "train": ["transport_safety"],
        "subway": ["transport_safety"],
        "ferry": ["transport_safety", "weather_outdoor"],
        "walking": ["transport_safety", "weather_outdoor"],
        "car": ["transport_safety"],
    },
    "activity_rules": {
        "hiking": ["weather_outdoor"],
        "swimming": ["weather_outdoor", "supervision_ratio"],
        "cycling": ["transport_safety", "weather_outdoor"],
        "observation": [],
        "workshop": ["venue_capacity"],
        "sports": ["supervision_ratio"],
        "cooking": ["hygiene"],
        "boating": ["weather_outdoor", "supervision_ratio"],
        "climbing": ["weather_outdoor", "supervision_ratio"],
        "sightseeing": [],
    },
    # Category applied whenever an input value is unknown (unknown-value fallback).
    "unknown_fallback_categories": ["emergency_protocol"],
    # ── Checklist item templates (issue #7) ──────────────────────────────
    # evidence_required=True means the item cannot be COMPLETE without confirmed
    # evidence in state; otherwise it defaults to REQUIRES_REVIEW.
    "checklist_templates": {
        "health_medical": [
            {"id": "hm-1", "title": "Aggregate medical-needs summary confirmed", "evidence_required": True},
            {"id": "hm-2", "title": "First-aid provisioning matches group health profile", "evidence_required": True},
        ],
        "emergency_protocol": [
            {"id": "ep-1", "title": "Emergency contact and evacuation plan documented", "evidence_required": True},
            {"id": "ep-2", "title": "Nearest medical facility identified for destination", "evidence_required": False},
        ],
        "transport_safety": [
            {"id": "ts-1", "title": "Transport operator safety credentials on file", "evidence_required": False},
            {"id": "ts-2", "title": "Boarding/headcount procedure defined", "evidence_required": False},
        ],
        "weather_outdoor": [
            {"id": "wo-1", "title": "Weather contingency / cancellation criteria set", "evidence_required": False},
        ],
        "venue_capacity": [
            {"id": "vc-1", "title": "Venue capacity and group booking confirmed", "evidence_required": False},
        ],
        "supervision_ratio": [
            {"id": "sr-1", "title": "Supervision ratio meets policy for group size", "evidence_required": False},
        ],
        "hygiene": [
            {"id": "hy-1", "title": "Hand-hygiene / food-handling arrangements checked", "evidence_required": False},
        ],
        "insurance": [
            {"id": "in-1", "title": "Excursion insurance coverage valid for dates", "evidence_required": False},
        ],
    },
    # ── Retrieval bounds (issue #6) ──────────────────────────────────────
    "rag_top_k": 5,
    "rag_ranking_cutoff": 0.35,
    "rag_partial_coverage_threshold": 0.6,  # coverage ratio below → PARTIAL
    "corpus_staleness_threshold_days": 120,  # quarterly refresh expectation
    # ── LLM (narrative only, issue #7) ───────────────────────────────────
    "llm_narrative_timeout_s": 15,
    "budget_usd": None,
}


def _config_path() -> Path:
    # src/services/runtime_config.py -> project root is parents[2]
    return Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def load_config() -> Dict[str, Any]:
    """Return DEFAULTS merged with optional config/config.yaml overrides.

    yaml is optional: if pyyaml is unavailable or the file is missing/invalid,
    the baked-in DEFAULTS are returned unchanged so behaviour stays deterministic.
    """
    config: Dict[str, Any] = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in DEFAULTS.items()}
    path = _config_path()
    if not path.exists():
        return config
    try:
        import yaml  # optional runtime override layer

        overrides = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return config
    runtime = overrides.get("runtime", overrides) if isinstance(overrides, dict) else {}
    if isinstance(runtime, dict):
        for key, value in runtime.items():
            if key in config:
                config[key] = value
    return config


def mock_mode() -> bool:
    """STG smoke / offline mode flag (non-secret). Honoured by the service layer
    so the STG first-invoke evidence can PASS without wiring real connectors."""
    return os.environ.get("STG_MOCK_MODE", "").strip().lower() in ("1", "true", "yes")
