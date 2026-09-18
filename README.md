# EDU-C2-045 — SchoolExcursionRiskAssessmentAgent

> **Category**: Cat 2 (orchestrates a multi-step, use-case-specific workflow)
> **Industry**: EDU
> **Framework**: L1-direct `framework/` with `shared/` composition
> **Inherits**: `AgentBaseGraph` (not `DocGenerationAgent`)
> **Status**: Draft

## Overview

SchoolExcursionRiskAssessmentAgent is a decision-support aid for school staff
preparing an excursion. It runs a strictly linear six-step pipeline and produces a
dual-section brief for **human review** — an administrator risk checklist and a
parent-communication draft. It never issues formal approval, never sends parent
messages, never writes back to source systems, and makes no autonomous risk
decision.
 
```
Step 1  InputValidationNode          validate schema · S-2 gate · reject student health data · mask financial
Step 2  ExcursionHistoryRetrievalNode aggregate-only history/policy + health flags (no LLM, no individual records)
Step 3  RiskCategoryClassificationNode deterministic rule-table classification (no LLM, no branches)
Step 4  PolicyAndGuidanceRAGNode      VectorRAG over MEXT/policy/destination corpora + MEXT fallback
Step 5  RiskChecklistAssessmentNode   deterministic COMPLETE / REQUIRES_REVIEW / NOT_APPLICABLE; LLM = narrative only
Step 6  ExcursionBriefGenerationNode  dual-section Markdown + JSON · S-3 gate · mandatory approval disclaimer
```

Architecture: outer `AgentBaseGraph` (`pre_process` = Step 1, `post_process` =
Step 6, both `VERIFIED_EXTERNAL`) wrapping an inner `DomainWorkflowGraph`
(Steps 2–5, all `ANONYMOUS` — trust is verified once at the boundary).

## Data boundary

Individual student health, identity, contact, disability, consent, and financial
data are prohibited at every boundary. Only defined aggregate health/completeness
flags may flow through state. Absent or unconfirmed health completeness forces all
health/medical checklist items to `REQUIRES_REVIEW` (never `NOT_APPLICABLE`).

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"        # or: pip install -e ".[dev]"
python -m pytest tests/ -v
```

## Requirements

- Python 3.11+
- See `pyproject.toml` for pinned runtime and dev dependencies

## Project Structure

See `docs/` for the proposal, design, test specification, and operation guide.
Development rules are in the CoE-distributed `CLAUDE.md` (local reference only,
never committed).

## Status of this repository

This repository is published as a reference template. It is provided **as-is**,
with no warranty of any kind, and without any ongoing support or maintenance
commitment from the author or organization.
