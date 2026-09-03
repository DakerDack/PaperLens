from __future__ import annotations

import hashlib
import json
import multiprocessing
import subprocess
import sys
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

from backend.app.audit_service import AuditService, normalize_evidence_text
from backend.app.hy3_service import Hy3ServiceError
from backend.app.models import DeepAuditResult
import eval.build_report as build_report_module
from eval.build_report import _render_markdown, build_report, summarize_results
from eval.run_eval import (
    RESULT_VERSION,
    STAGE7_ACCEPTANCE_TARGETS,
    SUPPORTED_MODES,
    EvaluationCase,
    EvaluationCaseError,
    EvaluationTimeoutError,
    UnsupportedCaseError,
    VersionInfo,
    build_mode_plan,
    DEFAULT_FREEZE_PATH,
    FREEZE_VERSION,
    evaluate_live_case,
    evaluate_smoke_case,
    load_mode_cases,
    freeze_configuration,
    _freeze_payload,
    main,
    materialize_live_case,
    pending_path_for,
    run_cases,
)


VERSIONS = VersionInfo(
    model="hy3",
    prompt_version="audit-v2",
    schema_version="deep-audit-result-v2",
    data_version="paperlens-eval-test-v1",
    code_version="test-code-version",
)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _concurrent_run_cases_worker(
    output_path: str,
    marker_path: str,
    start_event: object,
    read_count: object,
    read_count_lock: object,
    release_event: object,
) -> None:
    """Run one process while synchronizing the pending read for the red test."""
    import eval.run_eval as run_eval_module

    original_load_pending_keys = run_eval_module._load_pending_keys

    def synchronized_load_pending_keys(*, pending_path: Path, mode: str):
        result = original_load_pending_keys(pending_path=pending_path, mode=mode)
        with read_count_lock:
            read_count.value += 1
            read_ordinal = read_count.value
        if read_ordinal == 1:
            release_event.wait(timeout=3)
            release_event.set()
        else:
            release_event.set()
        return result

    run_eval_module._load_pending_keys = synchronized_load_pending_keys
    start_event.wait(timeout=10)

    def evaluate(_case: EvaluationCase) -> dict[str, object]:
        with Path(marker_path).open("a", encoding="utf-8") as handle:
            handle.write("evaluate\n")
            handle.flush()
        time.sleep(0.1)
        return {
            "provider_calls": 1,
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
            "metrics": {"local_counter": 1},
        }

    run_cases(
        mode="smoke",
        cases=[EvaluationCase(case_id="concurrent", run_index=0, payload={})],
        output_path=Path(output_path),
        versions=VERSIONS,
        evaluate=evaluate,
    )


def test_concurrent_run_cases_evaluates_a_key_once() -> None:
    """Two processes must not charge the same case/run slot twice."""
    context = multiprocessing.get_context("spawn")
    root = Path(__file__).resolve().parents[1]
    stem = f"_stage7_concurrent_{time.time_ns()}"
    output_path = root / f"{stem}.jsonl"
    marker_path = root / f"{stem}.calls"
    start_event = context.Event()
    read_count = context.Value("i", 0)
    read_count_lock = context.Lock()
    release_event = context.Event()
    processes = [
        context.Process(
            target=_concurrent_run_cases_worker,
            args=(
                str(output_path),
                str(marker_path),
                start_event,
                read_count,
                read_count_lock,
                release_event,
            ),
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        start_event.set()
        for process in processes:
            process.join(timeout=20)
        for process in processes:
            assert process.exitcode == 0

        calls = marker_path.read_text(encoding="utf-8").splitlines()
        assert len(calls) == 1
        records = _read_jsonl(output_path)
        assert len(records) == 1
        assert [
            (record["case_id"], record["run_index"])
            for record in records
        ] == [("concurrent", 0)]
        assert sum(record["provider_calls"] or 0 for record in records) == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        for path in (
            output_path,
            marker_path,
            pending_path_for(output_path),
            Path(f"{output_path}.lock"),
        ):
            path.unlink(missing_ok=True)


def test_evaluation_lock_preserves_oserror_from_protected_body() -> None:
    from eval.run_eval import _evaluation_run_lock

    root = Path(__file__).resolve().parents[1]
    output_path = root / f"_stage7_lock_error_{time.time_ns()}.jsonl"
    try:
        with pytest.raises(OSError, match="body-failure"):
            with _evaluation_run_lock(output_path):
                raise OSError("body-failure")
    finally:
        output_path.unlink(missing_ok=True)
        Path(f"{output_path}.lock").unlink(missing_ok=True)


def test_duplicate_pending_key_fails_closed_before_evaluation() -> None:
    root = Path(__file__).resolve().parents[1]
    stem = f"_stage7_duplicate_pending_{time.time_ns()}"
    output_path = root / f"{stem}.jsonl"
    pending_path = pending_path_for(output_path)
    pending_record = {
        "case_id": "duplicate-pending",
        "run_index": 0,
        "mode": "smoke",
    }
    pending_path.write_text(
        json.dumps(pending_record, ensure_ascii=False) + "\n"
        + json.dumps(pending_record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    def evaluate(_case: EvaluationCase) -> dict[str, object]:
        calls.append("evaluate")
        return {"provider_calls": 1, "usage": {}, "metrics": {}}

    try:
        with pytest.raises(ValueError, match="duplicate pending key"):
            run_cases(
                mode="smoke",
                cases=[
                    EvaluationCase(
                        case_id="duplicate-pending",
                        run_index=0,
                        payload={},
                    )
                ],
                output_path=output_path,
                versions=VERSIONS,
                evaluate=evaluate,
            )
        assert calls == []
        assert not output_path.exists()
    finally:
        for path in (
            output_path,
            pending_path,
            Path(f"{output_path}.lock"),
        ):
            path.unlink(missing_ok=True)


def _existing_result() -> dict[str, object]:
    return {
        "result_version": RESULT_VERSION,
        "case_id": "already-done",
        "run_index": 0,
        "mode": "smoke",
        "status": "succeeded",
        "error_code": "NONE",
        "model": VERSIONS.model,
        "prompt_version": VERSIONS.prompt_version,
        "schema_version": VERSIONS.schema_version,
        "data_version": VERSIONS.data_version,
        "code_version": VERSIONS.code_version,
        "provider_calls": 1,
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
        },
        "metrics": {"attack_detected": True},
        "elapsed_ms": 12,
        "recorded_at": "2026-09-02T00:00:00+00:00",
    }


@pytest.mark.parametrize("missing_field", ("status", "metrics"))
def test_invalid_existing_result_missing_field_fails_before_evaluation(
    missing_field: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    output_path = root / (
        f"_stage7_invalid_existing_missing_{missing_field}_{time.time_ns()}.jsonl"
    )
    record = _existing_result()
    record.pop(missing_field)
    original = json.dumps(record, ensure_ascii=False) + "\n"
    output_path.write_text(original, encoding="utf-8")
    calls: list[str] = []

    def evaluate(_case: EvaluationCase) -> dict[str, object]:
        calls.append("evaluate")
        return {"provider_calls": 1, "usage": {}, "metrics": {}}

    try:
        with pytest.raises(ValueError, match="invalid existing result"):
            run_cases(
                mode="smoke",
                cases=[
                    EvaluationCase(
                        case_id="already-done",
                        run_index=0,
                        payload={},
                    )
                ],
                output_path=output_path,
                versions=VERSIONS,
                evaluate=evaluate,
            )
        assert calls == []
        assert output_path.read_text(encoding="utf-8") == original
    finally:
        output_path.unlink(missing_ok=True)
        Path(f"{output_path}.lock").unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("provider_calls", "invalid"),
        ("usage", "invalid"),
        ("status", []),
    ),
)
def test_invalid_existing_result_type_fails_before_evaluation(
    field: str,
    value: object,
) -> None:
    root = Path(__file__).resolve().parents[1]
    output_path = root / f"_stage7_invalid_existing_type_{field}_{time.time_ns()}.jsonl"
    record = _existing_result()
    record[field] = value
    original = json.dumps(record, ensure_ascii=False) + "\n"
    output_path.write_text(original, encoding="utf-8")
    calls: list[str] = []

    def evaluate(_case: EvaluationCase) -> dict[str, object]:
        calls.append("evaluate")
        return {"provider_calls": 1, "usage": {}, "metrics": {}}

    try:
        with pytest.raises(ValueError, match="invalid existing result"):
            run_cases(
                mode="smoke",
                cases=[
                    EvaluationCase(
                        case_id="already-done",
                        run_index=0,
                        payload={},
                    )
                ],
                output_path=output_path,
                versions=VERSIONS,
                evaluate=evaluate,
            )
        assert calls == []
        assert output_path.read_text(encoding="utf-8") == original
    finally:
        output_path.unlink(missing_ok=True)
        Path(f"{output_path}.lock").unlink(missing_ok=True)


def _report_freeze(code_version: str) -> dict[str, object]:
    return {
        "freeze_version": FREEZE_VERSION,
        "code_version": code_version,
        "acceptance_targets": dict(STAGE7_ACCEPTANCE_TARGETS),
    }


def test_stale_freeze_code_version_invalidates_acceptance_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    freeze_path = root / f"_stage7_stale_freeze_{time.time_ns()}.json"
    freeze_path.write_text(
        json.dumps(_report_freeze("stale-code-version"), ensure_ascii=False),
        encoding="utf-8",
    )
    record = _existing_result()
    record["code_version"] = "current-code-version"
    monkeypatch.setattr(build_report_module, "DEFAULT_FREEZE_PATH", freeze_path)

    try:
        summary = summarize_results([record])
        assert summary["acceptance_gates"] == {
            "status": "invalid_freeze",
            "target_source": "invalid",
            "targets": {},
            "gates": {},
        }
    finally:
        freeze_path.unlink(missing_ok=True)


def test_mixed_freeze_code_versions_invalidate_matching_freeze(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    freeze_path = root / f"_stage7_mixed_freeze_{time.time_ns()}.json"
    freeze_path.write_text(
        json.dumps(_report_freeze("current-code-version"), ensure_ascii=False),
        encoding="utf-8",
    )
    matching = _existing_result()
    matching["code_version"] = "current-code-version"
    mixed = json.loads(json.dumps(matching))
    mixed["case_id"] = "mixed-version"
    mixed["code_version"] = "other-code-version"
    monkeypatch.setattr(build_report_module, "DEFAULT_FREEZE_PATH", freeze_path)

    try:
        summary = summarize_results([matching, mixed])
        assert summary["acceptance_gates"] == {
            "status": "invalid_freeze",
            "target_source": "invalid",
            "targets": {},
            "gates": {},
        }
    finally:
        freeze_path.unlink(missing_ok=True)


def test_stale_freeze_empty_code_version_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    freeze_path = root / f"_stage7_stale_freeze_empty_{time.time_ns()}.json"
    freeze_path.write_text(
        json.dumps(_report_freeze(""), ensure_ascii=False),
        encoding="utf-8",
    )
    record = _existing_result()
    record["code_version"] = ""
    monkeypatch.setattr(build_report_module, "DEFAULT_FREEZE_PATH", freeze_path)

    try:
        summary = summarize_results([record])
        assert summary["acceptance_gates"] == {
            "status": "invalid_freeze",
            "target_source": "invalid",
            "targets": {},
            "gates": {},
        }
    finally:
        freeze_path.unlink(missing_ok=True)


def test_malformed_freeze_file_invalidates_acceptance_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    freeze_path = root / f"_stage7_malformed_freeze_{time.time_ns()}.json"
    freeze_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(build_report_module, "DEFAULT_FREEZE_PATH", freeze_path)

    try:
        summary = summarize_results([_existing_result()])
        assert summary["acceptance_gates"] == {
            "status": "invalid_freeze",
            "target_source": "invalid",
            "targets": {},
            "gates": {},
        }
    finally:
        freeze_path.unlink(missing_ok=True)


def test_stale_freeze_version_invalidates_acceptance_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    freeze_path = root / f"_stage7_stale_freeze_version_{time.time_ns()}.json"
    freeze = _report_freeze(VERSIONS.code_version)
    freeze["freeze_version"] = "stale-freeze-version"
    freeze_path.write_text(
        json.dumps(freeze, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(build_report_module, "DEFAULT_FREEZE_PATH", freeze_path)

    try:
        summary = summarize_results([_existing_result()])
        assert summary["acceptance_gates"] == {
            "status": "invalid_freeze",
            "target_source": "invalid",
            "targets": {},
            "gates": {},
        }
    finally:
        freeze_path.unlink(missing_ok=True)


def test_supported_modes_are_exactly_the_four_stage_seven_modes() -> None:
    assert SUPPORTED_MODES == ("smoke", "calibrate", "final", "stability")


def test_resume_skips_every_existing_pair_and_records_all_failure_kinds(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "results.jsonl"
    output_path.write_text(
        json.dumps(_existing_result(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    cases = [
        EvaluationCase(case_id="already-done", run_index=0, payload={}),
        EvaluationCase(case_id="times-out", run_index=0, payload={}),
        EvaluationCase(case_id="unsupported", run_index=0, payload={}),
        EvaluationCase(case_id="fails", run_index=0, payload={}),
    ]
    calls: list[str] = []

    def evaluate(case: EvaluationCase) -> dict[str, object]:
        calls.append(case.case_id)
        if case.case_id == "times-out":
            raise EvaluationTimeoutError(
                provider_calls=1,
                usage={
                    "prompt_tokens": 8,
                    "completion_tokens": None,
                    "total_tokens": None,
                },
            )
        if case.case_id == "unsupported":
            raise UnsupportedCaseError()
        raise RuntimeError("dynamic provider detail must not enter JSONL")

    first = run_cases(
        mode="smoke",
        cases=cases,
        output_path=output_path,
        versions=VERSIONS,
        evaluate=evaluate,
    )

    assert calls == ["times-out", "unsupported", "fails"]
    assert first.skipped == 1
    assert first.appended == 3
    records = _read_jsonl(output_path)
    assert len(records) == 4
    by_id = {record["case_id"]: record for record in records}
    assert by_id["times-out"]["status"] == "timeout"
    assert by_id["times-out"]["error_code"] == "TIMEOUT"
    assert by_id["unsupported"]["status"] == "unsupported"
    assert by_id["unsupported"]["error_code"] == "UNSUPPORTED_CASE"
    assert by_id["fails"]["status"] == "failed"
    assert by_id["fails"]["error_code"] == "EVALUATION_FAILED"
    assert "dynamic provider detail" not in output_path.read_text(encoding="utf-8")
    for record in records:
        for field in (
            "model",
            "prompt_version",
            "schema_version",
            "data_version",
            "code_version",
            "mode",
        ):
            assert record[field]

    calls.clear()
    second = run_cases(
        mode="smoke",
        cases=cases,
        output_path=output_path,
        versions=VERSIONS,
        evaluate=evaluate,
    )

    assert calls == []
    assert second.skipped == 4
    assert second.appended == 0
    assert len(_read_jsonl(output_path)) == 4


def test_duplicate_input_or_existing_keys_stop_before_evaluation(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "results.jsonl"
    duplicate_cases = [
        EvaluationCase(case_id="duplicate", run_index=0, payload={}),
        EvaluationCase(case_id="duplicate", run_index=0, payload={}),
    ]
    called = False

    def evaluate(case: EvaluationCase) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    with pytest.raises(ValueError, match="duplicate case_id and run_index"):
        run_cases(
            mode="smoke",
            cases=duplicate_cases,
            output_path=output_path,
            versions=VERSIONS,
            evaluate=evaluate,
        )
    assert called is False

    duplicate_result = _existing_result()
    output_path.write_text(
        "\n".join(
            json.dumps(duplicate_result, ensure_ascii=False) for _ in range(2)
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate result key"):
        run_cases(
            mode="smoke",
            cases=[EvaluationCase(case_id="new", run_index=0, payload={})],
            output_path=output_path,
            versions=VERSIONS,
            evaluate=evaluate,
        )
    assert called is False


def test_interrupted_pending_pair_is_recorded_without_repeating_evaluation(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "results.jsonl"
    pending_path_for(output_path).write_text(
        json.dumps(
            {
                "case_id": "interrupted",
                "run_index": 0,
                "mode": "smoke",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    called = False

    def evaluate(case: EvaluationCase) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    summary = run_cases(
        mode="smoke",
        cases=[EvaluationCase(case_id="interrupted", run_index=0, payload={})],
        output_path=output_path,
        versions=VERSIONS,
        evaluate=evaluate,
    )

    assert called is False
    assert summary.recovered_interrupted == 1
    record = _read_jsonl(output_path)[0]
    assert record["status"] == "failed"
    assert record["error_code"] == "RUN_INTERRUPTED"
    assert record["provider_calls"] is None


def test_report_numbers_are_rebuilt_only_from_jsonl(tmp_path: Path) -> None:
    input_path = tmp_path / "smoke_results.jsonl"
    output_path = tmp_path / "smoke_report.md"
    records = [
        _existing_result(),
        {
            **_existing_result(),
            "case_id": "failed-case",
            "status": "failed",
            "error_code": "HY3_UNAVAILABLE",
            "provider_calls": 2,
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 6,
                "total_tokens": 26,
            },
            "metrics": {},
        },
    ]
    input_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
        + "\n",
        encoding="utf-8",
    )

    summary = build_report(input_path=input_path, output_path=output_path)

    assert summary["attempted"] == 2
    assert summary["status_counts"] == {"failed": 1, "succeeded": 1}
    assert summary["provider_calls_known"] == 3
    assert summary["usage"] == {
        "prompt_tokens": 30,
        "completion_tokens": 10,
        "total_tokens": 40,
    }
    first_report = output_path.read_text(encoding="utf-8")
    assert "Attempted: 2" in first_report
    assert "Known provider calls: 3" in first_report

    records.append(
        {
            **_existing_result(),
            "case_id": "unsupported-case",
            "status": "unsupported",
            "error_code": "UNSUPPORTED_CASE",
            "provider_calls": 0,
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "metrics": {},
        }
    )
    input_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
        + "\n",
        encoding="utf-8",
    )

    rebuilt = build_report(input_path=input_path, output_path=output_path)

    assert rebuilt["attempted"] == 3
    assert "Attempted: 3" in output_path.read_text(encoding="utf-8")
    assert output_path.read_text(encoding="utf-8") != first_report


def test_smoke_manifest_has_the_required_eleven_run_slots() -> None:
    cases = load_mode_cases("smoke")

    assert len(cases) == 11
    assert len({(case.case_id, case.run_index) for case in cases}) == 11
    quality_cases = [
        case for case in cases if case.payload["case_group"] == "quality"
    ]
    assert sorted(case.payload["quality_label"] for case in quality_cases) == [
        "bad",
        "good",
        "medium",
    ]
    attack_cases = [
        case for case in cases if case.payload["case_group"] == "attack"
    ]
    assert {
        (
            case.payload["attack_type"],
            case.payload["pair_role"],
        )
        for case in attack_cases
    } == {
        ("fake_citation", "attack"),
        ("fake_citation", "clean"),
        ("numeric_tampering", "attack"),
        ("numeric_tampering", "clean"),
    }
    stability_cases = [
        case for case in cases if case.payload["case_group"] == "stability"
    ]
    assert len(stability_cases) == 4
    assert {
        case.payload["stability_output_id"] for case in stability_cases
    } == {"fixed-good", "fixed-numeric-attack"}
    for output_id in {"fixed-good", "fixed-numeric-attack"}:
        assert sorted(
            case.run_index
            for case in stability_cases
            if case.payload["stability_output_id"] == output_id
        ) == [0, 1]


def test_smoke_reference_replay_executes_eight_dimensions_and_attacks() -> None:
    cases = load_mode_cases("smoke")
    evaluated = {case.case_id: evaluate_smoke_case(case) for case in cases}

    assert all(
        result["metrics"]["dimension_count"] == 8
        for result in evaluated.values()
    )
    assert all(result["provider_calls"] == 0 for result in evaluated.values())
    quality_scores = {
        case.payload["quality_label"]: evaluated[case.case_id]["metrics"][
            "overall_score"
        ]
        for case in cases
        if case.payload["case_group"] == "quality"
    }
    assert quality_scores["good"] > quality_scores["medium"]
    assert quality_scores["medium"] > quality_scores["bad"]

    attacks = {
        (case.payload["attack_type"], case.payload["pair_role"]): evaluated[
            case.case_id
        ]["metrics"]["attack_detected"]
        for case in cases
        if case.payload["case_group"] == "attack"
    }
    assert attacks == {
        ("fake_citation", "attack"): True,
        ("fake_citation", "clean"): False,
        ("numeric_tampering", "attack"): True,
        ("numeric_tampering", "clean"): False,
    }

    stability: dict[str, list[dict[str, object]]] = {}
    for case in cases:
        if case.payload["case_group"] != "stability":
            continue
        stability.setdefault(case.payload["stability_output_id"], []).append(
            evaluated[case.case_id]["metrics"]
        )
    assert all(results[0] == results[1] for results in stability.values())


def test_smoke_report_rebuilds_quality_attack_and_stability_metrics(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "smoke_results.jsonl"
    output_path = tmp_path / "smoke_report.md"
    run_cases(
        mode="smoke",
        cases=load_mode_cases("smoke"),
        output_path=input_path,
        versions=VERSIONS,
        evaluate=evaluate_smoke_case,
    )

    summary = build_report(input_path=input_path, output_path=output_path)

    assert summary["quality"] == {
        "complete_groups": 1,
        "strict_order_correct": 1,
        "pairwise_correct": 3,
        "pairwise_total": 3,
        "spearman_rank_correlation": 1.0,
    }
    assert summary["attacks"]["attacks_detected"] == 2
    assert summary["attacks"]["attack_total"] == 2
    assert summary["attacks"]["clean_false_positives"] == 0
    assert summary["attacks"]["clean_total"] == 2
    assert summary["stability"] == {
        "output_count": 2,
        "run_count": 4,
        "mean_score_standard_deviation": 0.0,
        "maximum_score_range": 0.0,
        "dimension_level_consistency_rate": 1.0,
    }
    report = output_path.read_text(encoding="utf-8")
    assert "Strict quality ordering: 1/1" in report
    assert "Attack detection: 2/2" in report
    assert "Clean false positives: 0/2" in report
    assert "Stability fixed outputs: 2" in report


def test_build_report_script_runs_from_the_stage_acceptance_command(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "smoke_results.jsonl"
    input_path.write_text(
        json.dumps(_existing_result(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    project_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [
            sys.executable,
            str(project_root / "eval" / "build_report.py"),
            "--input",
            str(input_path),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "REPORT_BUILT=True ATTEMPTED=1" in completed.stdout
    assert (tmp_path / "smoke_report.md").is_file()


def _run_report_main(monkeypatch: pytest.MonkeyPatch, *arguments: str) -> int:
    monkeypatch.setattr(sys, "argv", ["build_report.py", *arguments])
    try:
        return build_report_module.main()
    except SystemExit as exc:
        return int(exc.code)


def test_report_default_input_uses_smoke_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    stem = f"_stage7_report_default_{time.time_ns()}"
    default_input = root / f"{stem}_results.jsonl"
    default_output = root / f"{stem}_report.md"
    default_input.write_text(
        json.dumps(_existing_result(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        build_report_module,
        "DEFAULT_REPORT_INPUT",
        default_input,
        raising=False,
    )

    try:
        exit_code = _run_report_main(monkeypatch)
        assert exit_code == 0
        assert default_output.is_file()
    finally:
        default_input.unlink(missing_ok=True)
        default_output.unlink(missing_ok=True)


def test_report_default_input_explicit_path_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    stem = f"_stage7_report_explicit_{time.time_ns()}"
    default_input = root / f"{stem}_default_results.jsonl"
    explicit_input = root / f"{stem}_explicit_results.jsonl"
    explicit_output = root / f"{stem}_explicit_report.md"
    explicit_input.write_text(
        json.dumps(_existing_result(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        build_report_module,
        "DEFAULT_REPORT_INPUT",
        default_input,
        raising=False,
    )

    try:
        exit_code = _run_report_main(
            monkeypatch,
            "--input",
            str(explicit_input),
            "--output",
            str(explicit_output),
        )
        assert exit_code == 0
        assert explicit_output.is_file()
    finally:
        default_input.unlink(missing_ok=True)
        explicit_input.unlink(missing_ok=True)
        explicit_output.unlink(missing_ok=True)


def test_report_default_input_missing_fails_closed_without_scanning_other_results(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = Path(__file__).resolve().parents[1]
    stem = f"_stage7_report_missing_{time.time_ns()}"
    default_input = root / f"{stem}_results.jsonl"
    other_input = root / f"{stem}_other_results.jsonl"
    default_output = root / f"{stem}_report.md"
    other_input.write_text(
        json.dumps(_existing_result(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        build_report_module,
        "DEFAULT_REPORT_INPUT",
        default_input,
        raising=False,
    )

    try:
        exit_code = _run_report_main(monkeypatch)
        captured = capsys.readouterr()
        assert exit_code != 0
        assert "REPORT_INPUT_NOT_FOUND" in captured.out + captured.err
        assert not default_output.exists()
    finally:
        default_input.unlink(missing_ok=True)
        other_input.unlink(missing_ok=True)
        default_output.unlink(missing_ok=True)


def test_live_manifest_has_licensed_split_and_final_scale() -> None:
    manifest_path = Path(__file__).with_name("live_cases.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == "paperlens-live-manifest-v1"
    assert manifest["data_version"] == "paperlens-plos-abstracts-v1"
    papers = manifest["papers"]
    assert len(papers) == 10
    assert len({paper["paper_id"] for paper in papers}) == 10
    assert sum(paper["split"] == "development" for paper in papers) == 5
    assert sum(paper["split"] == "holdout" for paper in papers) == 5
    assert all(paper["license_name"] == "CC BY" for paper in papers)
    assert all(paper["license_verified_at"] == "2026-09-02" for paper in papers)
    assert all(paper["contains_private_text"] is False for paper in papers)

    section_ids = {
        "research_question",
        "methods",
        "results",
        "limitations",
        "plain_explanation",
    }
    for paper in papers:
        assert paper["source_url"].startswith("https://journals.plos.org/")
        assert paper["license_evidence_url"].startswith("https://plos.org/")
        assert len(paper["source_blocks"]) >= 3
        assert all(
            1 <= len(block["text"]) <= 800 for block in paper["source_blocks"]
        )
        canonical = json.dumps(
            paper["source_blocks"],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        assert paper["material_sha256"] == hashlib.sha256(canonical).hexdigest()
        output = paper["base_output"]
        assert set(output["sections"]) == section_ids
        block_text = {
            block["block_id"]: block["text"] for block in paper["source_blocks"]
        }
        sentence_ids: set[str] = set()
        for section_id, sentence in output["sections"].items():
            assert sentence["sentence_id"] not in sentence_ids
            sentence_ids.add(sentence["sentence_id"])
            claim = sentence["claim"]
            if claim is None:
                continue
            assert claim["candidate_block_id"] in block_text
            assert claim["candidate_quote"] in block_text[
                claim["candidate_block_id"]
            ]
        for quality_label in ("medium", "bad"):
            mutation = paper["quality_mutations"][quality_label]
            assert mutation["target_sentence_id"] in sentence_ids
            assert mutation["replacement_text"]
            assert mutation["known_error_type"]

    attacks = manifest["attacks"]
    assert len(attacks) == 16
    assert len({attack["attack_id"] for attack in attacks}) == 16
    expected_attack_types = {
        "length_padding",
        "terminology_stuffing",
        "numeric_unit_perturbation",
        "correlation_to_causation",
        "scope_expansion",
        "limitation_deletion",
        "fake_citation",
        "rubric_prompt_injection",
    }
    assert set(attack["attack_type"] for attack in attacks) == expected_attack_types
    assert all(
        sum(item["attack_type"] == attack_type for item in attacks) == 2
        for attack_type in expected_attack_types
    )
    assert len(manifest["stability_case_refs"]) == 12
    assert len(set(manifest["stability_case_refs"])) == 12
    assert len(manifest["revision_case_refs"]) == 5
    assert all(ref.endswith(":bad") for ref in manifest["revision_case_refs"])


def test_live_modes_expand_to_the_frozen_stage_seven_scale() -> None:
    calibrate = load_mode_cases("calibrate")
    assert len(calibrate) == 15
    assert len({(case.case_id, case.run_index) for case in calibrate}) == 15
    assert {case.payload["paper_id"] for case in calibrate} == {
        f"dev-{index:02d}" for index in range(1, 6)
    }
    assert {
        case.payload["quality_label"] for case in calibrate
    } == {"good", "medium", "bad"}

    final = load_mode_cases("final")
    assert len(final) == 52
    assert len({(case.case_id, case.run_index) for case in final}) == 52
    assert sum(case.payload["case_group"] == "quality" for case in final) == 15
    assert sum(case.payload["case_group"] == "attack" for case in final) == 32
    assert sum(case.payload["case_group"] == "revision" for case in final) == 5
    attack_roles = {
        (case.payload["attack_id"], case.payload["pair_role"])
        for case in final
        if case.payload["case_group"] == "attack"
    }
    assert len(attack_roles) == 32
    assert {role for _, role in attack_roles} == {"clean", "attack"}

    stability = load_mode_cases("stability")
    assert len(stability) == 36
    stability_ids = {case.case_id for case in stability}
    assert len(stability_ids) == 12
    assert all(
        {case.run_index for case in stability if case.case_id == case_id}
        == {0, 1, 2}
        for case_id in stability_ids
    )


def test_cost_plan_uses_resume_state_and_conservative_request_bounds(
    tmp_path: Path,
) -> None:
    final = load_mode_cases("final")
    quality = next(case for case in final if case.payload["case_group"] == "quality")
    attack = next(case for case in final if case.payload["case_group"] == "attack")
    revision = next(case for case in final if case.payload["case_group"] == "revision")
    output_path = tmp_path / "final_results.jsonl"

    existing_records = []
    for case in (quality, revision):
        record = _existing_result()
        record.update(
            {
                "case_id": case.case_id,
                "run_index": case.run_index,
                "mode": "final",
            }
        )
        existing_records.append(record)
    output_path.write_text(
        "".join(json.dumps(record) + "\n" for record in existing_records),
        encoding="utf-8",
    )
    pending_path_for(output_path).write_text(
        json.dumps(
            {
                "case_id": attack.case_id,
                "run_index": attack.run_index,
                "mode": "final",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    plan = build_mode_plan(mode="final", output_path=output_path)

    assert plan.total_slots == 52
    assert plan.resume_complete_slots == 3
    assert plan.pending_slots == 49
    assert plan.logical_requests_pending == 61
    assert plan.provider_attempts_upper == 183
    assert plan.input_tokens_upper == 1_464_000
    assert plan.completion_tokens_upper == 749_568
    assert plan.cost_cny_no_retry == pytest.approx(1.487424)
    assert plan.cost_cny_upper == pytest.approx(4.462272)


def test_every_frozen_live_slot_materializes_against_production_models() -> None:
    cases = [
        case
        for mode in ("calibrate", "final", "stability")
        for case in load_mode_cases(mode)
    ]
    assert len(cases) == 103

    for case in cases:
        source_blocks, bundle = materialize_live_case(case)
        assert len(source_blocks) == 3
        assert all(block.bbox is None for block in source_blocks)
        assert all(block.parser.value == "pdfplumber" for block in source_blocks)
        if len(bundle.document.sections) != 5:
            pytest.fail(f"{case.payload['paper_id']}: section_count_invalid")
        if len(bundle.claims) != 4:
            pytest.fail(f"{case.payload['paper_id']}: claim_count_invalid")
        if sum(
            getattr(claim.claim_type, "value", claim.claim_type) == "limitation"
            for claim in bundle.claims
        ) != 1:
            pytest.fail(f"{case.payload['paper_id']}: limitation_claim_count_invalid")


def test_attack_pairs_share_sources_and_change_only_the_declared_sentence() -> None:
    final = load_mode_cases("final")
    attacks = [case for case in final if case.payload["case_group"] == "attack"]
    by_attack: dict[str, dict[str, EvaluationCase]] = {}
    for case in attacks:
        by_attack.setdefault(case.payload["attack_id"], {})[
            case.payload["pair_role"]
        ] = case

    assert len(by_attack) == 16
    for pair in by_attack.values():
        clean_sources, clean_bundle = materialize_live_case(pair["clean"])
        attack_sources, attack_bundle = materialize_live_case(pair["attack"])
        assert clean_sources == attack_sources
        clean_sentences = {
            sentence.sentence_id: sentence.text
            for section in clean_bundle.document.sections
            for sentence in section.sentences
        }
        attack_sentences = {
            sentence.sentence_id: sentence.text
            for section in attack_bundle.document.sections
            for sentence in section.sentences
        }
        assert sum(
            clean_sentences[sentence_id] != attack_sentences[sentence_id]
            for sentence_id in clean_sentences
        ) == 1


def test_local_quick_check_exposes_numeric_and_fake_citation_attacks() -> None:
    final = load_mode_cases("final")
    selected = [
        case
        for case in final
        if case.payload.get("pair_role") == "attack"
        and case.payload.get("attack_type")
        in {"numeric_unit_perturbation", "fake_citation"}
    ]
    assert len(selected) == 4
    for case in selected:
        source_blocks, bundle = materialize_live_case(case)
        records, _ = AuditService().quick_check(bundle, source_blocks)
        flags = {flag for record in records for flag in record.rule_flags}
        if case.payload["attack_type"] == "numeric_unit_perturbation":
            assert any(
                flag.startswith(("NUMBER_MISMATCH:", "UNIT_MISMATCH:"))
                for flag in flags
            )
        else:
            assert any(
                flag.startswith(
                    ("CANDIDATE_BLOCK_NOT_FOUND:", "CANDIDATE_QUOTE_NOT_FOUND:")
                )
                for flag in flags
            )


class _FakeLiveService:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.last_run_observation = None

    def _observe(self, operation: str) -> None:
        self.last_run_observation = SimpleNamespace(
            operation=operation,
            provider_calls=1,
            retries=0,
            prompt_tokens=100,
            completion_tokens=40,
            total_tokens=140,
            error_code="NONE",
        )

    def deep_audit(self, *, document, claim_evidence_pairs):
        from backend.app.models import RiskCategory, RiskFinding, RiskLocation
        from backend.app.models import SemanticJudgment

        self.calls.append("deep_audit")
        self._observe("deep_audit")
        return DeepAuditResult(
            semantic_judgments=[
                SemanticJudgment(
                    claim_id=claim.claim_id,
                    block_id=evidence.block_id,
                    relation="supports",
                    scope_status="preserved",
                    terminology_status="correct",
                    severity="none",
                    reason="The claim matches its cited block.",
                )
                for claim, evidence in claim_evidence_pairs
                if evidence.block_id is not None
            ],
            risk_findings=[
                RiskFinding(
                    category=category,
                    status="not_detected",
                    locations=[],
                    reason=f"No {category.value} risk was detected.",
                    remediation="No remediation is required.",
                )
                for category in RiskCategory
            ],
        )

    def revise_sentence(self, *, current_text, sentence_id, **_kwargs):
        from backend.app.models import EditPatch

        self.calls.append("revision")
        self._observe("revision")
        after_text = f"{current_text} Revised with bounded wording."
        return EditPatch(
            patch_id="323e4567-e89b-42d3-a456-426614174012",
            base_version=1,
            scope="sentence",
            target_sentence_ids=[sentence_id],
            before_hash=hashlib.sha256(current_text.encode("utf-8")).hexdigest(),
            before_text=current_text,
            after_text=after_text,
            reason="Bounded test revision.",
            fact_changed=False,
            evidence_changed=False,
        )

    def regenerate_sentence_claims(self, *, original_claims, accepted_after_text, **_kwargs):
        from backend.app.models import SentenceClaimRegenerationResult

        self.calls.append("sentence_claims")
        self._observe("sentence_claims")
        return SentenceClaimRegenerationResult(
            claims=[
                claim.model_copy(
                    update={
                        "claim_id": f"{claim.claim_id}-revised",
                        "text": accepted_after_text,
                    }
                )
                for claim in original_claims
            ]
        )


class _NonTargetSevereIssueLiveService(_FakeLiveService):
    def deep_audit(self, *, document, claim_evidence_pairs):
        from backend.app.models import SemanticJudgment

        pairs = list(claim_evidence_pairs)
        result = super().deep_audit(
            document=document,
            claim_evidence_pairs=pairs,
        )
        non_target_claim, non_target_evidence = pairs[0]
        replacement = SemanticJudgment(
            claim_id=non_target_claim.claim_id,
            block_id=non_target_evidence.block_id,
            relation="contradicts",
            scope_status="preserved",
            terminology_status="correct",
            severity="major",
            reason="Safe test non-target semantic issue.",
        )
        return result.model_copy(
            update={
                "semantic_judgments": [
                    replacement if judgment.claim_id == non_target_claim.claim_id else judgment
                    for judgment in result.semantic_judgments
                ]
            }
        )


def test_every_good_baseline_has_grounded_critical_limitation_claim() -> None:
    good_cases = [
        case
        for mode in ("calibrate", "final")
        for case in load_mode_cases(mode)
        if case.payload.get("case_group") == "quality"
        and case.payload.get("quality_label") == "good"
    ]
    assert len(good_cases) == 10

    for case in good_cases:
        paper_id = str(case.payload["paper_id"])
        source_blocks, bundle = materialize_live_case(case)
        limitation_sentences = [
            sentence
            for section in bundle.document.sections
            if getattr(section.section_id, "value", section.section_id)
            == "limitations"
            for sentence in section.sentences
        ]
        if len(limitation_sentences) != 1:
            pytest.fail(f"{paper_id}: limitation_sentence_count_invalid")
        limitation_sentence = limitation_sentences[0]
        limitation_claims = [
            claim
            for claim in bundle.claims
            if claim.sentence_id == limitation_sentence.sentence_id
        ]
        if len(limitation_claims) != 1:
            pytest.fail(f"{paper_id}: limitation_claim_count_invalid")
        claim = limitation_claims[0]
        if claim.claim_id != f"{paper_id}-c04":
            pytest.fail(f"{paper_id}: limitation_claim_id_invalid")
        if getattr(claim.claim_type, "value", claim.claim_type) != "limitation":
            pytest.fail(f"{paper_id}: limitation_claim_type_invalid")
        if getattr(claim.importance, "value", claim.importance) != "critical":
            pytest.fail(f"{paper_id}: limitation_claim_importance_invalid")
        if claim.auditability != "auditable":
            pytest.fail(f"{paper_id}: limitation_claim_auditability_invalid")
        if not claim.qualifiers:
            pytest.fail(f"{paper_id}: limitation_claim_qualifiers_missing")
        if len(claim.candidate_block_ids) != 1:
            pytest.fail(f"{paper_id}: limitation_candidate_block_count_invalid")
        block_by_id = {block.block_id: block for block in source_blocks}
        block = block_by_id.get(claim.candidate_block_ids[0])
        if block is None:
            pytest.fail(f"{paper_id}: limitation_candidate_block_missing")
        if claim.candidate_quote is None or not (
            normalize_evidence_text(claim.candidate_quote)
            in normalize_evidence_text(block.text)
        ):
            pytest.fail(f"{paper_id}: limitation_candidate_quote_invalid")

        evidence_records, _ = AuditService().quick_check(bundle, source_blocks)
        matching_records = [
            record for record in evidence_records if record.claim_id == claim.claim_id
        ]
        if len(matching_records) != 1:
            pytest.fail(f"{paper_id}: limitation_evidence_count_invalid")
        record = matching_records[0]
        if getattr(record.match_method, "value", record.match_method) != (
            "model_candidate"
        ):
            pytest.fail(f"{paper_id}: limitation_match_method_invalid")
        if record.quote_verified is not True:
            pytest.fail(f"{paper_id}: limitation_quote_not_verified")
        if any(
            flag.startswith(
                (
                    "CANDIDATE_BLOCK_NOT_FOUND:",
                    "CANDIDATE_QUOTE_NOT_FOUND:",
                    "NUMBER_MISMATCH:",
                    "UNIT_MISMATCH:",
                )
            )
            or flag in {
                "NEGATION_MISMATCH",
                "COMPARISON_DIRECTION_MISMATCH",
            }
            for flag in record.rule_flags
        ):
            pytest.fail(f"{paper_id}: limitation_evidence_flags_invalid")


def test_good_baselines_reach_supported_conclusion_limitation_dimension() -> None:
    good_cases = [
        case
        for mode in ("calibrate", "final")
        for case in load_mode_cases(mode)
        if case.payload.get("case_group") == "quality"
        and case.payload.get("quality_label") == "good"
    ]
    assert len(good_cases) == 10

    failures: list[str] = []
    expected_metrics = {
        "key_claim_count": 4,
        "key_claim_citation_accuracy_numerator": 4,
        "key_claim_citation_accuracy_denominator": 4,
        "key_claim_citation_completeness_numerator": 4,
        "key_claim_citation_completeness_denominator": 4,
    }
    for case in good_cases:
        paper_id = str(case.payload["paper_id"])
        metrics = evaluate_live_case(
            case,
            hy3_service=_FakeLiveService(),
        )["metrics"]
        if metrics.get("dimension_points", {}).get("conclusion_limitations") != 4:
            failures.append(f"{paper_id}/conclusion_limitations_dimension_invalid")
        for field, expected in expected_metrics.items():
            if metrics.get(field) != expected:
                failures.append(f"{paper_id}/{field}_invalid")
    if failures:
        pytest.fail("good_baseline_metric_failures=" + ",".join(failures))


def test_good_baseline_negation_flags_are_aggregated() -> None:
    good_cases = [
        case
        for mode in ("calibrate", "final")
        for case in load_mode_cases(mode)
        if case.payload.get("case_group") == "quality"
        and case.payload.get("quality_label") == "good"
    ]
    assert len(good_cases) == 10

    failures: list[str] = []
    for case in good_cases:
        paper_id = str(case.payload["paper_id"])
        source_blocks, bundle = materialize_live_case(case)
        evidence_records, _ = AuditService().quick_check(bundle, source_blocks)
        failures.extend(
            f"{paper_id}/{record.claim_id}"
            for record in evidence_records
            if "NEGATION_MISMATCH" in record.rule_flags
        )
    if failures:
        pytest.fail("good_baseline_negation_failures=" + ",".join(failures))


class _FailingRevisionLiveService(_FakeLiveService):
    def revise_sentence(self, **_kwargs):
        self.calls.append("revision")
        self._observe("revision")
        raise Hy3ServiceError(
            "HY3_UNAVAILABLE",
            "safe test provider failure",
            retryable=True,
            retries=0,
            usage=(100, 40, 140),
        )


class _FailingClaimsLiveService(_FakeLiveService):
    def regenerate_sentence_claims(self, **_kwargs):
        self.calls.append("sentence_claims")
        self._observe("sentence_claims")
        raise Hy3ServiceError(
            "SCHEMA_INVALID",
            "safe test schema failure",
            retryable=False,
            retries=0,
            usage=(100, 40, 140),
        )


def test_revision_failure_preserves_pre_audit_and_failed_revision_usage() -> None:
    case = next(
        case
        for case in load_mode_cases("final")
        if case.payload["case_group"] == "revision"
    )
    service = _FailingRevisionLiveService()

    with pytest.raises(EvaluationCaseError) as exc_info:
        evaluate_live_case(case, hy3_service=service)

    assert exc_info.value.error_code == "HY3_UNAVAILABLE"
    assert exc_info.value.provider_calls == 2
    assert exc_info.value.usage == {
        "prompt_tokens": 200,
        "completion_tokens": 80,
        "total_tokens": 280,
    }


def test_claim_regeneration_failure_preserves_three_calls() -> None:
    case = next(
        case
        for case in load_mode_cases("final")
        if case.payload["case_group"] == "revision"
    )
    service = _FailingClaimsLiveService()

    with pytest.raises(EvaluationCaseError) as exc_info:
        evaluate_live_case(case, hy3_service=service)

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.provider_calls == 3
    assert exc_info.value.usage == {
        "prompt_tokens": 300,
        "completion_tokens": 120,
        "total_tokens": 420,
    }


def test_attack_detection_counts_a_severe_non_target_semantic_judgment() -> None:
    case = next(
        case
        for case in load_mode_cases("final")
        if case.payload.get("case_group") == "attack"
        and case.payload.get("attack_type") == "length_padding"
        and case.payload.get("pair_role") == "attack"
    )

    evaluated = evaluate_live_case(
        case,
        hy3_service=_NonTargetSevereIssueLiveService(),
    )

    assert evaluated["metrics"]["attack_detected"] is True


def test_live_evaluator_uses_production_audit_chain_and_aggregates_observations() -> None:
    case = next(
        case
        for case in load_mode_cases("calibrate")
        if case.payload["quality_label"] == "good"
    )
    service = _FakeLiveService()

    evaluated = evaluate_live_case(case, hy3_service=service)

    assert service.calls == ["deep_audit"]
    assert evaluated["provider_calls"] == 1
    assert evaluated["usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "total_tokens": 140,
    }
    assert evaluated["metrics"]["dimension_count"] == 8
    assert evaluated["metrics"]["case_group"] == "quality"
    assert evaluated["metrics"]["quality_label"] == "good"


def test_live_citation_metrics_support_baseline() -> None:
    case = next(
        case
        for case in load_mode_cases("calibrate")
        if case.payload["quality_label"] == "good"
    )

    evaluated = evaluate_live_case(case, hy3_service=_FakeLiveService())
    metrics = evaluated["metrics"]

    assert metrics["key_claim_count"] == 4
    assert metrics["key_claim_citation_accuracy_numerator"] == 4
    assert metrics["key_claim_citation_accuracy_denominator"] == 4
    assert metrics["key_claim_citation_completeness_numerator"] == 4
    assert metrics["key_claim_citation_completeness_denominator"] == 4


def test_live_key_claim_diagnostics_are_complete_and_privacy_safe() -> None:
    case = next(
        case
        for case in load_mode_cases("calibrate")
        if case.payload["quality_label"] == "good"
    )
    source_blocks, bundle = materialize_live_case(case)
    evidence_records, _ = AuditService().quick_check(bundle, source_blocks)
    expected_pairs = sorted(
        (record.claim_id, record.block_id or "")
        for record in evidence_records
        if any(
            claim.claim_id == record.claim_id
            and getattr(claim.importance, "value", claim.importance) == "critical"
            for claim in bundle.claims
        )
    )

    evaluated = evaluate_live_case(case, hy3_service=_FakeLiveService())
    diagnostics = evaluated["metrics"]["key_claim_diagnostics"]

    assert isinstance(diagnostics, list)
    assert len(diagnostics) == len(expected_pairs)
    allowed_fields = {
        "claim_id",
        "block_id",
        "relation",
        "scope_status",
        "terminology_status",
        "severity",
        "deterministic_issue_codes",
    }
    assert all(set(item) == allowed_fields for item in diagnostics)
    actual_pairs = [
        (item["claim_id"], item["block_id"] or "") for item in diagnostics
    ]
    assert actual_pairs == expected_pairs
    assert all(isinstance(item["deterministic_issue_codes"], list) for item in diagnostics)
    assert all(
        item["relation"] in {"supports", "contradicts", "insufficient", None}
        and item["scope_status"] in {"preserved", "expanded", "unclear", None}
        and item["terminology_status"] in {"correct", "misused", "unclear", None}
        and item["severity"] in {"none", "minor", "major", "critical", None}
        for item in diagnostics
    )
    serialized = json.dumps(diagnostics, ensure_ascii=False).casefold()
    for forbidden in (
        "reason",
        "remediation",
        "prompt",
        "candidate_quote",
    ):
        assert forbidden not in serialized


def test_live_citation_metrics_exclude_contradictory_judgment() -> None:
    case = next(
        case
        for case in load_mode_cases("calibrate")
        if case.payload["quality_label"] == "good"
    )

    evaluated = evaluate_live_case(
        case,
        hy3_service=_NonTargetSevereIssueLiveService(),
    )
    metrics = evaluated["metrics"]

    assert metrics["key_claim_citation_accuracy_numerator"] == 3
    assert metrics["key_claim_citation_accuracy_denominator"] == 4
    assert metrics["key_claim_citation_completeness_numerator"] == 3
    assert metrics["key_claim_citation_completeness_denominator"] == 4


def test_live_citation_metrics_exclude_deterministic_mismatch() -> None:
    case = next(
        case
        for case in load_mode_cases("calibrate")
        if case.case_id == "quality:dev-01:bad"
    )

    evaluated = evaluate_live_case(case, hy3_service=_FakeLiveService())
    metrics = evaluated["metrics"]

    assert metrics["key_claim_citation_accuracy_numerator"] == 3
    assert metrics["key_claim_citation_accuracy_denominator"] == 4
    assert metrics["key_claim_citation_completeness_numerator"] == 3
    assert metrics["key_claim_citation_completeness_denominator"] == 4


def test_live_citation_metrics_separate_bm25_accuracy_and_completeness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import eval.run_eval as run_eval_module

    case = next(
        case
        for case in load_mode_cases("calibrate")
        if case.case_id == "quality:dev-01:good"
    )
    source_blocks, bundle = materialize_live_case(case)
    first_claim = bundle.claims[0].model_copy(
        update={"candidate_quote": "fabricated candidate"}
    )
    modified_bundle = bundle.model_copy(
        update={"claims": [first_claim, *bundle.claims[1:]]}
    )
    monkeypatch.setattr(
        run_eval_module,
        "materialize_live_case",
        lambda _case: (source_blocks, modified_bundle),
    )

    evaluated = evaluate_live_case(case, hy3_service=_FakeLiveService())
    metrics = evaluated["metrics"]

    assert metrics["key_claim_citation_accuracy_numerator"] == 3
    assert metrics["key_claim_citation_accuracy_denominator"] == 4
    assert metrics["key_claim_citation_completeness_numerator"] == 4
    assert metrics["key_claim_citation_completeness_denominator"] == 4


def test_live_evaluator_revision_runs_four_logical_steps_and_reports_revision_metrics() -> None:
    case = next(
        case
        for case in load_mode_cases("final")
        if case.payload["case_group"] == "revision"
    )
    service = _FakeLiveService()

    evaluated = evaluate_live_case(case, hy3_service=service)

    assert service.calls == [
        "deep_audit",
        "revision",
        "sentence_claims",
        "deep_audit",
    ]
    assert evaluated["provider_calls"] == 4
    assert evaluated["usage"] == {
        "prompt_tokens": 400,
        "completion_tokens": 160,
        "total_tokens": 560,
    }
    metrics = evaluated["metrics"]
    assert metrics["case_group"] == "revision"
    assert metrics["known_issue_count"] == 1
    assert metrics["resolved_issue_count"] == 1
    assert metrics["new_severe_error_count"] == 0
    assert metrics["irrelevant_change"] is False


def test_offline_fake_provider_executes_every_frozen_live_case() -> None:
    service = _FakeLiveService()
    cases = [
        case
        for mode in ("calibrate", "final", "stability")
        for case in load_mode_cases(mode)
    ]

    results = [evaluate_live_case(case, hy3_service=service) for case in cases]

    assert len(results) == 103
    assert all(result["provider_calls"] in {1, 4} for result in results)
    assert len(service.calls) == 118


def test_freeze_configuration_records_manifest_and_model_contract_without_key(
    tmp_path: Path,
) -> None:
    freeze_path = tmp_path / "freeze.json"

    frozen = freeze_configuration(output_path=freeze_path)

    assert freeze_path != DEFAULT_FREEZE_PATH
    assert frozen["freeze_version"] == FREEZE_VERSION
    assert frozen["manifest_version"] == "paperlens-live-manifest-v1"
    assert frozen["data_version"] == "paperlens-plos-abstracts-v1"
    assert len(frozen["manifest_sha256"]) == 64
    assert frozen["model"] == "hy3"
    assert frozen["prompt_versions"]["deep_audit"] == "audit-v2"
    assert frozen["schema_versions"]["deep_audit"] == "deep-audit-result-v2"
    assert frozen["overall_score_threshold"] == 75
    assert frozen["dimension_weights"]["factual_consistency"] == 0.20
    assert frozen["core_dimension_gates"]["risk_compliance"] == 3
    assert "api_key" not in json.dumps(frozen).casefold()


def test_non_smoke_cli_requires_explicit_cost_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    isolated_output = tmp_path / "calibrate_results.jsonl"
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_eval.py", "--mode", "calibrate", "--output", str(isolated_output)],
    )

    exit_code = main()

    assert exit_code == 2
    assert "COST_CONFIRMATION_REQUIRED" in capsys.readouterr().out
    assert not isolated_output.exists()


def test_report_rebuilds_revision_rates_scope_counts_and_actual_cost() -> None:
    first = _existing_result()
    first.update(
        {
            "mode": "final",
            "case_id": "revision:holdout-01:bad",
            "metrics": {
                "case_group": "revision",
                "known_issue_count": 1,
                "resolved_issue_count": 1,
                "error_resolution_rate": 1.0,
                "new_severe_error_count": 0,
                "irrelevant_change": False,
            },
            "usage": {
                "prompt_tokens": 400,
                "completion_tokens": 160,
                "total_tokens": 560,
            },
            "provider_calls": 4,
        }
    )
    second = json.loads(json.dumps(first))
    second.update(
        {
            "case_id": "revision:holdout-02:bad",
            "run_index": 0,
            "metrics": {
                "case_group": "revision",
                "known_issue_count": 1,
                "resolved_issue_count": 0,
                "error_resolution_rate": 0.0,
                "new_severe_error_count": 1,
                "irrelevant_change": True,
            },
        }
    )

    summary = summarize_results([first, second])

    assert summary["mode_counts"] == {"final": 2}
    assert summary["provider_calls_known"] == 8
    assert summary["usage"]["total_tokens"] == 1_120
    assert summary["estimated_cost_cny"] == pytest.approx(0.00208)
    assert summary["revisions"] == {
        "completed": 2,
        "known_issue_total": 2,
        "resolved_issue_total": 1,
        "error_resolution_rate": 0.5,
        "new_severe_error_total": 1,
        "irrelevant_change_count": 1,
        "irrelevant_change_rate": 0.5,
    }


def test_report_lists_quality_gate_contributors_without_sensitive_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    missing_freeze_path = root / f"_stage7_diagnostics_missing_{time.time_ns()}.json"
    assert not missing_freeze_path.exists()
    monkeypatch.setattr(
        build_report_module,
        "DEFAULT_FREEZE_PATH",
        missing_freeze_path,
    )
    record = _existing_result()
    record.update(
        {
            "case_id": "quality:diagnostic-paper:good",
            "metrics": {
                "case_group": "quality",
                "paper_id": "diagnostic-paper",
                "quality_label": "good",
                "overall_score": 78,
                "decision": "needs_revision",
                "hard_failure_count": 1,
                "key_claim_count": 2,
                "key_claim_citation_accuracy_denominator": 2,
                "key_claim_citation_completeness_denominator": 2,
                "key_claim_diagnostics": [
                    {
                        "claim_id": "claim-supported",
                        "block_id": "block-01",
                        "relation": "supports",
                        "scope_status": "preserved",
                        "terminology_status": "correct",
                        "severity": "none",
                        "deterministic_issue_codes": [],
                    },
                    {
                        "claim_id": "claim-unsupported",
                        "block_id": "block-02",
                        "relation": "insufficient",
                        "scope_status": "unclear",
                        "terminology_status": "correct",
                        "severity": "major",
                        "deterministic_issue_codes": ["NUMBER_MISMATCH"],
                    },
                ],
            },
        }
    )

    summary = summarize_results([record])
    diagnostics = summary["quality_calibration_diagnostics"]

    assert diagnostics["status"] == "present"
    assert diagnostics["rows"] == [
        {
            "paper_id": "diagnostic-paper",
            "quality_label": "good",
            "overall_score": 78.0,
            "decision": "needs_revision",
            "hard_failure_count": 1,
            "non_supported_key_claim_ids": ["claim-unsupported"],
            "deterministic_issue_codes": ["NUMBER_MISMATCH"],
        }
    ]
    markdown = _render_markdown(summary=summary, records=[record])
    assert "## Quality calibration diagnostics" in markdown
    assert "| paper_id | quality_label | overall_score | decision | hard_failure_count |" in markdown
    assert "claim-unsupported" in markdown
    assert "NUMBER_MISMATCH" in markdown
    diagnostics_section = markdown.split(
        "## Quality calibration diagnostics", 1
    )[1].split("## Key-claim citation coverage", 1)[0]
    for forbidden in (
        "reason",
        "remediation",
        "candidate_quote",
        "prompt",
        "api_key",
    ):
        assert forbidden not in diagnostics_section.casefold()


def test_report_accepts_legacy_records_without_claim_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    missing_freeze_path = root / f"_stage7_legacy_missing_{time.time_ns()}.json"
    assert not missing_freeze_path.exists()
    monkeypatch.setattr(
        build_report_module,
        "DEFAULT_FREEZE_PATH",
        missing_freeze_path,
    )
    record = _existing_result()
    record.update(
        {
            "case_id": "quality:legacy-paper:good",
            "metrics": {
                "case_group": "quality",
                "paper_id": "legacy-paper",
                "quality_label": "good",
                "overall_score": 90,
            },
        }
    )

    summary = summarize_results([record])

    assert summary["quality_calibration_diagnostics"] == {
        "status": "not_available",
        "rows": [],
    }
    markdown = _render_markdown(summary=summary, records=[record])
    assert "## Quality calibration diagnostics" in markdown
    assert "diagnostics=not_available" in markdown


def _report_diagnostic_item(
    claim_id: str = "claim-01",
    block_id: str = "block-01",
) -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "block_id": block_id,
        "relation": "supports",
        "scope_status": "preserved",
        "terminology_status": "correct",
        "severity": "none",
        "deterministic_issue_codes": [],
    }


def _report_quality_diagnostics_record(
    *,
    diagnostics: list[dict[str, object]],
    key_claim_count: object = 1,
    accuracy_denominator: object = 1,
    completeness_denominator: object = 1,
) -> dict[str, object]:
    record = _existing_result()
    record.update(
        {
            "case_id": "quality:diagnostic-integrity:good",
            "metrics": {
                "case_group": "quality",
                "paper_id": "diagnostic-integrity",
                "quality_label": "good",
                "overall_score": 80,
                "decision": "needs_revision",
                "hard_failure_count": 0,
                "key_claim_count": key_claim_count,
                "key_claim_citation_accuracy_denominator": accuracy_denominator,
                "key_claim_citation_completeness_denominator": completeness_denominator,
                "key_claim_diagnostics": diagnostics,
            },
        }
    )
    return record


@pytest.mark.parametrize(
    ("diagnostics", "key_claim_count", "accuracy_denominator", "completeness_denominator"),
    (
        ([], 4, 4, 4),
        ([_report_diagnostic_item()], 1, 4, 1),
        (
            [
                _report_diagnostic_item("claim-01", "block-01"),
                _report_diagnostic_item("claim-01", "block-02"),
            ],
            2,
            2,
            2,
        ),
    ),
)
def test_report_claim_diagnostics_incomplete_counts_fail_closed(
    diagnostics: list[dict[str, object]],
    key_claim_count: object,
    accuracy_denominator: object,
    completeness_denominator: object,
) -> None:
    record = _report_quality_diagnostics_record(
        diagnostics=diagnostics,
        key_claim_count=key_claim_count,
        accuracy_denominator=accuracy_denominator,
        completeness_denominator=completeness_denominator,
    )

    with pytest.raises(ValueError, match="invalid key_claim_diagnostics"):
        summarize_results([record])


def test_report_claim_diagnostics_missing_counts_fail_closed() -> None:
    record = _report_quality_diagnostics_record(
        diagnostics=[_report_diagnostic_item()]
    )
    metrics = record["metrics"]
    assert isinstance(metrics, dict)
    for field in (
        "key_claim_count",
        "key_claim_citation_accuracy_denominator",
        "key_claim_citation_completeness_denominator",
    ):
        metrics.pop(field)

    with pytest.raises(ValueError, match="invalid key_claim_diagnostics"):
        summarize_results([record])


def test_report_claim_diagnostics_complete_counts_are_present() -> None:
    diagnostics = [
        _report_diagnostic_item("claim-01", "block-01"),
        _report_diagnostic_item("claim-02", "block-02"),
    ]
    record = _report_quality_diagnostics_record(
        diagnostics=diagnostics,
        key_claim_count=2,
        accuracy_denominator=2,
        completeness_denominator=2,
    )

    summary = summarize_results([record])

    assert summary["quality_calibration_diagnostics"]["status"] == "present"


def test_report_claim_diagnostics_zero_claims_allow_empty_diagnostics() -> None:
    record = _report_quality_diagnostics_record(
        diagnostics=[],
        key_claim_count=0,
        accuracy_denominator=0,
        completeness_denominator=0,
    )

    summary = summarize_results([record])

    assert summary["quality_calibration_diagnostics"]["status"] == "present"


def test_live_report_includes_manifest_sample_selection_and_license_metadata() -> None:
    record = _existing_result()
    record.update(
        {
            "mode": "final",
            "data_version": "paperlens-plos-abstracts-v1",
            "case_id": "quality:holdout-01:good",
            "metrics": {
                "case_group": "quality",
                "paper_id": "holdout-01",
                "quality_label": "good",
                "overall_score": 100,
            },
        }
    )

    summary = summarize_results([record])

    selection = summary["sample_selection"]
    assert selection["status"] == "present"
    assert selection["paper_count"] == 10
    assert selection["development_count"] == 5
    assert selection["holdout_count"] == 5
    assert selection["license_names"] == ["CC BY"]
    assert selection["papers"][0]["paper_id"] == "dev-01"
    assert "abstract" not in json.dumps(selection).casefold()
    markdown = _render_markdown(summary=summary, records=[record])
    assert "## Sample selection and licenses" in markdown
    assert "dev-01" in markdown
    assert "holdout-01" in markdown
    assert "CC BY" in markdown
    assert '"abstract"' not in markdown.casefold()


def test_report_exposes_frozen_stage_seven_acceptance_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = _freeze_payload()
    assert frozen["acceptance_targets"] == STAGE7_ACCEPTANCE_TARGETS

    missing_freeze_path = (
        Path(__file__).resolve().parent
        / f"_stage7_missing_freeze_{time.time_ns()}.json"
    )
    assert not missing_freeze_path.exists()
    monkeypatch.setattr(
        build_report_module,
        "DEFAULT_FREEZE_PATH",
        missing_freeze_path,
    )

    summary = summarize_results([_existing_result()])
    assert summary["acceptance_gates"]["status"] == "not_available"
    markdown = _render_markdown(summary=summary, records=[_existing_result()])
    assert "## Acceptance gates" in markdown
    assert "not_available" in markdown
