from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from eval.run_eval import (
    DIMENSION_WEIGHTS,
    context_diagnostic_cases,
    validate_context_diagnostic_record,
    DEFAULT_FREEZE_PATH,
    FREEZE_VERSION,
    EVALUATION_METHOD_VERSION,
    validate_expression_metrics,
    load_mode_cases,
    RESULT_VERSION,
    STAGE7_ACCEPTANCE_TARGETS,
)


_REQUIRED_FIELDS = (
    "result_version",
    "case_id",
    "run_index",
    "mode",
    "status",
    "error_code",
    "model",
    "prompt_version",
    "schema_version",
    "data_version",
    "code_version",
    "provider_calls",
    "usage",
    "metrics",
)
_INPUT_CNY_PER_MILLION_TOKENS = 1.0
_OUTPUT_CNY_PER_MILLION_TOKENS = 4.0
DEFAULT_REPORT_INPUT = _PROJECT_ROOT / "reports" / "smoke_results.jsonl"


def load_results(input_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    keys: set[tuple[str, int]] = set()
    with input_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise ValueError(f"blank JSONL record at line {line_number}")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL record at line {line_number}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"JSONL record must be an object at line {line_number}")
            missing = [field for field in _REQUIRED_FIELDS if field not in record]
            if missing:
                raise ValueError(f"missing result fields at line {line_number}")
            if record["result_version"] != RESULT_VERSION:
                raise ValueError(f"unsupported result_version at line {line_number}")
            case_id = record["case_id"]
            run_index = record["run_index"]
            if not isinstance(case_id, str) or not case_id:
                raise ValueError(f"invalid case_id at line {line_number}")
            if (
                not isinstance(run_index, int)
                or isinstance(run_index, bool)
                or run_index < 0
            ):
                raise ValueError(f"invalid run_index at line {line_number}")
            key = (case_id, run_index)
            if key in keys:
                raise ValueError(f"duplicate result key at line {line_number}")
            keys.add(key)
            validate_expression_metrics(record)
            records.append(record)
    return records


def summarize_results(records: list[dict[str, Any]]) -> dict[str, Any]:
    if any("revision_diagnostic" in r.get("metrics", {}) for r in records):
        from eval.run_eval import REVISION_DIAGNOSTIC_CASE_IDS, validate_revision_diagnostic_record

        rows = []
        plans, keys = set(), set()
        for record in records:
            d = validate_revision_diagnostic_record(record)
            key = (record["case_id"], record["run_index"])
            if key in keys:
                raise ValueError("invalid revision diagnostic")
            keys.add(key)
            plans.add((d["plan_sha256"], record["code_version"], d["diagnostic_id"]))
            rows.append({"case_id": record["case_id"], "status": record["status"],
                         "error_code": record["error_code"], "provider_calls": record["provider_calls"],
                         "usage": record["usage"], **d})
        if len(plans) != 1:
            raise ValueError("invalid revision diagnostic")
        return {"revision_diagnostic": sorted(rows, key=lambda row: row["case_id"]),
                "missing_slots": sorted(set(REVISION_DIAGNOSTIC_CASE_IDS) - {row["case_id"] for row in rows}),
                "acceptance_gates": {"status": "not_available", "target_source": "diagnostic_only", "targets": {}, "gates": {}}}
    for record in records:
        validate_expression_metrics(record)
    if any(isinstance(record.get("metrics"), dict) and "context_diagnostic" in record["metrics"] for record in records):
        return _summarize_context_diagnostic(records)
    status_counts = dict(sorted(Counter(record["status"] for record in records).items()))
    mode_counts = dict(sorted(Counter(record["mode"] for record in records).items()))
    known_calls = [
        record["provider_calls"]
        for record in records
        if isinstance(record["provider_calls"], int)
        and not isinstance(record["provider_calls"], bool)
    ]
    usage_fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    usage = {
        field: sum(
            record["usage"][field]
            for record in records
            if isinstance(record.get("usage"), dict)
            and isinstance(record["usage"].get(field), int)
            and not isinstance(record["usage"].get(field), bool)
        )
        for field in usage_fields
    }
    usage_unknown_records = {
        field: sum(
            1
            for record in records
            if not isinstance(record.get("usage"), dict)
            or record["usage"].get(field) is None
        )
        for field in usage_fields
    }
    cost_is_known = all(
        usage_unknown_records[field] == 0
        for field in ("prompt_tokens", "completion_tokens")
    )
    estimated_cost_cny = (
        round(
            (
                usage["prompt_tokens"] * _INPUT_CNY_PER_MILLION_TOKENS
                + usage["completion_tokens"] * _OUTPUT_CNY_PER_MILLION_TOKENS
            )
            / 1_000_000,
            6,
        )
        if cost_is_known
        else None
    )
    quality = _summarize_quality(records)
    known_error_detection = _summarize_known_error_detection(records)
    quality_calibration_diagnostics = _summarize_quality_calibration_diagnostics(
        records
    )
    attacks = _summarize_attacks(records)
    stability = _summarize_stability(records)
    revisions = _summarize_revisions(records)
    citation = _summarize_citation(records)
    freeze = _load_freeze_state(records)
    expression_coverage = _expression_method_coverage(records, freeze)
    return {
        "attempted": len(records),
        "status_counts": status_counts,
        "mode_counts": mode_counts,
        "provider_calls_known": sum(known_calls),
        "provider_calls_unknown_records": len(records) - len(known_calls),
        "usage": usage,
        "usage_unknown_records": usage_unknown_records,
        "estimated_cost_cny": estimated_cost_cny,
        "modes": sorted({record["mode"] for record in records}),
        "models": sorted({record["model"] for record in records}),
        "prompt_versions": sorted(
            {record["prompt_version"] for record in records}
        ),
        "schema_versions": sorted(
            {record["schema_version"] for record in records}
        ),
        "data_versions": sorted({record["data_version"] for record in records}),
        "code_versions": sorted({record["code_version"] for record in records}),
        "sample_scale": _summarize_sample_scale(records),
        "sample_selection": _load_sample_selection(records),
        "quality": quality,
        "known_error_detection": known_error_detection,
        "quality_calibration_diagnostics": quality_calibration_diagnostics,
        "citation": citation,
        "attacks": attacks,
        "stability": stability,
        "revisions": revisions,
        "freeze": freeze,
        "expression_audit": expression_coverage,
        "acceptance_gates": _summarize_acceptance_gates(
            quality=quality,
            known_error_detection=known_error_detection,
            citation=citation,
            attacks=attacks,
            stability=stability,
            stability_coverage_complete=_stability_coverage_complete(records),
            revisions=revisions,
            freeze=freeze,
            expression_coverage=expression_coverage,
        ),
    }


def _summarize_context_diagnostic(records: list[dict[str, Any]]) -> dict[str, Any]:
    plan = validate_context_diagnostic_record(records[0])
    cases = context_diagnostic_cases(plan)
    by_key, primary, rows = {}, {c["case_id"]: [] for c in plan["contexts"]}, []
    retry_successes = unverified_successes = 0
    for record in records:
        if validate_context_diagnostic_record(record) != plan:
            raise ValueError("invalid context diagnostic")
        key = (record["case_id"], record["run_index"])
        if key in by_key:
            raise ValueError("invalid context diagnostic")
        by_key[key] = record
    # Reuse existing successful known-error validation, without computing gates.
    _summarize_known_error_detection(records)
    for case in cases:
        record = by_key.get((case.case_id, case.run_index))
        if record is None:
            continue
        metrics = record["metrics"]
        state, score, conclusion_level = None, None, None
        verified = metrics["context_diagnostic"].get("verified_first_request_sha256")
        is_primary = record["status"] == "succeeded" and record["provider_calls"] == 1 and verified is not None
        if record["status"] == "succeeded":
            if metrics.get("case_group") != "quality" or metrics.get("paper_id") != case.payload["paper_id"] or metrics.get("quality_label") != case.payload["quality_label"]:
                raise ValueError("invalid context diagnostic")
            diagnostics = _validated_key_claim_diagnostics(metrics)
            selected = [d for d in diagnostics or [] if d["claim_id"] == plan["target_claim_id"] and d["block_id"] == plan["target_block_id"]]
            if len(selected) != 1:
                raise ValueError("invalid context diagnostic")
            item = selected[0]
            fields = ("relation", "scope_status", "terminology_status", "severity")
            if any(item[field] is None for field in fields):
                raise ValueError("invalid context diagnostic")
            state = "/".join([item[field] for field in fields] + [",".join(item["deterministic_issue_codes"]) or "NONE"])
            score = metrics.get("overall_score")
            points = metrics.get("dimension_points")
            if type(score) not in (float, int) or not math.isfinite(score) or not 0 <= score <= 100:
                raise ValueError("invalid context diagnostic")
            if not isinstance(points, dict) or set(points) != {d.value for d in DIMENSION_WEIGHTS} or any(type(p) is not int or not 0 <= p <= 4 for p in points.values()):
                raise ValueError("invalid context diagnostic")
            conclusion_level = points["conclusion_limitations"]
            if is_primary:
                primary[case.case_id].append(state)
            if record["provider_calls"] > 1:
                retry_successes += 1
            if verified is None:
                unverified_successes += 1
        elif "key_claim_diagnostics" in metrics:
            raise ValueError("invalid context diagnostic")
        rows.append({
            "case_id": case.case_id, "run_index": case.run_index,
            "status": record["status"], "error_code": record["error_code"],
            "provider_calls": record["provider_calls"], "primary": is_primary,
            "request_verification": "verified" if verified is not None else "not_verified",
            "verified_first_request_sha256": verified,
            "judgment": state, "overall_score": score,
            "conclusion_level": conclusion_level,
        })
    within, between = [], []
    context_ids = list(primary)
    for case_id, states in primary.items():
        comparisons = len(states) * (len(states) - 1) // 2
        disagreements = sum(a != b for i, a in enumerate(states) for b in states[i + 1:])
        scores = [r["overall_score"] for r in rows if r["case_id"] == case_id and r["primary"]]
        within.append({
            "case_id": case_id, "primary_records": len(states),
            "frequencies": dict(sorted(Counter(states).items())),
            "disagreements": disagreements, "comparisons": comparisons,
            "disagreement_rate": disagreements / comparisons if comparisons else None,
            "score_min": min(scores) if scores else None,
            "score_max": max(scores) if scores else None,
        })
    for i, left in enumerate(context_ids):
        for right in context_ids[i + 1:]:
            comparisons = len(primary[left]) * len(primary[right])
            disagreements = sum(a != b for a in primary[left] for b in primary[right])
            between.append({
                "left": left, "right": right, "disagreements": disagreements,
                "comparisons": comparisons,
                "disagreement_rate": disagreements / comparisons if comparisons else None,
            })
    primary_count = sum(len(values) for values in primary.values())
    calls = [r["provider_calls"] for r in records if r["provider_calls"] is not None]
    usage = {field: sum(r["usage"][field] or 0 for r in records) for field in ("prompt_tokens", "completion_tokens", "total_tokens")}
    usage_unknown = {field: sum(r["usage"][field] is None for r in records) for field in usage}
    return {
        "attempted": len(records),
        "provider_calls_known": sum(calls),
        "provider_calls_unknown_records": len(records) - len(calls),
        "usage": usage, "usage_unknown_records": usage_unknown,
        "estimated_cost_cny": (
            round((usage["prompt_tokens"] * _INPUT_CNY_PER_MILLION_TOKENS + usage["completion_tokens"] * _OUTPUT_CNY_PER_MILLION_TOKENS) / 1_000_000, 6)
            if len(calls) == len(records) == 9 and not usage_unknown["prompt_tokens"] and not usage_unknown["completion_tokens"] else None
        ),
        "context_diagnostic": {
            "plan": plan, "plan_sha256": records[0]["metrics"]["context_diagnostic"]["plan_sha256"],
            "status": "present" if primary_count == 9 else "partial" if primary_count else "not_available",
            "planned_slots": 9, "recorded_slots": len(records),
            "missing_slots": 9 - len(records), "primary_records": primary_count,
            "retry_success_records": retry_successes,
            "unverified_success_records": unverified_successes,
            "status_counts": dict(sorted(Counter(r["status"] for r in records).items())),
            "rows": rows, "within": within, "between": between,
        },
        "acceptance_gates": {
            "status": "not_available", "target_source": "diagnostic_only",
            "targets": {}, "gates": {},
        },
    }


def _summarize_sample_scale(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    ids: dict[str, set[str]] = {
        "quality": set(),
        "attack": set(),
        "revision": set(),
        "stability": set(),
    }
    for record in records:
        metrics = record.get("metrics")
        group = metrics.get("case_group") if isinstance(metrics, dict) else None
        if not isinstance(group, str):
            group = record["case_id"].split(":", 1)[0]
        if group not in ids:
            continue
        counts[group] += 1
        if group == "quality":
            paper_match = re.fullmatch(r"quality:([^:]+):(good|medium|bad)", record["case_id"])
            if paper_match:
                ids[group].add(paper_match.group(1))
                continue
            ids[group].add(
                metrics.get("paper_id")
                if isinstance(metrics, dict) and isinstance(metrics.get("paper_id"), str)
                else record["case_id"].rsplit(":", 1)[0]
            )
        elif group == "attack":
            attack_match = re.fullmatch(r"attack:([^:]+):(attack|clean)", record["case_id"])
            if attack_match:
                ids[group].add(attack_match.group(1))
                continue
            ids[group].add(
                metrics.get("attack_id")
                if isinstance(metrics, dict) and isinstance(metrics.get("attack_id"), str)
                else record["case_id"]
            )
        elif group == "revision":
            ids[group].add(record["case_id"])
        else:
            ids[group].add(record["case_id"])
    return {
        "quality_records": counts["quality"],
        "quality_paper_count": len(ids["quality"]),
        "attack_records": counts["attack"],
        "attack_pair_id_count": len(ids["attack"]),
        "revision_records": counts["revision"],
        "stability_records": counts["stability"],
        "stability_output_count": len(ids["stability"]),
    }


def _load_sample_selection(records: list[dict[str, Any]]) -> dict[str, Any]:
    if "paperlens-plos-abstracts-v1" not in {
        record["data_version"] for record in records
    }:
        return {"status": "not_live_data"}
    manifest_path = _PROJECT_ROOT / "eval" / "live_cases.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unavailable"}
    papers = manifest.get("papers")
    if not isinstance(papers, list):
        return {"status": "invalid"}
    selected = []
    for paper in papers:
        if not isinstance(paper, dict):
            return {"status": "invalid"}
        selected.append(
            {
                "paper_id": paper.get("paper_id"),
                "split": paper.get("split"),
                "doi": paper.get("doi"),
                "license_name": paper.get("license_name"),
                "source_url": paper.get("source_url"),
                "license_evidence_url": paper.get("license_evidence_url"),
            }
        )
    return {
        "status": "present",
        "paper_count": len(selected),
        "development_count": sum(item["split"] == "development" for item in selected),
        "holdout_count": sum(item["split"] == "holdout" for item in selected),
        "license_names": sorted({item["license_name"] for item in selected}),
        "papers": selected,
    }


def _summarize_quality(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, float]] = {}
    for record in records:
        metrics = _succeeded_metrics(record)
        if metrics is None or metrics.get("case_group") != "quality":
            continue
        paper_id = _metric_string(metrics, "paper_id")
        quality_label = _metric_string(metrics, "quality_label")
        if quality_label not in {"good", "medium", "bad"}:
            raise ValueError("quality_label must be good, medium, or bad")
        score = _metric_number(metrics, "overall_score")
        paper_scores = groups.setdefault(paper_id, {})
        if quality_label in paper_scores:
            raise ValueError("duplicate quality label within a paper group")
        paper_scores[quality_label] = score

    complete_groups = 0
    strict_order_correct = 0
    pairwise_correct = 0
    pairwise_total = 0
    expected_ranks: list[float] = []
    observed_scores: list[float] = []
    for scores in groups.values():
        if set(scores) != {"good", "medium", "bad"}:
            continue
        complete_groups += 1
        good = scores["good"]
        medium = scores["medium"]
        bad = scores["bad"]
        if good > medium > bad:
            strict_order_correct += 1
        pairwise_correct += sum((good > medium, good > bad, medium > bad))
        pairwise_total += 3
        expected_ranks.extend((3.0, 2.0, 1.0))
        observed_scores.extend((good, medium, bad))
    return {
        "complete_groups": complete_groups,
        "strict_order_correct": strict_order_correct,
        "pairwise_correct": pairwise_correct,
        "pairwise_total": pairwise_total,
        "spearman_rank_correlation": _spearman_rank_correlation(
            expected_ranks,
            observed_scores,
        ),
    }


_DIAGNOSTIC_FIELDS = frozenset(
    {
        "claim_id",
        "block_id",
        "relation",
        "scope_status",
        "terminology_status",
        "severity",
        "deterministic_issue_codes",
    }
)
_DIAGNOSTIC_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
_DIAGNOSTIC_RELATIONS = frozenset({"supports", "contradicts", "insufficient"})
_DIAGNOSTIC_SCOPE_STATUSES = frozenset({"preserved", "expanded", "unclear"})
_DIAGNOSTIC_TERMINOLOGY_STATUSES = frozenset({"correct", "misused", "unclear"})
_DIAGNOSTIC_SEVERITIES = frozenset({"none", "minor", "major", "critical"})
_DIAGNOSTIC_CODES = frozenset(
    {
        "CANDIDATE_BLOCK_NOT_FOUND",
        "CANDIDATE_QUOTE_MISSING",
        "CANDIDATE_QUOTE_NOT_FOUND",
        "NUMBER_MISMATCH",
        "UNIT_MISMATCH",
        "NEGATION_MISMATCH",
        "COMPARISON_DIRECTION_MISMATCH",
        "INSUFFICIENT_EVIDENCE",
        "NON_AUDITABLE",
    }
)
_MISSING = object()


def _invalid_key_claim_diagnostics() -> None:
    raise ValueError("invalid key_claim_diagnostics")


def _validated_key_claim_diagnostics(
    metrics: dict[str, Any],
) -> list[dict[str, Any]] | None:
    value = metrics.get("key_claim_diagnostics", _MISSING)
    if value is _MISSING:
        return None
    key_claim_count = metrics.get("key_claim_count", _MISSING)
    accuracy_denominator = metrics.get(
        "key_claim_citation_accuracy_denominator",
        _MISSING,
    )
    completeness_denominator = metrics.get(
        "key_claim_citation_completeness_denominator",
        _MISSING,
    )
    counts = (key_claim_count, accuracy_denominator, completeness_denominator)
    if any(
        not isinstance(count, int) or isinstance(count, bool) or count < 0
        for count in counts
    ):
        _invalid_key_claim_diagnostics()
    if completeness_denominator != key_claim_count:
        _invalid_key_claim_diagnostics()
    if not isinstance(value, list) or len(value) > 256:
        _invalid_key_claim_diagnostics()
    if len(value) != accuracy_denominator:
        _invalid_key_claim_diagnostics()
    if key_claim_count == 0 and value:
        _invalid_key_claim_diagnostics()
    normalized: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    seen_claim_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != _DIAGNOSTIC_FIELDS:
            _invalid_key_claim_diagnostics()
        claim_id = item.get("claim_id")
        block_id = item.get("block_id")
        if (
            not isinstance(claim_id, str)
            or not _DIAGNOSTIC_IDENTIFIER.fullmatch(claim_id)
            or (
                block_id is not None
                and (
                    not isinstance(block_id, str)
                    or not _DIAGNOSTIC_IDENTIFIER.fullmatch(block_id)
                )
            )
        ):
            _invalid_key_claim_diagnostics()
        seen_claim_ids.add(claim_id)
        relation = item.get("relation")
        scope_status = item.get("scope_status")
        terminology_status = item.get("terminology_status")
        severity = item.get("severity")
        if relation is not None and (
            not isinstance(relation, str) or relation not in _DIAGNOSTIC_RELATIONS
        ):
            _invalid_key_claim_diagnostics()
        if scope_status is not None and (
            not isinstance(scope_status, str)
            or scope_status not in _DIAGNOSTIC_SCOPE_STATUSES
        ):
            _invalid_key_claim_diagnostics()
        if (
            terminology_status is not None
            and (
                not isinstance(terminology_status, str)
                or terminology_status not in _DIAGNOSTIC_TERMINOLOGY_STATUSES
            )
        ):
            _invalid_key_claim_diagnostics()
        if severity is not None and (
            not isinstance(severity, str) or severity not in _DIAGNOSTIC_SEVERITIES
        ):
            _invalid_key_claim_diagnostics()
        codes = item.get("deterministic_issue_codes")
        if (
            not isinstance(codes, list)
            or len(codes) > 32
            or any(
                not isinstance(code, str) or code not in _DIAGNOSTIC_CODES
                for code in codes
            )
            or len(codes) != len(set(codes))
        ):
            _invalid_key_claim_diagnostics()
        pair = (claim_id, block_id or "")
        if pair in seen_pairs:
            _invalid_key_claim_diagnostics()
        seen_pairs.add(pair)
        normalized.append(
            {
                "claim_id": claim_id,
                "block_id": block_id,
                "relation": relation,
                "scope_status": scope_status,
                "terminology_status": terminology_status,
                "severity": severity,
                "deterministic_issue_codes": sorted(codes),
            }
        )
    if len(seen_claim_ids) != key_claim_count:
        _invalid_key_claim_diagnostics()
    return sorted(
        normalized,
        key=lambda item: (item["claim_id"], item["block_id"] or ""),
    )


def _summarize_quality_calibration_diagnostics(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    quality_record_count = 0
    available_record_count = 0
    rows: list[dict[str, Any]] = []
    for record in records:
        metrics = _succeeded_metrics(record)
        if metrics is None or metrics.get("case_group") != "quality":
            continue
        quality_record_count += 1
        diagnostics = _validated_key_claim_diagnostics(metrics)
        if diagnostics is None:
            continue
        available_record_count += 1
        non_supported = sorted(
            {
                item["claim_id"]
                for item in diagnostics
                if item["relation"] != "supports"
            }
        )
        deterministic_codes = sorted(
            {
                code
                for item in diagnostics
                for code in item["deterministic_issue_codes"]
            }
        )
        hard_failure_count = metrics.get("hard_failure_count")
        if (
            not isinstance(hard_failure_count, int)
            or isinstance(hard_failure_count, bool)
            or hard_failure_count < 0
        ):
            hard_failure_count = None
        decision = metrics.get("decision")
        if not isinstance(decision, str) or decision not in {
            "qualified",
            "needs_revision",
            "unqualified",
            "pending_deep_audit",
        }:
            decision = None
        score = metrics.get("overall_score")
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(float(score))
        ):
            score = None
        rows.append(
            {
                "paper_id": _metric_string(metrics, "paper_id"),
                "quality_label": _metric_string(metrics, "quality_label"),
                "overall_score": float(score) if score is not None else None,
                "decision": decision,
                "hard_failure_count": hard_failure_count,
                "non_supported_key_claim_ids": non_supported,
                "deterministic_issue_codes": deterministic_codes,
            }
        )
    if not quality_record_count or not available_record_count:
        status = "not_available"
    elif available_record_count < quality_record_count:
        status = "partial"
    else:
        status = "present"
    rows.sort(key=lambda row: (row["paper_id"], row["quality_label"]))
    return {"status": status, "rows": rows}


_KNOWN_ERROR_METRIC_FIELDS = frozenset(
    {
        "known_error_type",
        "known_error_severity",
        "known_error_target_sentence_id",
        "known_error_target_claim_ids",
        "known_error_detected",
    }
)
_KNOWN_ERROR_SEVERITIES = frozenset({"minor", "major", "critical"})


def _invalid_known_error_detection_metrics() -> None:
    raise ValueError("invalid known_error_detection metrics")


def _diagnostic_detects_known_error(item: dict[str, Any]) -> bool:
    return bool(item["deterministic_issue_codes"]) or (
        item["relation"] in {"contradicts", "insufficient"}
        or item["scope_status"] == "expanded"
        or item["terminology_status"] == "misused"
        or item["severity"] in {"minor", "major", "critical"}
    )


def _validated_known_error_detection(
    metrics: dict[str, Any],
) -> tuple[str, bool] | None:
    present_fields = _KNOWN_ERROR_METRIC_FIELDS.intersection(metrics)
    unexpected_fields = {
        key
        for key in metrics
        if isinstance(key, str)
        and key.startswith("known_error_")
        and key not in _KNOWN_ERROR_METRIC_FIELDS
    }
    if unexpected_fields:
        _invalid_known_error_detection_metrics()
    if not present_fields:
        return None
    if present_fields != _KNOWN_ERROR_METRIC_FIELDS:
        _invalid_known_error_detection_metrics()

    known_error_type = metrics["known_error_type"]
    severity = metrics["known_error_severity"]
    target_sentence_id = metrics["known_error_target_sentence_id"]
    target_claim_ids = metrics["known_error_target_claim_ids"]
    detected = metrics["known_error_detected"]
    if (
        not isinstance(known_error_type, str)
        or not _DIAGNOSTIC_IDENTIFIER.fullmatch(known_error_type)
        or not isinstance(severity, str)
        or severity not in _KNOWN_ERROR_SEVERITIES
        or not isinstance(target_sentence_id, str)
        or not _DIAGNOSTIC_IDENTIFIER.fullmatch(target_sentence_id)
        or not isinstance(target_claim_ids, list)
        or not target_claim_ids
        or len(target_claim_ids) > 256
        or any(
            not isinstance(claim_id, str)
            or not _DIAGNOSTIC_IDENTIFIER.fullmatch(claim_id)
            for claim_id in target_claim_ids
        )
        or target_claim_ids != sorted(set(target_claim_ids))
        or not isinstance(detected, bool)
    ):
        _invalid_known_error_detection_metrics()

    try:
        diagnostics = _validated_key_claim_diagnostics(metrics)
    except ValueError:
        _invalid_known_error_detection_metrics()
    if diagnostics is None:
        _invalid_known_error_detection_metrics()
    target_claim_id_set = set(target_claim_ids)
    diagnostic_claim_ids = {item["claim_id"] for item in diagnostics}
    if not target_claim_id_set <= diagnostic_claim_ids:
        _invalid_known_error_detection_metrics()
    detected_from_diagnostics = any(
        item["claim_id"] in target_claim_id_set
        and _diagnostic_detects_known_error(item)
        for item in diagnostics
    )
    if detected != detected_from_diagnostics:
        _invalid_known_error_detection_metrics()
    return severity, detected


def _non_succeeded_quality_label(record: dict[str, Any]) -> str | None:
    if record.get("status") not in {"failed", "timeout", "unsupported"}:
        return None
    if record.get("mode") not in {"calibrate", "final"}:
        return None
    case_id = record.get("case_id")
    if not isinstance(case_id, str):
        return None
    parts = case_id.split(":")
    if len(parts) != 3 or parts[0] != "quality" or not parts[1]:
        return None
    return parts[2] if parts[2] in {"good", "medium", "bad"} else None


def _summarize_known_error_detection(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    eligible_records = 0
    records_with_metrics = 0
    known_error_numerator = 0
    severe_error_numerator = 0
    severe_error_denominator = 0
    for record in records:
        metrics = _succeeded_metrics(record)
        if metrics is None:
            if _non_succeeded_quality_label(record) in {"medium", "bad"}:
                eligible_records += 1
            continue
        if metrics.get("case_group") != "quality":
            continue
        quality_label = _metric_string(metrics, "quality_label")
        if quality_label == "good":
            if any(
                isinstance(key, str) and key.startswith("known_error_")
                for key in metrics
            ):
                _invalid_known_error_detection_metrics()
            continue
        if quality_label not in {"medium", "bad"}:
            _invalid_known_error_detection_metrics()
        eligible_records += 1
        validated = _validated_known_error_detection(metrics)
        if validated is None:
            continue
        severity, detected = validated
        records_with_metrics += 1
        known_error_numerator += int(detected)
        if severity in {"major", "critical"}:
            severe_error_denominator += 1
            severe_error_numerator += int(detected)

    known_error_denominator = records_with_metrics
    if not records_with_metrics:
        status = "not_available"
    elif records_with_metrics < eligible_records:
        status = "partial"
    else:
        status = "present"
    return {
        "status": status,
        "records_with_metrics": records_with_metrics,
        "eligible_records": eligible_records,
        "known_error_numerator": known_error_numerator,
        "known_error_denominator": known_error_denominator,
        "known_error_detection_rate": (
            round(known_error_numerator / known_error_denominator, 4)
            if known_error_denominator
            else None
        ),
        "severe_error_numerator": severe_error_numerator,
        "severe_error_denominator": severe_error_denominator,
        "severe_error_detection_rate": (
            round(severe_error_numerator / severe_error_denominator, 4)
            if severe_error_denominator
            else None
        ),
    }


def _spearman_rank_correlation(
    expected: list[float],
    observed: list[float],
) -> float | None:
    if len(expected) != len(observed) or len(expected) < 2:
        return None

    def ranks(values: list[float]) -> list[float]:
        ordered = sorted(enumerate(values), key=lambda item: item[1])
        result = [0.0] * len(values)
        index = 0
        while index < len(ordered):
            end = index + 1
            while end < len(ordered) and ordered[end][1] == ordered[index][1]:
                end += 1
            rank = (index + 1 + end) / 2.0
            for position in range(index, end):
                result[ordered[position][0]] = rank
            index = end
        return result

    expected_rank = ranks(expected)
    observed_rank = ranks(observed)
    expected_mean = statistics.fmean(expected_rank)
    observed_mean = statistics.fmean(observed_rank)
    numerator = sum(
        (left - expected_mean) * (right - observed_mean)
        for left, right in zip(expected_rank, observed_rank)
    )
    expected_variance = sum((value - expected_mean) ** 2 for value in expected_rank)
    observed_variance = sum((value - observed_mean) ** 2 for value in observed_rank)
    denominator = math.sqrt(expected_variance * observed_variance)
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _summarize_citation(records: list[dict[str, Any]]) -> dict[str, Any]:
    quality_records = 0
    records_with_metrics = 0
    accuracy_numerator = 0
    accuracy_denominator = 0
    completeness_numerator = 0
    completeness_denominator = 0
    key_claim_count = 0
    for record in records:
        metrics = _succeeded_metrics(record)
        if metrics is None or metrics.get("case_group") != "quality":
            continue
        quality_records += 1
        fields = (
            "key_claim_count",
            "key_claim_citation_accuracy_numerator",
            "key_claim_citation_accuracy_denominator",
            "key_claim_citation_completeness_numerator",
            "key_claim_citation_completeness_denominator",
        )
        if not all(field in metrics for field in fields):
            continue
        values = [metrics[field] for field in fields]
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in values
        ):
            raise ValueError("citation count metrics must be non-negative integers")
        records_with_metrics += 1
        key_claim_count += metrics["key_claim_count"]
        accuracy_numerator += metrics["key_claim_citation_accuracy_numerator"]
        accuracy_denominator += metrics["key_claim_citation_accuracy_denominator"]
        completeness_numerator += metrics[
            "key_claim_citation_completeness_numerator"
        ]
        completeness_denominator += metrics[
            "key_claim_citation_completeness_denominator"
        ]
    return {
        "quality_records": quality_records,
        "records_with_metrics": records_with_metrics,
        "key_claim_count": key_claim_count,
        "accuracy_numerator": accuracy_numerator,
        "accuracy_denominator": accuracy_denominator,
        "accuracy_rate": (
            round(accuracy_numerator / accuracy_denominator, 4)
            if accuracy_denominator
            else None
        ),
        "completeness_numerator": completeness_numerator,
        "completeness_denominator": completeness_denominator,
        "completeness_rate": (
            round(completeness_numerator / completeness_denominator, 4)
            if completeness_denominator
            else None
        ),
    }


def _summarize_attacks(records: list[dict[str, Any]]) -> dict[str, Any]:
    attack_total = 0
    attacks_detected = 0
    clean_total = 0
    clean_false_positives = 0
    by_type: dict[str, dict[str, int]] = {}
    pair_records: dict[str, dict[str, dict[str, Any]]] = {}
    for record in records:
        metrics = _succeeded_metrics(record)
        if metrics is None or metrics.get("case_group") != "attack":
            continue
        attack_type = _metric_string(metrics, "attack_type")
        pair_role = _metric_string(metrics, "pair_role")
        detected = metrics.get("attack_detected")
        if not isinstance(detected, bool):
            raise ValueError("attack_detected must be boolean")
        attack_id = metrics.get("attack_id")
        if not isinstance(attack_id, str) or not attack_id:
            attack_id = record["case_id"]
        type_summary = by_type.setdefault(
            attack_type,
            {
                "attacks_detected": 0,
                "attack_total": 0,
                "clean_false_positives": 0,
                "clean_total": 0,
            },
        )
        if pair_role == "attack":
            attack_total += 1
            attacks_detected += int(detected)
            type_summary["attack_total"] += 1
            type_summary["attacks_detected"] += int(detected)
            pair_records.setdefault(attack_id, {})["attack"] = metrics
        elif pair_role == "clean":
            clean_total += 1
            clean_false_positives += int(detected)
            type_summary["clean_total"] += 1
            type_summary["clean_false_positives"] += int(detected)
            pair_records.setdefault(attack_id, {})["clean"] = metrics
        else:
            raise ValueError("pair_role must be attack or clean")
    pair_score_deltas: dict[str, float] = {}
    pair_dimension_deltas: dict[str, dict[str, int]] = {}
    score_deltas_by_type: dict[str, list[float]] = {}
    for attack_id, pair in pair_records.items():
        attack_metrics = pair.get("attack")
        clean_metrics = pair.get("clean")
        if attack_metrics is None or clean_metrics is None:
            continue
        if "overall_score" in attack_metrics and "overall_score" in clean_metrics:
            attack_score = _metric_number(attack_metrics, "overall_score")
            clean_score = _metric_number(clean_metrics, "overall_score")
            delta = round(attack_score - clean_score, 4)
            pair_score_deltas[attack_id] = delta
            attack_type = _metric_string(attack_metrics, "attack_type")
            score_deltas_by_type.setdefault(attack_type, []).append(delta)
        attack_points = attack_metrics.get("dimension_points")
        clean_points = clean_metrics.get("dimension_points")
        if isinstance(attack_points, dict) and isinstance(clean_points, dict):
            shared_dimensions = set(attack_points) & set(clean_points)
            if shared_dimensions:
                deltas: dict[str, int] = {}
                for dimension_id in sorted(shared_dimensions):
                    attack_point = attack_points[dimension_id]
                    clean_point = clean_points[dimension_id]
                    if (
                        isinstance(attack_point, int)
                        and not isinstance(attack_point, bool)
                        and isinstance(clean_point, int)
                        and not isinstance(clean_point, bool)
                    ):
                        deltas[dimension_id] = attack_point - clean_point
                if deltas:
                    pair_dimension_deltas[attack_id] = deltas
    mean_score_delta_by_type = {
        attack_type: round(statistics.fmean(deltas), 4)
        for attack_type, deltas in sorted(score_deltas_by_type.items())
    }
    return {
        "attacks_detected": attacks_detected,
        "attack_total": attack_total,
        "clean_false_positives": clean_false_positives,
        "clean_total": clean_total,
        "by_type": dict(sorted(by_type.items())),
        "pair_score_deltas": dict(sorted(pair_score_deltas.items())),
        "pair_dimension_deltas": dict(sorted(pair_dimension_deltas.items())),
        "mean_score_delta_by_type": mean_score_delta_by_type,
    }


def _summarize_stability(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[tuple[float, dict[str, int]]]] = {}
    for record in records:
        metrics = _succeeded_metrics(record)
        if metrics is None or metrics.get("case_group") != "stability":
            continue
        output_id = _metric_string(metrics, "stability_output_id")
        score = _metric_number(metrics, "overall_score")
        raw_points = metrics.get("dimension_points")
        if not isinstance(raw_points, dict) or not raw_points:
            raise ValueError("stability dimension_points must be a non-empty object")
        points: dict[str, int] = {}
        for dimension_id, value in raw_points.items():
            if (
                not isinstance(dimension_id, str)
                or not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= 4
            ):
                raise ValueError("invalid stability dimension point")
            points[dimension_id] = value
        groups.setdefault(output_id, []).append((score, points))

    if not groups:
        return {
            "output_count": 0,
            "run_count": 0,
            "mean_score_standard_deviation": None,
            "maximum_score_range": None,
            "dimension_level_consistency_rate": None,
        }
    standard_deviations: list[float] = []
    score_ranges: list[float] = []
    consistent_dimensions = 0
    compared_dimensions = 0
    run_count = 0
    for runs in groups.values():
        scores = [score for score, _ in runs]
        run_count += len(runs)
        standard_deviations.append(statistics.pstdev(scores))
        score_ranges.append(max(scores) - min(scores))
        dimension_ids = set(runs[0][1])
        if any(set(points) != dimension_ids for _, points in runs):
            raise ValueError("stability runs have different dimension sets")
        for dimension_id in dimension_ids:
            compared_dimensions += 1
            if len({points[dimension_id] for _, points in runs}) == 1:
                consistent_dimensions += 1
    return {
        "output_count": len(groups),
        "run_count": run_count,
        "mean_score_standard_deviation": round(
            statistics.fmean(standard_deviations),
            4,
        ),
        "maximum_score_range": round(max(score_ranges), 4),
        "dimension_level_consistency_rate": round(
            consistent_dimensions / compared_dimensions,
            4,
        ),
    }


def _stability_coverage_complete(records: list[dict[str, Any]]) -> bool:
    relevant = [
        record for record in records
        if record.get("mode") == "stability"
        or (isinstance(record.get("metrics"), dict)
            and record["metrics"].get("case_group") == "stability")
    ]
    if not any(record.get("mode") == "stability" for record in relevant):
        return False
    # A current plan must not be assumed to describe an unidentifiable old batch.
    try:
        frozen = json.loads(DEFAULT_FREEZE_PATH.read_text(encoding="utf-8"))
        manifest_bytes = (_PROJECT_ROOT / "eval" / "live_cases.json").read_bytes()
        manifest = json.loads(manifest_bytes)
    except (OSError, ValueError):
        return False
    if (
        not isinstance(frozen, dict) or not isinstance(manifest, dict)
        or frozen.get("manifest_sha256") != hashlib.sha256(manifest_bytes).hexdigest()
        or not isinstance(manifest.get("data_version"), str)
        or not manifest["data_version"]
        or frozen.get("data_version") != manifest["data_version"]
    ):
        return False
    cases = load_mode_cases("stability")
    expected = {(case.case_id, case.run_index): case.payload["stability_output_id"] for case in cases}
    outputs = set(expected.values())
    if len(expected) != 36 or len(outputs) != 12:
        raise ValueError("invalid stability plan")
    if outputs != set(manifest.get("stability_case_refs", [])):
        return False
    by_key = {}
    for record in relevant:
        key = (record["case_id"], record["run_index"])
        if key in by_key:
            raise ValueError("duplicate stability result key")
        by_key[key] = record
    if by_key.keys() != expected.keys():
        return False
    dimensions = {dimension.value for dimension in DIMENSION_WEIGHTS}
    for key, record in by_key.items():
        metrics = _succeeded_metrics(record)
        if (
            record["mode"] != "stability" or metrics is None
            or record["data_version"] != manifest["data_version"]
            or metrics.get("case_group") != "stability"
            or metrics.get("stability_output_id") != expected[key]
            or set(metrics.get("dimension_points", {})) != dimensions
        ):
            return False
    return True


def _summarize_revisions(records: list[dict[str, Any]]) -> dict[str, Any]:
    completed = 0
    known_issue_total = 0
    resolved_issue_total = 0
    new_severe_error_total = 0
    irrelevant_change_count = 0
    for record in records:
        metrics = _succeeded_metrics(record)
        if metrics is None or metrics.get("case_group") != "revision":
            continue
        completed += 1
        known = metrics.get("known_issue_count")
        resolved = metrics.get("resolved_issue_count")
        new_severe = metrics.get("new_severe_error_count")
        irrelevant = metrics.get("irrelevant_change")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (known, resolved, new_severe)
        ):
            raise ValueError("revision count metrics must be non-negative integers")
        if not isinstance(irrelevant, bool):
            raise ValueError("irrelevant_change must be boolean")
        if resolved > known:
            raise ValueError("resolved issues cannot exceed known issues")
        known_issue_total += known
        resolved_issue_total += resolved
        new_severe_error_total += new_severe
        irrelevant_change_count += int(irrelevant)
    return {
        "completed": completed,
        "known_issue_total": known_issue_total,
        "resolved_issue_total": resolved_issue_total,
        "error_resolution_rate": (
            round(resolved_issue_total / known_issue_total, 4)
            if known_issue_total
            else None
        ),
        "new_severe_error_total": new_severe_error_total,
        "irrelevant_change_count": irrelevant_change_count,
        "irrelevant_change_rate": (
            round(irrelevant_change_count / completed, 4)
            if completed
            else None
        ),
    }


def _expression_method_coverage(records: list[dict[str, Any]], freeze: dict[str, Any]) -> dict[str, Any]:
    relevant = [r for r in records if r.get("mode") != "smoke"]
    current = any(r.get("schema_version") == "deep-audit-result-v3" or "evaluation_method_version" in r.get("metrics", {}) for r in relevant)
    required = current or freeze.get("evaluation_method_version") == EVALUATION_METHOD_VERSION
    groups = {name: [] for name in ("quality", "attack", "revision", "stability")}
    for record in relevant:
        group = record.get("case_id", "").split(":", 1)[0]
        group = group if group in groups else record.get("metrics", {}).get("case_group")
        if group in groups:
            groups[group].append(record)
    valid = lambda r: (validate_expression_metrics(r)
                       and r.get("schema_version") == "deep-audit-result-v3"
                       and r.get("prompt_version") == "audit-v8")
    return {
        "status": ("present" if relevant and all(valid(r) for r in relevant) else "partial") if current else "not_available",
        "evaluation_method_version": (next(iter(methods)) if len(methods := {
            r.get("metrics", {}).get("evaluation_method_version") for r in relevant
        }) == 1 else None) if current else None,
        "required": required,
        "complete_groups": {name: bool(rows) and all(valid(r) for r in rows) for name, rows in groups.items()},
        "rows": [
            {"case_id": r["case_id"], "status": r["status"],
             "findings": r.get("metrics", {}).get("expression_findings"),
             "observed_factual_alert_count": r.get("metrics", {}).get("observed_factual_alert_count")}
            for r in relevant
        ] if current else [],
    }


def _summarize_acceptance_gates(
    *,
    quality: dict[str, Any],
    known_error_detection: dict[str, Any],
    citation: dict[str, Any],
    attacks: dict[str, Any],
    stability: dict[str, Any],
    stability_coverage_complete: bool,
    revisions: dict[str, Any],
    freeze: dict[str, Any],
    expression_coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if freeze.get("status") == "invalid":
        return {
            "status": "invalid_freeze",
            "target_source": "invalid",
            "targets": {},
            "gates": {},
        }
    if freeze.get("status") == "present":
        frozen_targets = freeze.get("acceptance_targets")
        if (
            freeze.get("freeze_version") != FREEZE_VERSION
            or freeze.get("matches_result_code_version") is not True
            or frozen_targets != STAGE7_ACCEPTANCE_TARGETS
        ):
            return {
                "status": "invalid_freeze",
                "target_source": "invalid",
                "targets": {},
                "gates": {},
            }
        targets = frozen_targets
        target_source = "frozen"
    else:
        targets = STAGE7_ACCEPTANCE_TARGETS
        target_source = "proposal_targets_not_frozen"

    gates = {
        "quality_strict_order": _acceptance_gate(
            observed=quality["strict_order_correct"],
            target=targets["quality_strict_order_min"],
            available=quality["complete_groups"] >= 5,
            denominator=5,
            operator=">=",
        ),
        "quality_pairwise": _acceptance_gate(
            observed=quality["pairwise_correct"],
            target=targets["quality_pairwise_min"],
            available=quality["pairwise_total"] >= 15,
            denominator=15,
            operator=">=",
        ),
        "severe_error_detection": _acceptance_gate(
            observed=known_error_detection["severe_error_detection_rate"],
            target=targets["severe_error_detection_min"],
            available=(
                known_error_detection["status"] == "present"
                and known_error_detection["severe_error_denominator"] >= 5
                and known_error_detection["severe_error_detection_rate"] is not None
            ),
            denominator=known_error_detection["severe_error_denominator"],
            operator=">=",
        ),
        "key_citation_accuracy": _acceptance_gate(
            observed=citation["accuracy_rate"],
            target=targets["key_citation_accuracy_min"],
            available=(
                citation["records_with_metrics"] >= 15
                and citation["accuracy_rate"] is not None
            ),
            denominator=citation["accuracy_denominator"],
            operator=">=",
        ),
        "key_citation_completeness": _acceptance_gate(
            observed=citation["completeness_rate"],
            target=targets["key_citation_completeness_min"],
            available=(
                citation["records_with_metrics"] >= 15
                and citation["completeness_rate"] is not None
            ),
            denominator=citation["completeness_denominator"],
            operator=">=",
        ),
        "stability_mean_score_sd": _acceptance_gate(
            observed=stability["mean_score_standard_deviation"],
            target=targets["stability_mean_score_sd_max"],
            available=stability_coverage_complete,
            denominator=36,
            operator="<=",
        ),
        "stability_dimension_consistency": _acceptance_gate(
            observed=stability["dimension_level_consistency_rate"],
            target=targets["stability_dimension_consistency_min"],
            available=stability_coverage_complete,
            denominator=36,
            operator=">=",
        ),
        "attack_detection": _acceptance_gate(
            observed=attacks["attacks_detected"],
            target=targets["attack_detection_min"],
            available=attacks["attack_total"] >= 16,
            denominator=16,
            operator=">=",
        ),
        "clean_false_positives": _acceptance_gate(
            observed=attacks["clean_false_positives"],
            target=targets["clean_false_positives_max"],
            available=attacks["clean_total"] >= 16,
            denominator=16,
            operator="<=",
        ),
        "revision_error_resolution": _acceptance_gate(
            observed=revisions["error_resolution_rate"],
            target=targets["revision_error_resolution_min"],
            available=revisions["completed"] >= 5
            and revisions["error_resolution_rate"] is not None,
            denominator=revisions["known_issue_total"],
            operator=">=",
        ),
        "revision_new_severe_errors": _acceptance_gate(
            observed=revisions["new_severe_error_total"],
            target=targets["revision_new_severe_errors_max"],
            available=revisions["completed"] >= 5,
            denominator=revisions["completed"],
            operator="<=",
        ),
        "revision_irrelevant_change_rate": _acceptance_gate(
            observed=revisions["irrelevant_change_rate"],
            target=targets["revision_irrelevant_change_rate_max"],
            available=revisions["completed"] >= 5
            and revisions["irrelevant_change_rate"] is not None,
            denominator=revisions["completed"],
            operator="<=",
        ),
    }
    if expression_coverage and expression_coverage["required"]:
        for name, gate in gates.items():
            group = ("stability" if name.startswith("stability_") else
                     "revision" if name.startswith("revision_") else
                     "attack" if name in {"attack_detection", "clean_false_positives"} else "quality")
            if not expression_coverage["complete_groups"][group] or freeze.get("evaluation_method_version") != EVALUATION_METHOD_VERSION:
                gate.update(status="not_available", observed=None)
    statuses = [gate["status"] for gate in gates.values()]
    if all(status == "passed" for status in statuses):
        overall_status = "passed"
    elif any(status == "failed" for status in statuses):
        overall_status = "failed"
    elif all(status == "not_available" for status in statuses):
        overall_status = "not_available"
    else:
        overall_status = "incomplete"
    return {
        "status": overall_status,
        "target_source": target_source,
        "targets": dict(targets),
        "gates": gates,
    }


def _acceptance_gate(
    *,
    observed: Any,
    target: int | float,
    available: bool,
    denominator: int | None,
    operator: str,
) -> dict[str, Any]:
    if not available:
        status = "not_available"
        safe_observed = None
    elif operator == ">=":
        status = "passed" if observed >= target else "failed"
        safe_observed = observed
    elif operator == "<=":
        status = "passed" if observed <= target else "failed"
        safe_observed = observed
    else:
        raise ValueError("unsupported acceptance gate operator")
    return {
        "status": status,
        "observed": safe_observed,
        "target": target,
        "denominator": denominator,
        "operator": operator,
    }


def _load_freeze_state(records: list[dict[str, Any]]) -> dict[str, Any]:
    code_versions = sorted({record["code_version"] for record in records})
    candidate_paths = {DEFAULT_FREEZE_PATH}
    # JSONL is the source of all result numbers. A sidecar is optional metadata
    # used only to state whether the immutable configuration was present.
    for candidate in list(candidate_paths):
        if not candidate or not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "invalid"}
        if not isinstance(payload, dict):
            return {"status": "invalid"}
        return {
            "status": "present",
            "freeze_version": payload.get("freeze_version"),
            "evaluation_method_version": payload.get("evaluation_method_version"),
            "code_version": payload.get("code_version"),
            "matches_result_code_version": (
                len(code_versions) == 1
                and isinstance(payload.get("code_version"), str)
                and bool(payload.get("code_version"))
                and isinstance(code_versions[0], str)
                and bool(code_versions[0])
                and payload.get("code_version") == code_versions[0]
            ),
            "acceptance_targets": payload.get("acceptance_targets"),
        }
    if not code_versions:
        return {"status": "not_available"}
    return {"status": "not_available"}


def _succeeded_metrics(record: dict[str, Any]) -> dict[str, Any] | None:
    if record.get("status") != "succeeded":
        return None
    metrics = record.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("succeeded result metrics must be an object")
    return metrics


def _metric_string(metrics: dict[str, Any], field: str) -> str:
    value = metrics.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty metric string")
    return value


def _metric_number(metrics: dict[str, Any], field: str) -> float:
    value = metrics.get(field)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field} must be a numeric metric")
    return float(value)


def build_report(*, input_path: Path, output_path: Path) -> dict[str, Any]:
    records = load_results(input_path)
    summary = summarize_results(records)
    if "revision_diagnostic" in summary and (input_path.resolve() == output_path.resolve() or output_path.exists()):
        raise ValueError("invalid revision diagnostic output")
    if "context_diagnostic" in summary and input_path.resolve() == output_path.resolve():
        raise ValueError("invalid context diagnostic output")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _render_markdown(summary=summary, records=records),
        encoding="utf-8",
        newline="\n",
    )
    return summary


def _render_markdown(
    *,
    summary: dict[str, Any],
    records: list[dict[str, Any]],
) -> str:
    if "revision_diagnostic" in summary:
        lines = ["# Revision three-case diagnostic", "", "Diagnostic only; no formal acceptance gates.",
                 "Structured conditions do not establish semantic truth. Private evidence is not read by this report.",
                 "Missing slots: " + (", ".join(summary["missing_slots"]) or "none"), ""]
        for row in summary["revision_diagnostic"]:
            lines.extend([f"## {_markdown_cell(row['case_id'])}", "",
                f"Status: {row['status']} / {row['error_code']}",
                f"Evidence capture: {row['evidence_capture_status']}; reference: {row['evidence_ref'] or '-'}", "",
                "| Step | Status | Error | Calls | Input | Output | Total |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: |"])
            for step in row["steps"]:
                cells = [step[k] for k in ("step", "status", "error_code", "provider_calls", "input_tokens", "output_tokens", "total_tokens")]
                lines.append("| " + " | ".join("-" if c is None else str(c) for c in cells) + " |")
            lines.extend(["", "| Condition | Value |", "| --- | --- |"])
            for key, value in sorted(row["conditions"].items()):
                lines.append(f"| {key} | {str(value).lower()} |")
            lines.extend(["", "```json", json.dumps({k: row[k] for k in ("coverage", "claims", "pairs", "evidence")},
                ensure_ascii=False, sort_keys=True, indent=2), "```", ""])
        return "\n".join(lines)
    if "context_diagnostic" in summary:
        return _render_context_diagnostic(summary)
    lines = [
        "# PaperLens Evaluation Report",
        "",
        "This report is rebuilt exclusively from the input JSONL records.",
        "",
        "## Run summary",
        "",
        f"- Attempted: {summary['attempted']}",
        f"- Known provider calls: {summary['provider_calls_known']}",
        (
            "- Records with unknown provider calls: "
            f"{summary['provider_calls_unknown_records']}"
        ),
    ]
    for status, count in summary["status_counts"].items():
        lines.append(f"- Status `{status}`: {count}")
    lines.append(
        "- Mode counts: "
        + ", ".join(
            f"`{mode}`={count}"
            for mode, count in summary["mode_counts"].items()
        )
    )
    lines.extend(
        [
            "",
            "## Token usage",
            "",
            f"- Prompt tokens (known subtotal): {summary['usage']['prompt_tokens']}",
            (
                "- Completion tokens (known subtotal): "
                f"{summary['usage']['completion_tokens']}"
            ),
            f"- Total tokens (known subtotal): {summary['usage']['total_tokens']}",
            (
                "- Estimated cost from recorded usage (CNY): "
                f"{summary['estimated_cost_cny']}"
                if summary["estimated_cost_cny"] is not None
                else "- Estimated cost from recorded usage (CNY): unknown usage present"
            ),
            (
                "- Usage pricing basis: input 1 CNY/million tokens, "
                "output 4 CNY/million tokens."
            ),
            "",
            "## Frozen run metadata",
            "",
            f"- Modes: {', '.join(summary['modes'])}",
            f"- Models: {', '.join(summary['models'])}",
            f"- Prompt versions: {', '.join(summary['prompt_versions'])}",
            f"- Schema versions: {', '.join(summary['schema_versions'])}",
            f"- Data versions: {', '.join(summary['data_versions'])}",
            f"- Code versions: {', '.join(summary['code_versions'])}",
            "",
            "## Sample selection and licenses",
            "",
        ]
    )
    selection = summary["sample_selection"]
    if selection["status"] == "present":
        lines.extend(
            [
                f"- Papers: {selection['paper_count']} "
                f"(development {selection['development_count']}, "
                f"holdout {selection['holdout_count']})",
                f"- License names: {', '.join(selection['license_names'])}",
                "",
                "| paper_id | split | DOI | license | source | license evidence |",
                "|---|---|---|---|---|---|",
            ]
        )
        for paper in selection["papers"]:
            source_url = paper.get("source_url")
            license_url = paper.get("license_evidence_url")
            source_cell = f"[official source]({source_url})" if source_url else "-"
            license_cell = (
                f"[license policy]({license_url})" if license_url else "-"
            )
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_cell(paper.get("paper_id")),
                        _markdown_cell(paper.get("split")),
                        _markdown_cell(paper.get("doi")),
                        _markdown_cell(paper.get("license_name")),
                        source_cell,
                        license_cell,
                    ]
                )
                + " |"
            )
    else:
        lines.append(f"- Sample selection metadata: `{selection['status']}`")
    lines.extend(
        [
            "",
            "## Sample scale rebuilt from JSONL",
            "",
            f"- Expression diagnostics: {summary['expression_audit']['status']}",
            f"- Quality records/papers: {summary['sample_scale']['quality_records']}/"
            f"{summary['sample_scale']['quality_paper_count']}",
            f"- Attack pair records/attack IDs: {summary['sample_scale']['attack_records']}/"
            f"{summary['sample_scale']['attack_pair_id_count']}",
            f"- Revision records: {summary['sample_scale']['revision_records']}",
            f"- Stability records/outputs: {summary['sample_scale']['stability_records']}/"
            f"{summary['sample_scale']['stability_output_count']}",
            "",
            "## Acceptance gates",
            "",
        ]
    )
    expression = summary["expression_audit"]
    if expression["rows"]:
        lines.extend(["", "## Document expression diagnostics", "",
                      "| Case | Status | Category | Signal | Sentence IDs | Observed factual alerts |",
                      "| --- | --- | --- | --- | --- | --- |"])
        for row in expression["rows"]:
            for finding in row["findings"] or [None]:
                cells = [row["case_id"], row["status"],
                         finding["category"] if finding else "not_available",
                         finding["status"] if finding else "not_available",
                         ", ".join(finding["sentence_ids"]) if finding else None,
                         (str(row["observed_factual_alert_count"])
                          if row["observed_factual_alert_count"] is not None else None)]
                lines.append("| " + " | ".join(_markdown_cell(value) for value in cells) + " |")
    acceptance = summary["acceptance_gates"]
    lines.extend(
        [
            f"- Overall status: `{acceptance['status']}`",
            f"- Target source: `{acceptance['target_source']}`",
        ]
    )
    for gate_name, gate in acceptance["gates"].items():
        observed = gate["observed"]
        observed_text = "not available" if observed is None else str(observed)
        denominator = (
            f" (denominator {gate['denominator']})"
            if gate["denominator"] is not None
            else ""
        )
        lines.append(
            f"- `{gate_name}`: `{gate['status']}`, observed {observed_text} "
            f"{gate['operator']} {gate['target']}{denominator}"
        )
    lines.extend(
        [
            "",
            "## Quality ordering",
            "",
        ]
    )
    quality = summary["quality"]
    if quality["complete_groups"]:
        lines.extend(
            [
                "- Strict quality ordering: "
                f"{quality['strict_order_correct']}/{quality['complete_groups']}",
                "- Correct pairwise orderings: "
                f"{quality['pairwise_correct']}/{quality['pairwise_total']}",
                "- Spearman rank correlation (descriptive): "
                f"{quality['spearman_rank_correlation']}",
            ]
        )
    else:
        lines.append("- Not available in these JSONL records.")
    diagnostics = summary["quality_calibration_diagnostics"]
    lines.extend(
        [
            "",
            "## Quality calibration diagnostics",
            "",
            "| paper_id | quality_label | overall_score | decision | hard_failure_count | "
            "non_supported_key_claim_ids | deterministic_issue_codes |",
            "|---|---|---:|---|---:|---|---|",
        ]
    )
    if diagnostics["rows"]:
        for row in diagnostics["rows"]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_cell(row["paper_id"]),
                        _markdown_cell(row["quality_label"]),
                        _markdown_cell(
                            str(row["overall_score"])
                            if row["overall_score"] is not None
                            else None
                        ),
                        _markdown_cell(row["decision"]),
                        _markdown_cell(
                            str(row["hard_failure_count"])
                            if row["hard_failure_count"] is not None
                            else None
                        ),
                        _markdown_cell(
                            ", ".join(row["non_supported_key_claim_ids"])
                            or "-"
                        ),
                        _markdown_cell(
                            ", ".join(row["deterministic_issue_codes"]) or "-"
                        ),
                    ]
                )
                + " |"
            )
    if diagnostics["status"] != "present":
        lines.append(f"- diagnostics={diagnostics['status']}")
    known_error_detection = summary["known_error_detection"]
    lines.extend(
        [
            "",
            "## Known-error detection",
            "",
            (
                "- Records with known-error metrics: "
                f"{known_error_detection['records_with_metrics']}/"
                f"{known_error_detection['eligible_records']}"
            ),
            (
                "- Known-error detection: "
                f"{known_error_detection['known_error_numerator']}/"
                f"{known_error_detection['known_error_denominator']} "
                f"({known_error_detection['known_error_detection_rate']})"
            ),
            (
                "- Severe known-error detection: "
                f"{known_error_detection['severe_error_numerator']}/"
                f"{known_error_detection['severe_error_denominator']} "
                f"({known_error_detection['severe_error_detection_rate']})"
            ),
            f"- known_error_detection={known_error_detection['status']}",
        ]
    )
    citation = summary["citation"]
    lines.extend(
        [
            "",
            "## Key-claim citation coverage",
            "",
        ]
    )
    if citation["records_with_metrics"]:
        lines.extend(
            [
                f"- Records with citation metrics: {citation['records_with_metrics']}/"
                f"{citation['quality_records']}",
                "- Key-claim citation accuracy: "
                f"{citation['accuracy_numerator']}/"
                f"{citation['accuracy_denominator']} "
                f"({citation['accuracy_rate']})",
                "- Key-claim citation completeness: "
                f"{citation['completeness_numerator']}/"
                f"{citation['completeness_denominator']} "
                f"({citation['completeness_rate']})",
            ]
        )
    else:
        lines.append("- Not available in these JSONL records.")
    lines.extend(
        [
            "",
            "## Adversarial pairs",
            "",
        ]
    )
    attacks = summary["attacks"]
    if attacks["attack_total"] or attacks["clean_total"]:
        lines.extend(
            [
                "- Attack detection: "
                f"{attacks['attacks_detected']}/{attacks['attack_total']}",
                "- Clean false positives: "
                f"{attacks['clean_false_positives']}/{attacks['clean_total']}",
            ]
        )
        for attack_type, values in attacks["by_type"].items():
            lines.append(
                f"- `{attack_type}`: detected "
                f"{values['attacks_detected']}/{values['attack_total']}; "
                "clean false positives "
                f"{values['clean_false_positives']}/{values['clean_total']}"
            )
        if attacks["mean_score_delta_by_type"]:
            lines.append("- Mean attack-minus-clean score delta by type:")
            for attack_type, delta in attacks["mean_score_delta_by_type"].items():
                lines.append(f"  - `{attack_type}`: {delta}")
        if attacks["pair_dimension_deltas"]:
            lines.append(
                "- Pair dimension deltas are available in the JSONL-derived summary."
            )
    else:
        lines.append("- Not available in these JSONL records.")
    lines.extend(
        [
            "",
            "## Stability",
            "",
        ]
    )
    stability = summary["stability"]
    if stability["output_count"]:
        lines.extend(
            [
                f"- Stability fixed outputs: {stability['output_count']}",
                f"- Stability runs: {stability['run_count']}",
                "- Mean score standard deviation: "
                f"{stability['mean_score_standard_deviation']}",
                f"- Maximum score range: {stability['maximum_score_range']}",
                "- Dimension-level consistency rate: "
                f"{stability['dimension_level_consistency_rate']}",
            ]
        )
    else:
        lines.append("- Not available in these JSONL records.")
    lines.extend(
        [
            "",
            "## Revision effectiveness",
            "",
        ]
    )
    revisions = summary["revisions"]
    if revisions["completed"]:
        lines.extend(
            [
                f"- Completed revision cases: {revisions['completed']}",
                "- Error resolution rate: "
                f"{revisions['resolved_issue_total']}/"
                f"{revisions['known_issue_total']} "
                f"({revisions['error_resolution_rate']})",
                f"- New severe errors: {revisions['new_severe_error_total']}",
                "- Irrelevant content changes: "
                f"{revisions['irrelevant_change_count']}/"
                f"{revisions['completed']} "
                f"({revisions['irrelevant_change_rate']})",
            ]
        )
    else:
        lines.append("- Not available in these JSONL records.")
    freeze = summary["freeze"]
    lines.extend(
        [
            "",
            "## Configuration freeze",
            "",
            f"- Status: `{freeze['status']}`",
        ]
    )
    if freeze["status"] == "present":
        lines.extend(
            [
                f"- Freeze version: `{freeze['freeze_version']}`",
                f"- Frozen code matches result code: `{freeze['matches_result_code_version']}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Failed, timed out, unsupported, or interrupted cases",
            "",
            "| case_id | run_index | status | error_code |",
            "|---|---:|---|---|",
        ]
    )
    failures = [record for record in records if record["status"] != "succeeded"]
    if failures:
        for record in failures:
            lines.append(
                f"| {record['case_id']} | {record['run_index']} | "
                f"{record['status']} | {record['error_code']} |"
            )
    else:
        lines.append("| none | - | - | - |")
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- Labels are fixed reference answers created by one project author; "
            "no inter-annotator agreement is claimed.",
            "- Repeated model runs measure observed repeatability for this fixed "
            "configuration, not universal model reliability.",
            "- Correlation or ordering on this small set is descriptive evidence, "
            "not absolute validity proof.",
            "",
        ]
    )
    return "\n".join(lines)


def _markdown_cell(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return "-"
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _render_context_diagnostic(summary: dict[str, Any]) -> str:
    diagnosis = summary["context_diagnostic"]
    plan = diagnosis["plan"]
    lines = [
        "# PaperLens Development Context Diagnostic",
        "", "Rebuilt exclusively from JSONL; not a formal stability or acceptance result.",
        "No acceptance thresholds are evaluated. Small samples do not establish a causal context effect.",
        "Pair comparisons share observations and are not independent additional requests.",
        "Primary analysis uses only dispatch-verified first-attempt successes. Schema retries alter the request and are listed separately.",
        "Planned fingerprints are not dispatch observations. Missing verified fingerprints (including legacy and interrupted records) are not verified.",
        "Failure, unknown usage and missing slots are not treated as agreement or zero cost.",
        "", f"- Diagnostic: {_markdown_cell(plan['diagnostic_id'])}",
        f"- Target: {_markdown_cell(plan['target_claim_id'])} / {_markdown_cell(plan['target_block_id'])}",
        f"- Primary status: {diagnosis['status']}",
        f"- Planned / recorded / primary / missing: 9 / {diagnosis['recorded_slots']} / {diagnosis['primary_records']} / {diagnosis['missing_slots']}",
        f"- Retry successes (excluded from primary): {diagnosis['retry_success_records']}",
        f"- Unverified successes (excluded from primary): {diagnosis['unverified_success_records']}",
        f"- Known calls / records with unknown calls: {summary['provider_calls_known']} / {summary['provider_calls_unknown_records']}",
        f"- Known input / output tokens: {summary['usage']['prompt_tokens']} / {summary['usage']['completion_tokens']}",
        f"- Estimated cost CNY: {summary['estimated_cost_cny'] if summary['estimated_cost_cny'] is not None else 'unknown'}",
        "- Pricing basis: input 1 / output 4 CNY per million tokens; not a guaranteed current tariff.",
        f"- Plan SHA256: {diagnosis['plan_sha256']}",
        f"- Pair SHA256: {plan['pair_sha256']}",
        f"- Configuration SHA256: {plan['configuration_sha256']}",
        "", "## Request fingerprints", "",
        "| context | planned first-request SHA256 |", "|---|---|",
    ]
    for context in plan["contexts"]:
        lines.append(f"| {_markdown_cell(context['case_id'])} | {context['request_sha256']} |")
    lines.extend(["", "## Within-context changes", "",
                  "| context | first-attempt successes | disagreements / comparisons | judgment frequencies | score range |",
                  "|---|---:|---:|---|---|"])
    for row in diagnosis["within"]:
        frequencies = json.dumps(row["frequencies"], sort_keys=True)
        lines.append(f"| {_markdown_cell(row['case_id'])} | {row['primary_records']}/3 | {row['disagreements']}/{row['comparisons']} | {_markdown_cell(frequencies)} | {row['score_min']}–{row['score_max']} |")
    lines.extend(["", "## Between-context changes", "",
                  "Cross-context score differences are not used as evidence of a local context effect.", "",
                  "| contexts | disagreements / comparisons |", "|---|---:|"])
    for row in diagnosis["between"]:
        lines.append(f"| {_markdown_cell(row['left'])} / {_markdown_cell(row['right'])} | {row['disagreements']}/{row['comparisons']} |")
    lines.extend(["", "## All recorded slots", "",
                  "Judgment order: relation / scope / terminology / severity / deterministic codes.", "",
                  "| case | run | status | error code | calls | primary | verification | verified first-request SHA256 | judgment | score | conclusion level |",
                  "|---|---:|---|---|---:|---|---|---|---|---:|---:|"])
    for row in diagnosis["rows"]:
        cells = []
        for field in (
            "case_id", "run_index", "status", "error_code", "provider_calls",
            "primary", "request_verification", "verified_first_request_sha256",
            "judgment", "overall_score", "conclusion_level",
        ):
            value = row[field]
            if field == "primary" and type(value) is bool:
                value = "true" if value else "false"
            elif field in {"run_index", "provider_calls", "overall_score", "conclusion_level"} and type(value) in (int, float):
                value = str(value)
            cells.append(_markdown_cell(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _default_output_path(input_path: Path) -> Path:
    stem = input_path.stem
    if stem.endswith("_results"):
        stem = stem[: -len("_results")]
    return input_path.with_name(f"{stem}_report.md")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild a PaperLens evaluation report from raw JSONL.",
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_REPORT_INPUT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output_path = args.output or _default_output_path(args.input)
    try:
        summary = build_report(input_path=args.input, output_path=output_path)
    except FileNotFoundError:
        print("REPORT_INPUT_NOT_FOUND", file=sys.stderr)
        return 2
    print(
        "REPORT_BUILT=True "
        f"ATTEMPTED={summary['attempted']} "
        f"OUTPUT={output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
