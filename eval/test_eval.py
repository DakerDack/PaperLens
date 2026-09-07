from __future__ import annotations

import copy
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


def _context_diagnostic_setup(**overrides):
    import eval.run_eval as runner

    service = _FakeLiveService()
    service.settings = SimpleNamespace(
        hy3_model="hy3", hy3_base_url="https://offline.invalid/v1",
        hy3_timeout_seconds=120, hy3_max_retries=2, paperlens_model_mode="mock",
    )
    versions = VersionInfo(
        model="hy3", prompt_version=runner.DEEP_AUDIT_PROMPT_VERSION,
        schema_version=runner.DEEP_AUDIT_SCHEMA_VERSION,
        data_version=runner._load_live_manifest()["data_version"],
        code_version=runner._code_version(),
    )
    arguments = dict(
        case_ids=[f"quality:dev-02:{label}" for label in ("good", "medium", "bad")],
        target_claim_id="dev-02-c04", target_block_id="dev-02-b01",
        diagnostic_id="offline-context-test", repetitions=3,
        hy3_service=service, versions=versions,
    )
    arguments.update(overrides)
    plan = runner.build_context_diagnostic_plan(**arguments)
    return runner, service, versions, plan


def _run_context_diagnostic_fixture(tmp_path):
    runner, _, versions, plan = _context_diagnostic_setup()
    service, _ = _context_dispatch_service()
    path = tmp_path / "context_results.jsonl"
    cases = runner.context_diagnostic_cases(plan)
    runner.run_cases(
        mode="calibrate", cases=cases, output_path=path, versions=versions,
        diagnostic_plan=plan,
        evaluate=lambda case: runner.evaluate_live_case(
            case, hy3_service=service, diagnostic_plan=plan,
        ),
    )
    return runner, service, versions, plan, path


def test_context_diagnostic_plan_is_nine_balanced_development_requests():
    runner, service, versions, plan = _context_diagnostic_setup()
    cases = runner.context_diagnostic_cases(plan)
    assert [(c.payload["quality_label"], c.run_index) for c in cases] == [
        (label, index) for index, labels in enumerate(
            (("good", "medium", "bad"), ("medium", "bad", "good"), ("bad", "good", "medium"))
        ) for label in labels
    ]
    assert len({(c.case_id, c.run_index) for c in cases}) == 9
    assert len({c["request_sha256"] for c in plan["contexts"]}) == 3
    assert len(plan["pair_sha256"]) == 64
    assert service.calls == []
    assert runner.SUPPORTED_MODES == ("smoke", "calibrate", "final", "stability")
    assert len(runner.load_mode_cases("calibrate")) == 15


_UNAPPROVED_CONTEXT_PROFILES = [
    ("dev-01", "dev-01-c04", "dev-01-b01"),
    ("dev-04", "dev-04-c04", "dev-04-b01"),
    ("dev-02", "dev-02-c01", "dev-02-b01"),
    ("dev-02", "dev-02-c04", "dev-02-b99"),
]


@pytest.mark.parametrize("paper_id, claim_id, block_id", _UNAPPROVED_CONTEXT_PROFILES)
def test_context_diagnostic_fixed_profile_builder_rejects_other_targets(paper_id, claim_id, block_id):
    runner, service, versions, _ = _context_diagnostic_setup()
    with pytest.raises(ValueError, match="^invalid context diagnostic$"):
        runner.build_context_diagnostic_plan(
            case_ids=[f"quality:{paper_id}:{label}" for label in ("good", "medium", "bad")],
            target_claim_id=claim_id, target_block_id=block_id,
            diagnostic_id="isolated-profile-test", repetitions=3,
            hy3_service=service, versions=versions,
        )
    assert service.calls == []


def test_context_diagnostic_fixed_profile_retains_approved_order_and_local_id():
    runner, service, versions, plan = _context_diagnostic_setup()
    expected = [
        ("quality:dev-02:good", 0), ("quality:dev-02:medium", 0), ("quality:dev-02:bad", 0),
        ("quality:dev-02:medium", 1), ("quality:dev-02:bad", 1), ("quality:dev-02:good", 1),
        ("quality:dev-02:bad", 2), ("quality:dev-02:good", 2), ("quality:dev-02:medium", 2),
    ]
    assert [(c.case_id, c.run_index) for c in runner.context_diagnostic_cases(plan)] == expected
    assert (plan["target_claim_id"], plan["target_block_id"], plan["repetitions"]) == ("dev-02-c04", "dev-02-b01", 3)
    _, _, _, renamed = _context_diagnostic_setup(diagnostic_id="another-local-identifier")
    assert renamed["contexts"] == plan["contexts"] and renamed["pair_sha256"] == plan["pair_sha256"]
    assert renamed["diagnostic_id"] != plan["diagnostic_id"]
    assert service.calls == []


@pytest.mark.parametrize("paper_id, claim_id, block_id", _UNAPPROVED_CONTEXT_PROFILES)
def test_context_diagnostic_fixed_profile_cli_refuses_without_writes(tmp_path, monkeypatch, capsys, paper_id, claim_id, block_id):
    runner, service, versions, _ = _context_diagnostic_setup()
    path = tmp_path / "unapproved.jsonl"
    monkeypatch.setattr(runner, "Hy3Service", lambda: service)
    monkeypatch.setattr(sys, "argv", [
        "run_eval.py", "--mode", "calibrate", "--diagnostic-id", "isolated-profile-test",
        "--target-claim-id", claim_id, "--target-block-id", block_id,
        "--repeat-count", "3", "--output", str(path),
        *[value for label in ("good", "medium", "bad") for value in ("--case-id", f"quality:{paper_id}:{label}")],
    ])
    assert runner.main() == 2
    output = capsys.readouterr()
    assert output.out == "EVAL_REFUSED=invalid context diagnostic\n" and output.err == ""
    assert service.calls == []
    assert not path.exists() and not pending_path_for(path).exists()


def _self_consistent_unapproved_context_record(paper_id, claim_id, block_id):
    """Synthetic metadata only: no historical results or provider judgments."""
    runner, service, versions, approved = _context_diagnostic_setup()
    plan = copy.deepcopy(approved)
    plan.update(target_claim_id=claim_id, target_block_id=block_id)
    for context in plan["contexts"]:
        context["case_id"] = context["case_id"].replace("dev-02", paper_id)
    cases = [EvaluationCase(
        case_id=case.case_id.replace("dev-02", paper_id), run_index=case.run_index,
        payload={**case.payload, "paper_id": paper_id},
    ) for case in runner.context_diagnostic_cases(approved)]
    record = runner._result_record(
        case=cases[0], mode="calibrate", versions=versions, status="failed",
        error_code="HY3_UNAVAILABLE", provider_calls=0, usage=None, elapsed_ms=0,
        metrics={"context_diagnostic": {
            "plan": plan, "plan_sha256": runner._sha256_json(plan),
            "request_sha256": plan["contexts"][0]["request_sha256"],
            "verified_first_request_sha256": None,
        }},
    )
    return runner, service, versions, plan, cases, record


@pytest.mark.parametrize("paper_id, claim_id, block_id", _UNAPPROVED_CONTEXT_PROFILES)
@pytest.mark.parametrize("resume_kind", ["completed", "pending"])
def test_context_diagnostic_fixed_profile_resume_rejects_self_consistent_plan(tmp_path, resume_kind, paper_id, claim_id, block_id):
    runner, service, versions, plan, cases, record = _self_consistent_unapproved_context_record(paper_id, claim_id, block_id)
    output = tmp_path / "resume.jsonl"
    if resume_kind == "completed":
        source = output
        entry = record
    else:
        source = pending_path_for(output)
        entry = {"case_id": cases[0].case_id, "run_index": 0, "mode": "calibrate", **record["metrics"]}
    source.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    before = source.read_bytes()
    calls = []

    def unexpected_evaluate(case):
        calls.append(case.case_id)
        raise EvaluationCaseError(status="failed", error_code="HY3_UNAVAILABLE", provider_calls=0)

    with pytest.raises(ValueError, match="^invalid context diagnostic$"):
        runner.run_cases(mode="calibrate", cases=cases, output_path=output, versions=versions,
                         diagnostic_plan=plan, evaluate=unexpected_evaluate)
    assert calls == [] and service.calls == []
    assert source.read_bytes() == before
    if resume_kind == "pending":
        assert not output.exists()


@pytest.mark.parametrize("paper_id, claim_id, block_id", _UNAPPROVED_CONTEXT_PROFILES)
def test_context_diagnostic_fixed_profile_report_rejects_self_consistent_plan(tmp_path, paper_id, claim_id, block_id):
    runner, service, versions, plan, cases, record = _self_consistent_unapproved_context_record(paper_id, claim_id, block_id)
    source, output = tmp_path / "records.jsonl", tmp_path / "report.md"
    source.write_text(json.dumps(record) + "\n", encoding="utf-8")
    before = source.read_bytes()
    with pytest.raises(ValueError, match="^invalid context diagnostic$"):
        build_report(input_path=source, output_path=output)
    assert not output.exists() and source.read_bytes() == before
    assert service.calls == []


@pytest.mark.parametrize("changes", [
    {"case_ids": ["quality:holdout-01:good", "quality:holdout-01:medium", "quality:holdout-01:bad"]},
    {"case_ids": ["quality:dev-02:good"] * 3},
    {"case_ids": ["quality:dev-02:good", "quality:dev-01:medium", "quality:dev-02:bad"]},
    {"repetitions": 4}, {"repetitions": True},
    {"target_claim_id": "missing-target"}, {"diagnostic_id": "unsafe|identifier"},
])
def test_context_diagnostic_invalid_selection_fails_closed(changes):
    with pytest.raises(ValueError, match="invalid context diagnostic"):
        _context_diagnostic_setup(**changes)


def test_context_diagnostic_changed_target_pair_is_rejected(monkeypatch):
    import eval.run_eval as runner

    original = runner.materialize_live_case

    def changed(case):
        sources, bundle = original(case)
        if case.payload["quality_label"] == "medium":
            bundle.claims[-1].qualifiers.append("synthetic changed boundary")
        return sources, bundle

    monkeypatch.setattr(runner, "materialize_live_case", changed)
    with pytest.raises(ValueError, match="invalid context diagnostic"):
        _context_diagnostic_setup()


def test_context_diagnostic_fingerprint_matches_actual_provider_request(monkeypatch):
    from backend.app.hy3_service import Hy3Service
    from backend.app.settings import Settings

    runner, reference, versions, plan = _context_diagnostic_setup()
    captured = []
    service = Hy3Service(settings=Settings(_env_file=None, paperlens_model_mode="mock"))

    class Captured(Exception):
        pass

    def capture(**kwargs):
        captured.append(runner._sha256_json(kwargs))
        encoded = json.dumps(kwargs, ensure_ascii=False)
        for forbidden in ('"quality_label"', '"known_error_type"', '"case_id"', '"paper_id"', '"diagnostic_id"'):
            assert forbidden not in encoded
        raise Captured

    monkeypatch.setattr(service, "_get_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=capture))
    ))
    for context, case in zip(plan["contexts"], runner.context_diagnostic_cases(plan)[:3]):
        sources, bundle = runner.materialize_live_case(case)
        audit = AuditService()
        records, _ = audit.quick_check(bundle, sources)
        pairs = audit.semantic_pairs(bundle, records)
        prompt = Hy3Service._build_deep_audit_prompt(bundle.document, pairs)
        with pytest.raises(Captured):
            service._deep_audit_live(prompt, {(c.claim_id, e.block_id) for c, e in pairs})
        assert captured[-1] == context["request_sha256"]
    assert len(captured) == 3
    assert reference.calls == []


def test_context_diagnostic_request_drift_stops_before_provider(monkeypatch):
    from backend.app.hy3_service import Hy3Service

    runner, service, versions, plan = _context_diagnostic_setup()
    monkeypatch.setattr(Hy3Service, "_build_deep_audit_prompt", staticmethod(lambda *_: "changed synthetic input"))
    with pytest.raises(ValueError, match="context diagnostic input drift"):
        runner.evaluate_live_case(
            runner.context_diagnostic_cases(plan)[0], hy3_service=service,
            diagnostic_plan=plan,
        )
    assert service.calls == []


def _context_dispatch_service():
    """Exercise the production chain with an injected, network-free client."""
    from backend.app.hy3_service import Hy3Service
    from backend.app.settings import Settings
    import eval.run_eval as runner

    class LocalClient:
        def __init__(self):
            self.chat = self.completions = self
            self.hashes = []
            self.invalid_first_response = False

        def create(self, **kwargs):
            self.hashes.append(runner._sha256_json(kwargs))
            encoded = json.dumps(kwargs, ensure_ascii=False)
            assert not any(label in encoded for label in (
                '"quality_label"', '"known_error_type"', '"case_id"',
                '"paper_id"', '"diagnostic_id"',
            )), "evaluation_labels_reached_dispatch"
            content = "{}" if self.invalid_first_response and len(self.hashes) == 1 else self.response
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=40, total_tokens=140),
            )

    client = LocalClient()

    class InjectedService(Hy3Service):
        def deep_audit(self, *, document, claim_evidence_pairs):
            # Only the local response fixture is synthetic; serialization,
            # dispatch, Schema validation and retries use Hy3Service itself.
            client.response = _FakeLiveService().deep_audit(
                document=document, claim_evidence_pairs=claim_evidence_pairs,
            ).model_dump_json()
            return super().deep_audit(document=document, claim_evidence_pairs=claim_evidence_pairs)

    service = InjectedService(settings=Settings(
        _env_file=None, paperlens_model_mode="live", hy3_api_key="offline-placeholder",
        hy3_model="hy3", hy3_base_url="https://offline.invalid/v1",
        hy3_timeout_seconds=120, hy3_max_retries=2,
    ), client=client)
    service.calls = client.hashes
    return service, client


@pytest.mark.parametrize("changed", ["messages", "message_whitespace", "schema", "schema_array_order", "model_parameters"])
def test_context_diagnostic_dispatch_boundary_rejects_second_build_drift(monkeypatch, changed):
    from backend.app.hy3_service import Hy3Service

    runner, _, versions, plan = _context_diagnostic_setup()
    service, client = _context_dispatch_service()
    builds = 0
    method = "_deep_audit_response_format" if changed.startswith("schema") else "_build_deep_audit_prompt"
    original = getattr(Hy3Service, method)

    def changing_build(*args):
        nonlocal builds
        builds += 1
        value = original(*args)
        if builds == 2:
            if changed == "messages":
                return value + "\nsynthetic dispatch-only change"
            if changed == "message_whitespace":
                return value + " "
            if changed == "schema":
                value["json_schema"]["name"] = "synthetic_changed_schema"
            elif changed == "schema_array_order":
                value["json_schema"]["schema"]["required"].reverse()
            else:
                monkeypatch.setattr(runner.hy3_contract, "DEEP_AUDIT_TEMPERATURE", 0.5)
        return value

    monkeypatch.setattr(Hy3Service, method, staticmethod(changing_build))
    error = None
    try:
        runner.evaluate_live_case(runner.context_diagnostic_cases(plan)[0],
                                  hy3_service=service, diagnostic_plan=plan)
    except ValueError as exc:
        error = str(exc)
    assert builds == 2
    assert len(client.hashes) == 0, "changed_first_request_reached_client"
    assert error == "context diagnostic input drift"


def test_context_diagnostic_dispatch_boundary_records_captured_hash(tmp_path):
    runner, _, versions, plan = _context_diagnostic_setup()
    service, client = _context_dispatch_service()
    path = tmp_path / "verified.jsonl"
    cases = runner.context_diagnostic_cases(plan)

    def evaluate(case):
        result = runner.evaluate_live_case(case, hy3_service=service, diagnostic_plan=plan)
        assert result["metrics"]["context_diagnostic"]["verified_first_request_sha256"] == client.hashes[-1]
        return result

    runner.run_cases(mode="calibrate", cases=cases, output_path=path, versions=versions,
                     evaluate=evaluate, diagnostic_plan=plan)
    records = _read_jsonl(path)
    assert all(record["status"] == "succeeded" for record in records)
    assert len(client.hashes) == 9
    assert service._get_client() is client
    assert "_get_client" not in vars(service)
    for record, captured in zip(records, client.hashes):
        metadata = record["metrics"]["context_diagnostic"]
        assert metadata["verified_first_request_sha256"] == metadata["request_sha256"] == captured
        assert set(metadata) == {"plan", "plan_sha256", "request_sha256", "verified_first_request_sha256"}
    assert summarize_results(records)["context_diagnostic"]["primary_records"] == 9


def test_context_diagnostic_dispatch_boundary_unverified_and_pending_are_not_observed(tmp_path):
    runner, service, versions, plan, path = _run_context_diagnostic_fixture(tmp_path)
    records = _read_jsonl(path)
    for record in records:
        record["metrics"]["context_diagnostic"].pop("verified_first_request_sha256", None)
    summary = summarize_results(records)
    assert summary["context_diagnostic"]["primary_records"] == 0
    assert summary["context_diagnostic"]["status"] == "not_available"
    assert all(row["verified_first_request_sha256"] is None for row in summary["context_diagnostic"]["rows"])

    interrupted = tmp_path / "pending.jsonl"
    pending_path_for(interrupted).write_bytes(pending_path_for(path).read_bytes())
    runner.run_cases(mode="calibrate", cases=runner.context_diagnostic_cases(plan),
                     output_path=interrupted, versions=versions, diagnostic_plan=plan,
                     evaluate=lambda _: pytest.fail("pending_reached_provider"))
    recovered = _read_jsonl(interrupted)
    assert all(r["error_code"] == "RUN_INTERRUPTED" and r["provider_calls"] is None for r in recovered)
    assert all(r["metrics"]["context_diagnostic"].get("verified_first_request_sha256") is None for r in recovered)
    summary = summarize_results(recovered)
    assert summary["context_diagnostic"]["primary_records"] == 0
    assert summary["provider_calls_unknown_records"] == 9
    assert summary["estimated_cost_cny"] is None
    report = _render_markdown(summary=summary, records=recovered)
    assert "planned first-request SHA256" in report
    assert "verified first-request SHA256" in report


def test_context_diagnostic_dispatch_boundary_schema_retry_remains_secondary():
    runner, _, versions, plan = _context_diagnostic_setup()
    service, client = _context_dispatch_service()
    client.invalid_first_response = True
    result = runner.evaluate_live_case(runner.context_diagnostic_cases(plan)[0],
                                      hy3_service=service, diagnostic_plan=plan)
    assert result["provider_calls"] == 2
    assert len(client.hashes) == 2 and client.hashes[0] != client.hashes[1]
    assert result["metrics"]["context_diagnostic"]["verified_first_request_sha256"] == client.hashes[0]
    record = runner._result_record(case=runner.context_diagnostic_cases(plan)[0], mode="calibrate",
        versions=versions, status="succeeded", error_code="NONE", elapsed_ms=1, **result)
    summary = summarize_results([record])["context_diagnostic"]
    assert summary["primary_records"] == 0 and summary["retry_success_records"] == 1


@pytest.mark.parametrize("damage", ["wrong_hash", "wrong_type", "pending_observed", "interrupted_observed"])
def test_context_diagnostic_dispatch_boundary_rejects_false_verification(tmp_path, damage):
    runner, service, versions, plan, path = _run_context_diagnostic_fixture(tmp_path)
    records = _read_jsonl(path)
    first = records[0]
    metadata = first["metrics"]["context_diagnostic"]
    if damage == "wrong_hash":
        metadata["verified_first_request_sha256"] = "0" * 64
    elif damage == "wrong_type":
        metadata["verified_first_request_sha256"] = True
    elif damage == "interrupted_observed":
        first.update(status="failed", error_code="RUN_INTERRUPTED", provider_calls=None)
        first["usage"] = {key: None for key in first["usage"]}
        first["metrics"] = {"context_diagnostic": metadata}
    else:
        entries = _read_jsonl(pending_path_for(path))
        entries[0]["context_diagnostic"]["verified_first_request_sha256"] = metadata["request_sha256"]
        pending_path_for(path).write_text("".join(json.dumps(r) + "\n" for r in entries), encoding="utf-8")
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="invalid context diagnostic"):
        runner.run_cases(mode="calibrate", cases=runner.context_diagnostic_cases(plan),
                         output_path=path, versions=versions, diagnostic_plan=plan,
                         evaluate=lambda _: pytest.fail("false_verification_reached_provider"))
    assert path.read_bytes() == before and len(service.calls) == 9
    if damage != "pending_observed":
        report = tmp_path / "invalid.md"
        with pytest.raises(ValueError, match="invalid context diagnostic"):
            build_report(input_path=path, output_path=report)
        assert not report.exists()


def test_context_diagnostic_resume_and_cost_do_not_repeat_calls(tmp_path):
    runner, service, versions, plan, path = _run_context_diagnostic_fixture(tmp_path)
    before = path.read_bytes()
    summary = runner.run_cases(
        mode="calibrate", cases=runner.context_diagnostic_cases(plan),
        output_path=path, versions=versions, diagnostic_plan=plan,
        evaluate=lambda _: pytest.fail("completed_slot_was_called"),
    )
    assert len(service.calls) == 9
    assert summary.skipped == 9 and summary.appended == 0
    assert path.read_bytes() == before
    budget = runner.build_mode_plan(mode="calibrate", output_path=path, diagnostic_plan=plan)
    assert budget.pending_slots == budget.logical_requests_pending == 0
    fresh = runner.build_mode_plan(mode="calibrate", output_path=tmp_path / "fresh.jsonl", diagnostic_plan=plan)
    assert (fresh.total_slots, fresh.logical_requests_pending, fresh.provider_attempts_upper) == (9, 9, 27)
    assert fresh.cost_cny_no_retry == pytest.approx(0.219456)
    assert fresh.cost_cny_upper == pytest.approx(0.658368)


def test_context_diagnostic_pending_and_failures_keep_metadata(tmp_path):
    runner, service, versions, plan = _context_diagnostic_setup()
    cases = runner.context_diagnostic_cases(plan)
    path = tmp_path / "interrupted.jsonl"
    metadata = runner._context_diagnostic_metadata(plan, cases[0])
    pending_path_for(path).write_text(json.dumps({
        "case_id": cases[0].case_id, "run_index": 0, "mode": "calibrate",
        "context_diagnostic": metadata,
    }) + "\n", encoding="utf-8")
    calls = []

    def evaluate(case):
        calls.append(case.case_id)
        if len(calls) == 1:
            raise EvaluationTimeoutError(provider_calls=1)
        if len(calls) == 2:
            raise UnsupportedCaseError()
        raise EvaluationCaseError(status="failed", error_code="HY3_UNAVAILABLE", provider_calls=1)

    runner.run_cases(mode="calibrate", cases=cases, output_path=path, versions=versions,
                     evaluate=evaluate, diagnostic_plan=plan)
    records = _read_jsonl(path)
    assert len(calls) == 8 and len(records) == 9
    assert records[0]["error_code"] == "RUN_INTERRUPTED"
    assert records[0]["provider_calls"] is None
    assert {r["error_code"] for r in records} == {"RUN_INTERRUPTED", "TIMEOUT", "UNSUPPORTED_CASE", "HY3_UNAVAILABLE"}
    assert all("context_diagnostic" in r["metrics"] for r in records)
    summary = summarize_results(records)["context_diagnostic"]
    assert summary["planned_slots"] == 9 and summary["primary_records"] == 0
    assert summary["status"] == "not_available"


@pytest.mark.parametrize("damage", ["missing", "request", "plan", "extra", "version", "duplicate", "pending", "pending_extra", "record_extra", "missing_judgment"])
def test_context_diagnostic_bad_resume_is_rejected_before_calls(tmp_path, damage):
    runner, service, versions, plan, path = _run_context_diagnostic_fixture(tmp_path)
    records = _read_jsonl(path)
    meta = records[0]["metrics"]["context_diagnostic"]
    if damage == "missing":
        del records[0]["metrics"]["context_diagnostic"]
    elif damage == "request":
        meta["request_sha256"] = "0" * 64
    elif damage == "plan":
        meta["plan_sha256"] = "0" * 64
    elif damage == "extra":
        meta["sensitive_text"] = "must not be echoed"
    elif damage == "version":
        records[0]["code_version"] = "different-code"
    elif damage == "duplicate":
        records.append(records[0])
    elif damage == "record_extra":
        records[0]["sensitive_text"] = "must not be echoed"
    elif damage == "missing_judgment":
        records[0]["metrics"].pop("key_claim_diagnostics")
    else:
        pending = pending_path_for(path)
        entries = _read_jsonl(pending)
        if damage == "pending_extra":
            entries[0]["sensitive_text"] = "must not be echoed"
        else:
            del entries[0]["context_diagnostic"]
        pending.write_text("".join(json.dumps(r) + "\n" for r in entries), encoding="utf-8")
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        runner.run_cases(mode="calibrate", cases=runner.context_diagnostic_cases(plan),
                         output_path=path, versions=versions, diagnostic_plan=plan,
                         evaluate=lambda _: pytest.fail("invalid_resume_called_provider"))
    assert path.read_bytes() == before
    assert len(service.calls) == 9


def test_context_diagnostic_report_separates_within_and_between_without_gates(tmp_path, monkeypatch):
    runner, service, versions, plan, path = _run_context_diagnostic_fixture(tmp_path)
    records = _read_jsonl(path)
    changed = next(r for r in records if r["case_id"].endswith(":medium") and r["run_index"] == 1)
    item = next(d for d in changed["metrics"]["key_claim_diagnostics"] if d["claim_id"] == "dev-02-c04")
    item.update(scope_status="expanded", severity="minor")
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    monkeypatch.setattr(build_report_module, "_load_freeze_state", lambda _: pytest.fail("diagnostic_read_formal_freeze"))
    report_path = tmp_path / "context_report.md"
    summary = build_report(input_path=path, output_path=report_path)
    diagnosis = summary["context_diagnostic"]
    assert diagnosis["status"] == "present" and diagnosis["primary_records"] == 9
    assert [(r["disagreements"], r["comparisons"]) for r in diagnosis["within"]] == [(0, 3), (2, 3), (0, 3)]
    assert [(r["disagreements"], r["comparisons"]) for r in diagnosis["between"]] == [(3, 9), (0, 9), (3, 9)]
    assert summary["acceptance_gates"] == {"status": "not_available", "target_source": "diagnostic_only", "targets": {}, "gates": {}}
    content = report_path.read_text(encoding="utf-8")
    assert "not a formal stability" in content
    assert "Within-context" in content and "Between-context" in content
    assert "causal" in content
    assert "reason" not in content.casefold()
    rebuilt = _render_markdown(summary=summarize_results(list(reversed(records))), records=list(reversed(records)))
    assert rebuilt == content


@pytest.mark.parametrize("scenario, calls, score, level, primary", [
    ("first_success", 1, 87.5, 3, "true"),
    ("zero_success", 1, 0, 0, "true"),
    ("retry_success", 2, 75.25, 2, "false"),
    ("failed_known_zero", 0, None, None, "false"),
    ("failed_unknown", None, None, None, "false"),
    ("interrupted", None, None, None, "false"),
])
def test_context_diagnostic_markdown_scalars_render_validated_jsonl(tmp_path, scenario, calls, score, level, primary):
    runner, service, versions, plan, source = _run_context_diagnostic_fixture(tmp_path)
    records = _read_jsonl(source)
    record = records[0]
    record["provider_calls"] = calls
    if score is not None:
        record["metrics"]["overall_score"] = score
        record["metrics"]["dimension_points"]["conclusion_limitations"] = level
    else:
        record.update(status="failed", error_code="RUN_INTERRUPTED" if scenario == "interrupted" else "HY3_UNAVAILABLE")
        record["usage"] = {key: None for key in record["usage"]}
        metadata = record["metrics"]["context_diagnostic"]
        metadata["verified_first_request_sha256"] = None
        record["metrics"] = {"context_diagnostic": metadata}
    source.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    before = source.read_bytes()
    expected_summary = copy.deepcopy(summarize_results(records))
    output = tmp_path / "scalars.md"
    summary = build_report(input_path=source, output_path=output)
    content = output.read_text(encoding="utf-8")
    table = content.split("## All recorded slots\n", 1)[1]
    rows = [line for line in table.splitlines() if line.startswith("| quality:")]
    cells = [cell.strip() for cell in rows[0].strip("|").split("|")]
    assert len(rows) == 9 and len(cells) == 11
    assert {"run": cells[1], "calls": cells[4], "primary": cells[5],
            "score": cells[9], "level": cells[10]} == {
        "run": "0", "calls": "-" if calls is None else str(calls),
        "primary": primary, "score": "-" if score is None else str(score),
        "level": "-" if level is None else str(level),
    }
    assert [line.strip("|").split("|")[1].strip() for line in rows] == [
        str(row["run_index"]) for row in summary["context_diagnostic"]["rows"]
    ]
    assert summary == expected_summary and source.read_bytes() == before
    assert summary["context_diagnostic"]["primary_records"] == (9 if primary == "true" else 8)
    assert summary["context_diagnostic"]["retry_success_records"] == (1 if scenario == "retry_success" else 0)
    assert summary["acceptance_gates"] == {
        "status": "not_available", "target_source": "diagnostic_only", "targets": {}, "gates": {},
    }
    reversed_source = tmp_path / "reversed.jsonl"
    reversed_output = tmp_path / "reversed.md"
    reversed_source.write_text("".join(json.dumps(r) + "\n" for r in reversed(records)), encoding="utf-8")
    assert build_report(input_path=reversed_source, output_path=reversed_output) == summary
    assert reversed_output.read_bytes() == output.read_bytes()


def test_context_diagnostic_markdown_scalars_preserve_escaping_and_whitelist(tmp_path):
    runner, service, versions, plan, source = _run_context_diagnostic_fixture(tmp_path)
    records = _read_jsonl(source)
    summary = summarize_results(records)
    # Exercise the real renderer's defensive string handling with synthetic
    # display text; invalid JSONL is still rejected by the existing loader tests.
    row = summary["context_diagnostic"]["rows"][0]
    row["judgment"] = "synthetic | bounded\r\nlocal"
    row["reason"] = "UNLISTED_PRIVATE_SENTINEL"
    snapshot = copy.deepcopy(summary)
    content = _render_markdown(summary=summary, records=records)
    assert "synthetic \\| bounded  local" in content
    assert "UNLISTED_PRIVATE_SENTINEL" not in content
    assert summary == snapshot

    class MustNotStringify:
        def __str__(self):
            pytest.fail("arbitrary_object_was_stringified")

    row["overall_score"] = MustNotStringify()
    content = _render_markdown(summary=summary, records=records)
    table = content.split("## All recorded slots\n", 1)[1]
    first = next(line for line in table.splitlines() if line.startswith("| quality:"))
    assert first.endswith(" | - | 4 |")
    assert "UNLISTED_PRIVATE_SENTINEL" not in content


def test_context_diagnostic_report_keeps_retries_and_missing_slots_visible(tmp_path):
    runner, service, versions, plan, path = _run_context_diagnostic_fixture(tmp_path)
    records = _read_jsonl(path)[:-1]
    records[0]["provider_calls"] = 2
    records[1].update(status="timeout", error_code="TIMEOUT")
    records[1]["metrics"] = {"context_diagnostic": records[1]["metrics"]["context_diagnostic"]}
    summary = summarize_results(records)["context_diagnostic"]
    assert summary["status"] == "partial"
    assert (summary["planned_slots"], summary["recorded_slots"], summary["primary_records"]) == (9, 8, 6)
    assert summary["retry_success_records"] == 1
    assert summary["missing_slots"] == 1


def test_context_diagnostic_missing_slot_does_not_imply_known_total_cost(tmp_path):
    runner, service, versions, plan, path = _run_context_diagnostic_fixture(tmp_path)
    summary = summarize_results(_read_jsonl(path)[:-1])
    assert summary["estimated_cost_cny"] is None


def _context_diagnostic_concurrent_worker(path, marker, versions, plan, start):
    import eval.run_eval as runner

    start.wait(timeout=10)

    def evaluate(case):
        with Path(marker).open("a", encoding="utf-8") as handle:
            handle.write(f"{case.case_id}:{case.run_index}\n")
        time.sleep(0.02)
        raise EvaluationCaseError(status="failed", error_code="HY3_UNAVAILABLE", provider_calls=1)

    runner.run_cases(mode="calibrate", cases=runner.context_diagnostic_cases(plan),
                     output_path=Path(path), versions=versions, diagnostic_plan=plan,
                     evaluate=evaluate)


def test_context_diagnostic_concurrent_resume_calls_each_slot_once(tmp_path):
    runner, service, versions, plan = _context_diagnostic_setup()
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    path, marker = tmp_path / "parallel.jsonl", tmp_path / "calls.txt"
    processes = [context.Process(target=_context_diagnostic_concurrent_worker,
        args=(str(path), str(marker), versions, plan, start)) for _ in range(2)]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=20)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        assert process.exitcode == 0
    calls = marker.read_text(encoding="utf-8").splitlines()
    records = _read_jsonl(path)
    assert len(calls) == len(set(calls)) == len(records) == 9
    assert len({(r["case_id"], r["run_index"]) for r in records}) == 9


def test_context_diagnostic_ordinary_report_still_rejects_repeated_quality_labels(tmp_path):
    runner, service, versions, plan, path = _run_context_diagnostic_fixture(tmp_path)
    records = _read_jsonl(path)
    for record in records:
        record["metrics"].pop("context_diagnostic")
    with pytest.raises(ValueError, match="duplicate quality label"):
        summarize_results(records)


@pytest.mark.parametrize("extra", [
    ["--mode", "final"], ["--mode", "stability"], ["--mode", "smoke"],
    ["--freeze-config"], ["--repeat-count", "4"],
])
def test_context_diagnostic_cli_rejects_other_modes_or_refreezing(tmp_path, monkeypatch, extra):
    import eval.run_eval as runner

    monkeypatch.setattr(runner, "freeze_configuration", lambda: pytest.fail("refreeze_attempted"))
    monkeypatch.setattr(runner, "Hy3Service", lambda: pytest.fail("invalid_cli_reached_service"))
    monkeypatch.setattr(sys, "argv", ["run_eval.py", "--mode", "calibrate", "--diagnostic-id", "offline",
        "--case-id", "quality:dev-02:good", "--case-id", "quality:dev-02:medium", "--case-id", "quality:dev-02:bad",
        "--target-claim-id", "dev-02-c04", "--target-block-id", "dev-02-b01",
        "--repeat-count", "3", "--output", str(tmp_path / "invalid.jsonl"), *extra])
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("damage", ["mixed", "request", "target", "extra", "missing_judgment"])
def test_context_diagnostic_report_malformed_input_never_writes(tmp_path, damage):
    runner, service, versions, plan, path = _run_context_diagnostic_fixture(tmp_path)
    records = _read_jsonl(path)
    if damage == "mixed":
        records[0]["metrics"].pop("context_diagnostic")
    elif damage == "request":
        records[0]["metrics"]["context_diagnostic"]["request_sha256"] = "0" * 64
    elif damage == "target":
        records[0]["metrics"]["context_diagnostic"]["plan"]["target_claim_id"] = "different"
    elif damage == "extra":
        records[0]["metrics"]["context_diagnostic"]["raw_response"] = "sensitive sentinel"
    else:
        records[0]["metrics"].pop("key_claim_diagnostics")
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    output = tmp_path / "must_not_exist.md"
    with pytest.raises(ValueError):
        build_report(input_path=path, output_path=output)
    assert not output.exists()


def test_context_diagnostic_cli_requires_cost_and_never_runs_without_it(tmp_path, monkeypatch, capsys):
    runner, service, versions, plan = _context_diagnostic_setup()
    path = tmp_path / "context_results.jsonl"
    monkeypatch.setattr(runner, "Hy3Service", lambda: service)
    # Keep the serializer class intact; the CLI only injects the configured service.
    monkeypatch.setattr(sys, "argv", ["run_eval.py", "--mode", "calibrate",
        "--diagnostic-id", "offline-context-test", "--target-claim-id", "dev-02-c04",
        "--target-block-id", "dev-02-b01", "--repeat-count", "3", "--output", str(path),
        *[value for c in plan["contexts"] for value in ("--case-id", c["case_id"])]])
    assert runner.main() == 2
    output = capsys.readouterr().out
    assert "SLOTS=9" in output and "PROVIDER_ATTEMPTS_UPPER=27" in output
    assert "COST_CONFIRMATION_REQUIRED=True" in output
    assert service.calls == [] and not path.exists()
    assert not pending_path_for(path).exists()


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


def _quality_paper_count_identity_records(paper_ids, failed_indexes=()):
    records = []
    for paper_id in paper_ids:
        for label, score in (("good", 90), ("medium", 60), ("bad", 30)):
            record = _existing_result()
            record.update(case_id=f"quality:{paper_id}:{label}", mode="final", metrics={
                "case_group": "quality", "paper_id": paper_id,
                "quality_label": label, "overall_score": score,
            })
            if len(records) in failed_indexes:
                record.update(status="failed", error_code="EVALUATION_FAILED", metrics={})
            records.append(record)
    return records


@pytest.mark.parametrize(("paper_count", "failed_indexes"), [
    (5, (14,)), (1, (2,)), (1, (0, 1, 2)), (5, tuple(range(15))), (5, ()),
])
def test_quality_paper_count_identity_counts_rows_without_double_counting_papers(paper_count, failed_indexes):
    records = _quality_paper_count_identity_records(
        [f"synthetic-paper-{index:02d}" for index in range(paper_count)], failed_indexes,
    )
    original = copy.deepcopy(records)
    scale = build_report_module._summarize_sample_scale(records)
    assert scale["quality_records"] == paper_count * 3
    assert scale["quality_paper_count"] == paper_count
    assert build_report_module._summarize_sample_scale(list(reversed(records))) == scale
    assert records == original


@pytest.mark.parametrize(("status", "error_code"), [
    ("failed", "EVALUATION_FAILED"), ("failed", "RUN_INTERRUPTED"),
    ("timeout", "TIMEOUT"), ("unsupported", "UNSUPPORTED_CASE"),
])
@pytest.mark.parametrize("failure_metrics", [None, {}, {"case_group": "quality"}])
def test_quality_paper_count_identity_non_success_uses_the_same_paper(status, error_code, failure_metrics):
    records = _quality_paper_count_identity_records(["synthetic-long-paper-id"], (2,))
    records[-1].update(status=status, error_code=error_code, metrics=copy.deepcopy(failure_metrics))
    scale = build_report_module._summarize_sample_scale(records)
    assert scale["quality_records"] == 3
    assert scale["quality_paper_count"] == 1


def test_quality_paper_count_identity_preserves_distinct_hyphenated_ids():
    paper_ids = ["synthetic-paper-a", "synthetic-paper-a-1", "synthetic-paper-a-10", "synthetic-paper-b-long-suffix"]
    records = _quality_paper_count_identity_records(paper_ids, (2, 4, 8, 11))
    scale = build_report_module._summarize_sample_scale(records)
    assert scale["quality_records"] == 12
    assert scale["quality_paper_count"] == 4


def test_quality_paper_count_identity_keeps_smoke_and_other_groups():
    smoke = []
    for case in load_mode_cases("smoke"):
        record = _existing_result()
        record.update(case_id=case.case_id, run_index=case.run_index,
                      metrics=evaluate_smoke_case(case)["metrics"])
        smoke.append(record)
    assert build_report_module._summarize_sample_scale(smoke) == {
        "quality_records": 3, "quality_paper_count": 1,
        "attack_records": 4, "attack_pair_id_count": 4, "revision_records": 0,
        "stability_records": 4, "stability_output_count": 2,
    }
    others = [record for record in smoke if record["metrics"]["case_group"] != "quality"]
    others.append({**_existing_result(), "case_id": "revision:synthetic-paper:bad",
                   "status": "failed", "error_code": "RUN_INTERRUPTED", "metrics": {}})
    original = build_report_module._summarize_sample_scale(others)
    mixed = build_report_module._summarize_sample_scale(
        others + _quality_paper_count_identity_records(["synthetic-paper"], (2,))
    )
    assert {key: value for key, value in mixed.items() if not key.startswith("quality_")} == {
        key: value for key, value in original.items() if not key.startswith("quality_")
    }
    assert mixed["quality_records"] == 3
    assert mixed["quality_paper_count"] == 1


def test_quality_paper_count_identity_preserves_supported_legacy_format():
    records = _quality_paper_count_identity_records(["synthetic-legacy-paper"])
    for index, record in enumerate(records):
        record["case_id"] = f"legacy-quality-output-{index}"
    records[-1].update(status="failed", error_code="EVALUATION_FAILED")
    scale = build_report_module._summarize_sample_scale(records)
    assert scale["quality_records"] == 3
    assert scale["quality_paper_count"] == 1


def test_quality_paper_count_identity_jsonl_report_and_other_metrics_are_unchanged(tmp_path, monkeypatch):
    records = _quality_paper_count_identity_records([f"synthetic-paper-{i}" for i in range(5)], (14,))
    freeze_path = tmp_path / "freeze.json"
    freeze_path.write_text(json.dumps(_report_freeze(VERSIONS.code_version)), encoding="utf-8")
    monkeypatch.setattr(build_report_module, "DEFAULT_FREEZE_PATH", freeze_path)
    # Demonstrate that sample scale does not drive any other metric or gate.
    with monkeypatch.context() as isolated:
        isolated.setattr(build_report_module, "_summarize_sample_scale", lambda _: {"sentinel": 999})
        unrelated_summary = summarize_results(records)
    unrelated_summary.pop("sample_scale")
    path, output = tmp_path / "quality.jsonl", tmp_path / "quality.md"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    original = path.read_bytes()
    assert build_report_module.load_results(path) == records
    summary = build_report(input_path=path, output_path=output)
    assert summary["sample_scale"]["quality_records"] == 15
    assert summary["sample_scale"]["quality_paper_count"] == 5
    assert {key: value for key, value in summary.items() if key != "sample_scale"} == unrelated_summary
    assert summary["attempted"] == 15
    assert summary["status_counts"] == {"failed": 1, "succeeded": 14}
    assert summary["provider_calls_known"] == 15
    assert summary["usage"] == {"prompt_tokens": 150, "completion_tokens": 60, "total_tokens": 210}
    assert summary["quality"]["complete_groups"] == 4
    assert summary["quality"]["pairwise_total"] == 12
    assert "Quality records/papers: 15/5" in output.read_text(encoding="utf-8")
    assert build_report(input_path=path, output_path=tmp_path / "rebuilt.md") == summary
    assert (tmp_path / "rebuilt.md").read_bytes() == output.read_bytes()
    assert path.read_bytes() == original


def test_quality_paper_count_identity_keeps_malformed_record_rejection(tmp_path, monkeypatch):
    records = _quality_paper_count_identity_records(["synthetic-paper"])
    records[0]["metrics"]["quality_label"] = "invalid-label"
    monkeypatch.setattr(build_report_module, "DEFAULT_FREEZE_PATH", tmp_path / "absent.json")
    path, output = tmp_path / "invalid.jsonl", tmp_path / "invalid.md"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="quality_label must be good, medium, or bad"):
        build_report(input_path=path, output_path=output)
    assert not output.exists()
    assert path.read_bytes() == original


def _stability_coverage_contract_fixture(tmp_path: Path, monkeypatch):
    manifest_path = Path(__file__).resolve().parents[1] / "eval" / "live_cases.json"
    manifest_bytes = manifest_path.read_bytes()
    data_version = json.loads(manifest_bytes)["data_version"]
    freeze = _report_freeze(VERSIONS.code_version)
    freeze.update(data_version=data_version, manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest())
    freeze_path = tmp_path / "freeze.json"
    freeze_path.write_text(json.dumps(freeze), encoding="utf-8")
    monkeypatch.setattr(build_report_module, "DEFAULT_FREEZE_PATH", freeze_path)
    records = []
    # Reuse the formal plan; all measured values below are synthetic, not Live results.
    for case in load_mode_cases("stability"):
        record = _existing_result()
        record.update(case_id=case.case_id, run_index=case.run_index,
                      mode="stability", data_version=data_version)
        record["metrics"] = {
            "case_group": "stability",
            "stability_output_id": case.payload["stability_output_id"],
            "overall_score": 80.0,
            "dimension_points": {dimension.value: 4 for dimension in build_report_module.DIMENSION_WEIGHTS},
        }
        records.append(record)
    assert len(records) == 36
    assert len({record["case_id"] for record in records}) == 12
    return records, freeze_path


def _assert_stability_coverage_contract_gates(summary, *, complete):
    assert summary["acceptance_gates"]["target_source"] == "frozen"
    for name, observed, target in (
        ("stability_mean_score_sd", 0.0, 5.0),
        ("stability_dimension_consistency", 1.0, 0.80),
    ):
        gate = summary["acceptance_gates"]["gates"][name]
        assert gate["target"] == target
        assert gate["status"] == ("passed" if complete else "not_available")
        assert gate["observed"] == (observed if complete else None)


@pytest.mark.parametrize("variant", [
    "complete", "two_plus_four", "missing", "wrong_run_indices", "extra_run",
    "non_plan_output", "wrong_output_owner", "wrong_case_owner", "smoke_slot",
    "extra_smoke", "wrong_group", "incomplete_dimensions",
])
def test_stability_coverage_contract_requires_each_planned_slot(tmp_path, monkeypatch, variant):
    records, _ = _stability_coverage_contract_fixture(tmp_path, monkeypatch)
    if variant == "two_plus_four":
        records[2] = copy.deepcopy(records[3])
        records[2]["run_index"] = 3
    elif variant == "missing":
        records.pop()
    elif variant == "wrong_run_indices":
        for record in records:
            if record["run_index"] == 2:
                record["run_index"] = 3
    elif variant == "extra_run":
        extra = copy.deepcopy(records[0])
        extra["run_index"] = 3
        records.append(extra)
    elif variant == "non_plan_output":
        for record in records[:3]:
            record["case_id"] = "stability:synthetic-unplanned"
            record["metrics"]["stability_output_id"] = "synthetic-unplanned"
    elif variant == "wrong_output_owner":
        first, last = records[0]["metrics"], records[-1]["metrics"]
        first["stability_output_id"], last["stability_output_id"] = last["stability_output_id"], first["stability_output_id"]
    elif variant == "wrong_case_owner":
        records[0]["case_id"] = "stability:synthetic-unplanned"
    elif variant == "smoke_slot":
        records[0]["mode"] = "smoke"
    elif variant == "extra_smoke":
        extra = copy.deepcopy(records[0])
        extra.update(case_id="synthetic-smoke-extra", mode="smoke")
        records.append(extra)
    elif variant == "wrong_group":
        records[0]["metrics"]["case_group"] = "synthetic-other-group"
    elif variant == "incomplete_dimensions":
        for record in records:
            record["metrics"]["dimension_points"].pop("factual_consistency")
    assert len({(record["case_id"], record["run_index"]) for record in records}) == len(records)
    summary = summarize_results(records)
    if variant == "two_plus_four":
        assert summary["stability"]["output_count"] == 12
        assert summary["stability"]["run_count"] == 36
        assert sorted(sum(record["case_id"] == case_id for record in records)
                      for case_id in {record["case_id"] for record in records}) == [2] + [3] * 10 + [4]
    _assert_stability_coverage_contract_gates(summary, complete=variant == "complete")


@pytest.mark.parametrize(("status", "error_code"), [
    ("failed", "PROVIDER_FAILED"), ("timeout", "TIMEOUT"),
    ("unsupported", "UNSUPPORTED"), ("failed", "RUN_INTERRUPTED"),
])
def test_stability_coverage_contract_failed_slot_cannot_be_replaced(tmp_path, monkeypatch, status, error_code):
    records, _ = _stability_coverage_contract_fixture(tmp_path, monkeypatch)
    records[0].update(status=status, error_code=error_code, metrics={}, provider_calls=None)
    extra = copy.deepcopy(records[-1])
    extra["run_index"] = 3
    records.append(extra)
    summary = summarize_results(records)
    assert summary["stability"]["run_count"] == 36
    _assert_stability_coverage_contract_gates(summary, complete=False)


@pytest.mark.parametrize("variant", ["data_version", "manifest_hash", "missing_manifest_hash", "missing_data_version"])
def test_stability_coverage_contract_does_not_guess_historical_plan(tmp_path, monkeypatch, variant):
    records, freeze_path = _stability_coverage_contract_fixture(tmp_path, monkeypatch)
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if variant == "data_version":
        for record in records:
            record["data_version"] = "synthetic-historical-data-v0"
        freeze["data_version"] = "synthetic-historical-data-v0"
    elif variant == "manifest_hash":
        freeze["manifest_sha256"] = "0" * 64
    elif variant == "missing_manifest_hash":
        freeze.pop("manifest_sha256")
    else:
        freeze.pop("data_version")
    freeze_path.write_text(json.dumps(freeze), encoding="utf-8")
    _assert_stability_coverage_contract_gates(summarize_results(records), complete=False)


@pytest.mark.parametrize("complete", [False, True])
def test_stability_coverage_contract_jsonl_report_rebuild(tmp_path, monkeypatch, complete):
    records, _ = _stability_coverage_contract_fixture(tmp_path, monkeypatch)
    if not complete:
        records[2] = copy.deepcopy(records[3])
        records[2]["run_index"] = 3
    input_path, report_path = tmp_path / "results.jsonl", tmp_path / "report.md"
    input_path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    original = input_path.read_bytes()
    assert build_report_module.load_results(input_path) == records
    expected = summarize_results(records)
    summary = build_report(input_path=input_path, output_path=report_path)
    assert summary == expected == summarize_results(list(reversed(records)))
    _assert_stability_coverage_contract_gates(summary, complete=complete)
    rendered = report_path.read_text(encoding="utf-8")
    for name in ("stability_mean_score_sd", "stability_dimension_consistency"):
        row = next(line for line in rendered.splitlines() if line.startswith(f"- `{name}`:"))
        assert ("passed" if complete else "not_available") in row
    assert build_report(input_path=input_path, output_path=tmp_path / "rebuilt.md") == summary
    assert (tmp_path / "rebuilt.md").read_bytes() == report_path.read_bytes()
    assert input_path.read_bytes() == original


@pytest.mark.parametrize("variant", ["duplicate", "run_index", "score", "points", "dimension_point"])
def test_stability_coverage_contract_malformed_records_still_fail(tmp_path, monkeypatch, variant):
    records, _ = _stability_coverage_contract_fixture(tmp_path, monkeypatch)
    if variant == "duplicate":
        records.append(copy.deepcopy(records[0]))
    elif variant == "run_index":
        records[0]["run_index"] = True
    elif variant == "score":
        records[0]["metrics"]["overall_score"] = "80"
    elif variant == "points":
        records[0]["metrics"]["dimension_points"] = {}
    else:
        records[0]["metrics"]["dimension_points"]["factual_consistency"] = True
    path, output = tmp_path / "invalid.jsonl", tmp_path / "invalid.md"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(ValueError):
        build_report(input_path=path, output_path=output)
    assert not output.exists()
    assert path.read_bytes() == original


def test_stability_coverage_contract_smoke_remains_descriptive(tmp_path, monkeypatch):
    monkeypatch.setattr(build_report_module, "DEFAULT_FREEZE_PATH", tmp_path / "absent.json")
    records = []
    for case in load_mode_cases("smoke"):
        if case.payload["case_group"] == "stability":
            record = _existing_result()
            record.update(case_id=case.case_id, run_index=case.run_index,
                          metrics=evaluate_smoke_case(case)["metrics"])
            records.append(record)
    path = tmp_path / "smoke.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    summary = build_report(input_path=path, output_path=tmp_path / "smoke.md")
    assert summary["stability"] == {
        "output_count": 2, "run_count": 4, "mean_score_standard_deviation": 0.0,
        "maximum_score_range": 0.0, "dimension_level_consistency_rate": 1.0,
    }
    for name in ("stability_mean_score_sd", "stability_dimension_consistency"):
        assert summary["acceptance_gates"]["gates"][name]["status"] == "not_available"


def test_stability_coverage_contract_invalid_freeze_is_unchanged(tmp_path, monkeypatch):
    records, freeze_path = _stability_coverage_contract_fixture(tmp_path, monkeypatch)
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    freeze["code_version"] = "synthetic-stale-code"
    freeze_path.write_text(json.dumps(freeze), encoding="utf-8")
    assert summarize_results(records)["acceptance_gates"] == {
        "status": "invalid_freeze", "target_source": "invalid", "targets": {}, "gates": {},
    }


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


@pytest.fixture
def mutation_qualifiers_case(monkeypatch: pytest.MonkeyPatch):
    import eval.run_eval as run_eval_module

    original_text = "The sensor was tested only in the pilot group."
    sections = {}
    for index, section_id in enumerate(
        ("research_question", "methods", "results", "limitations", "plain_explanation")
    ):
        sections[section_id] = {
            "sentence_id": f"synthetic-s{index}",
            "text": original_text,
            "claim": {
                "claim_id": f"synthetic-c{index}",
                "claim_type": "result",
                "importance": "critical",
                "qualifiers": ["only", "pilot group"],
                "numeric_entities": [],
                "auditability": "auditable",
                "candidate_block_id": "p01-b001",
                "candidate_quote": original_text,
            },
        }
    paper = {
        "paper_id": "synthetic",
        "source_blocks": [{"block_id": "p01-b001", "text": original_text}],
        "base_output": {"title": "Synthetic sensor study", "sections": sections},
        "quality_mutations": {
            "medium": {
                "target_sentence_id": "synthetic-s2",
                "replacement_text": "The sensor was tested in the pilot group.",
            }
        },
    }
    manifest = {"papers": [paper], "attacks": []}
    monkeypatch.setattr(run_eval_module, "_load_live_manifest", lambda: manifest)
    case = EvaluationCase(
        case_id="quality:synthetic:medium",
        run_index=0,
        payload={
            "case_group": "quality",
            "paper_id": "synthetic",
            "quality_label": "medium",
        },
    )
    return case, paper


@pytest.mark.parametrize("qualifiers", [["pilot group"], []], ids=["explicit", "empty"])
def test_mutation_qualifiers_override_only_target_and_preserve_base(
    mutation_qualifiers_case, qualifiers: list[str],
) -> None:
    case, paper = mutation_qualifiers_case
    mutation = paper["quality_mutations"]["medium"]
    mutation["qualifiers"] = qualifiers
    original_paper = copy.deepcopy(paper)
    good_case = EvaluationCase(
        case_id="quality:synthetic:good",
        run_index=0,
        payload={**case.payload, "quality_label": "good"},
    )
    original_sources, original_bundle = materialize_live_case(good_case)

    sources, bundle = materialize_live_case(case)

    assert sources == original_sources
    assert paper == original_paper
    for original, changed in zip(original_bundle.claims, bundle.claims, strict=True):
        expected = original.model_dump(mode="json")
        if original.sentence_id == mutation["target_sentence_id"]:
            expected.update(text=mutation["replacement_text"], qualifiers=qualifiers)
        assert changed.model_dump(mode="json") == expected
    expected_document = original_bundle.document.model_dump(mode="json")
    for section in expected_document["sections"]:
        for sentence in section["sentences"]:
            if sentence["sentence_id"] == mutation["target_sentence_id"]:
                sentence["text"] = mutation["replacement_text"]
    assert bundle.document.model_dump(mode="json") == expected_document
    target = next(claim for claim in bundle.claims if claim.claim_id == "synthetic-c2")
    target.qualifiers.append("local copy only")
    assert paper == original_paper
    assert materialize_live_case(good_case) == (original_sources, original_bundle)


def test_mutation_qualifiers_missing_keeps_legacy_behavior(
    mutation_qualifiers_case,
) -> None:
    case, paper = mutation_qualifiers_case
    original_paper = copy.deepcopy(paper)

    _, bundle = materialize_live_case(case)

    target = next(claim for claim in bundle.claims if claim.claim_id == "synthetic-c2")
    assert target.qualifiers == ["only", "pilot group"]
    assert target.text == paper["quality_mutations"]["medium"]["replacement_text"]
    assert paper == original_paper


@pytest.mark.parametrize(
    "invalid_qualifiers",
    [None, "pilot group", [1], [None], [False], [""], [" \t\n"], ["pilot group", " "]],
    ids=["null", "string", "number-member", "null-member", "bool-member", "empty-member", "blank-member", "mixed-members"],
)
def test_mutation_qualifiers_invalid_fails_before_provider(
    mutation_qualifiers_case, invalid_qualifiers: object,
) -> None:
    case, paper = mutation_qualifiers_case
    paper["quality_mutations"]["medium"]["qualifiers"] = invalid_qualifiers
    original_paper = copy.deepcopy(paper)
    provider_calls = 0

    def forbidden_deep_audit(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise ValueError("unexpected provider boundary")

    with pytest.raises(ValueError) as error:
        evaluate_live_case(
            case, hy3_service=SimpleNamespace(deep_audit=forbidden_deep_audit)
        )

    assert provider_calls == 0
    assert str(error.value) == (
        "mutation qualifiers must be an array of non-empty strings"
    )
    assert paper == original_paper


@pytest.mark.parametrize(
    ("quality_label", "expected_qualifiers"),
    [("medium", ["video-game group"]), ("bad", ["All three groups"])],
)
def test_dev03_mutation_qualifiers_match_text_without_changing_base(
    quality_label: str, expected_qualifiers: list[str],
) -> None:
    import eval.run_eval as run_eval_module

    manifest = run_eval_module._load_live_manifest()
    original_manifest = copy.deepcopy(manifest)
    paper = next(paper for paper in manifest["papers"] if paper["paper_id"] == "dev-03")
    mutation = paper["quality_mutations"][quality_label]
    actual_qualifiers = mutation.get("qualifiers")
    assert actual_qualifiers == expected_qualifiers
    cases = {case.case_id: case for case in load_mode_cases("calibrate")}
    good_sources, good_bundle = materialize_live_case(cases["quality:dev-03:good"])

    sources, bundle = materialize_live_case(cases[f"quality:dev-03:{quality_label}"])

    if sources != good_sources:
        pytest.fail("source_blocks_changed")
    for original, changed in zip(good_bundle.claims, bundle.claims, strict=True):
        if changed.sentence_id == mutation["target_sentence_id"]:
            assert changed.qualifiers == expected_qualifiers
            assert all(term in changed.text for term in changed.qualifiers)
            if changed.candidate_quote != original.candidate_quote:
                pytest.fail("evidence_changed")
            assert original.qualifiers == ["only", "video-game group"]
        elif changed != original:
            pytest.fail("non_target_claim_changed")
    if manifest != original_manifest:
        pytest.fail("base_manifest_changed")


@pytest.mark.parametrize(
    ("quality_label", "expected_qualifiers"),
    [("medium", ["video-game group"]), ("bad", ["All three groups"])],
)
def test_dev03_mutation_qualifiers_reach_deep_audit_prompt_with_original_evidence(
    monkeypatch: pytest.MonkeyPatch,
    quality_label: str,
    expected_qualifiers: list[str],
) -> None:
    import backend.app.hy3_service as hy3_service_module

    cases = {case.case_id: case for case in load_mode_cases("calibrate")}
    good_sources, good_bundle = materialize_live_case(cases["quality:dev-03:good"])
    original_claim = next(c for c in good_bundle.claims if c.claim_id == "dev-03-c03")
    captured = []

    class PromptInputCaptured(Exception):
        pass

    def capture_render_input(*, content_draft_json, verified_claim_evidence_pairs_json):
        pairs = json.loads(verified_claim_evidence_pairs_json)
        for pair in pairs:
            if pair["claim"]["claim_id"] != original_claim.claim_id:
                continue
            evidence = pair["evidence"]
            captured.append({
                "qualifiers": pair["claim"]["qualifiers"],
                "qualifiers_match_text": all(
                    term in pair["claim"]["text"] for term in pair["claim"]["qualifiers"]
                ),
                "quote_verified": evidence["quote_verified"],
                "original_evidence_retained": (
                    evidence["quote"] == original_claim.candidate_quote
                    and "only" in evidence["quote"].casefold().split()
                    and any(
                        block.block_id == evidence["block_id"]
                        and evidence["quote"] in block.text
                        for block in good_sources
                    )
                ),
            })
        # Stop at the real serializer/renderer boundary: no provider or fake judgment.
        raise PromptInputCaptured

    monkeypatch.setattr(hy3_service_module, "render_deep_audit_prompt", capture_render_input)
    service = SimpleNamespace(
        deep_audit=hy3_service_module.Hy3Service._build_deep_audit_prompt
    )
    with pytest.raises(PromptInputCaptured):
        evaluate_live_case(cases[f"quality:dev-03:{quality_label}"], hy3_service=service)

    assert captured == [{
        "qualifiers": expected_qualifiers,
        "qualifiers_match_text": True,
        "quote_verified": True,
        "original_evidence_retained": True,
    }]


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


def test_dev04_medium_condition_omission_has_no_deterministic_direction_mismatch(
) -> None:
    case = next(
        case
        for case in load_mode_cases("calibrate")
        if case.case_id == "quality:dev-04:medium"
    )

    source_blocks, bundle = materialize_live_case(case)
    records, report = AuditService().quick_check(bundle, source_blocks)
    issue_codes = {
        flag
        for record in records
        for flag in record.rule_flags
    }

    assert getattr(report.audit_status, "value", report.audit_status) == (
        "quick_complete"
    )
    assert getattr(report.decision, "value", report.decision) == (
        "pending_deep_audit"
    )
    assert report.core_gate_passed is None
    assert report.overall_score is None
    assert "COMPARISON_DIRECTION_MISMATCH" not in issue_codes


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

    def revise_sentence(self, *, current_text, sentence_id, evidence_records, **_kwargs):
        from backend.app.models import EditPatch

        self.calls.append("revision")
        self._observe("revision")
        after_text = " ".join(dict.fromkeys(
            record.quote for record in evidence_records if record.quote_verified and record.quote
        ))
        assert after_text, "synthetic_revision_requires_verified_evidence"
        return EditPatch(
            patch_id="323e4567-e89b-42d3-a456-426614174012",
            base_version=1,
            scope="sentence",
            target_sentence_ids=[sentence_id],
            before_hash=hashlib.sha256(current_text.encode("utf-8")).hexdigest(),
            before_text=current_text,
            after_text=after_text,
            reason="Bounded test revision.",
            fact_changed=after_text != current_text,
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


class _TargetSignalLiveService(_FakeLiveService):
    def __init__(self, *, target_claim_id: str, signal: dict[str, str]) -> None:
        super().__init__()
        self.target_claim_id = target_claim_id
        self.signal = signal

    def deep_audit(self, *, document, claim_evidence_pairs):
        from backend.app.models import SemanticJudgment

        pairs = list(claim_evidence_pairs)
        result = super().deep_audit(
            document=document,
            claim_evidence_pairs=pairs,
        )
        target = next(
            judgment
            for judgment in result.semantic_judgments
            if judgment.claim_id == self.target_claim_id
        )
        replacement = SemanticJudgment(
            claim_id=target.claim_id,
            block_id=target.block_id,
            relation=self.signal.get("relation", "supports"),
            scope_status=self.signal.get("scope_status", "preserved"),
            terminology_status=self.signal.get("terminology_status", "correct"),
            severity=self.signal.get("severity", "none"),
            reason="Safe synthetic known-error signal.",
        )
        return result.model_copy(
            update={
                "semantic_judgments": [
                    replacement
                    if judgment.claim_id == self.target_claim_id
                    else judgment
                    for judgment in result.semantic_judgments
                ]
            }
        )


class _CapturingLiveService(_FakeLiveService):
    def __init__(self) -> None:
        super().__init__()
        self.review_input = ""

    def deep_audit(self, *, document, claim_evidence_pairs):
        pairs = list(claim_evidence_pairs)
        self.review_input = json.dumps(
            {
                "document": document.model_dump(mode="json"),
                "claim_evidence_pairs": [
                    {
                        "claim": claim.model_dump(mode="json"),
                        "evidence": evidence.model_dump(mode="json"),
                    }
                    for claim, evidence in pairs
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return super().deep_audit(
            document=document,
            claim_evidence_pairs=pairs,
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


def _attack_attribution_contract_inputs(*, has_target=True, signal=None, points=None):
    from backend.app.models import DimensionId, DimensionResult, SemanticJudgment
    import eval.run_eval as runner

    revision, sources = _revision_metrics_fixture()
    bundle = revision["after_bundle"]
    bundle.claims[1].sentence_id = "synthetic-methods"
    runner._find_sentence(bundle, "synthetic-target").text = bundle.claims[0].text
    runner._find_sentence(bundle, "synthetic-methods").text = bundle.claims[1].text
    evidence, _ = AuditService().quick_check(bundle, sources)
    assert all(record.quote_verified and not record.rule_flags for record in evidence)
    judgments = [judgment.model_copy(deep=True) for judgment in revision["after_result"].semantic_judgments]
    non_target = judgments[1].model_dump(mode="json")
    non_target.update(signal or {})
    judgments[1] = SemanticJudgment.model_validate(non_target)
    dimensions = []
    for dimension in DimensionId:
        level = (points or {}).get(dimension.value, 4)
        dimensions.append(DimensionResult(
            dimension_id=dimension, raw_metrics={"level_points": level}, score=level * 25,
            level="good" if level == 4 else "acceptable" if level >= 2 else "poor",
        ))
    return dict(
        attack={"target_sentence_id": "synthetic-target" if has_target else "synthetic-absent-target",
                "attack_type": "numeric_tampering"},
        bundle=bundle, source_blocks=sources, evidence_records=evidence,
        deep_result=DeepAuditResult(semantic_judgments=judgments, risk_findings=[]),
        report=SimpleNamespace(dimensions=dimensions),
    )


_ATTRIBUTION_NON_TARGET_ANOMALIES = [
    {"relation": "contradicts"}, {"relation": "insufficient"},
    {"scope_status": "expanded"}, {"scope_status": "unclear"},
    {"terminology_status": "misused"}, {"terminology_status": "unclear"},
    {"relation": "contradicts", "scope_status": "expanded", "terminology_status": "misused"},
]


@pytest.mark.parametrize("has_target", [True, False])
@pytest.mark.parametrize("severity", ["none", "minor"])
@pytest.mark.parametrize("anomaly", _ATTRIBUTION_NON_TARGET_ANOMALIES)
def test_attack_detection_attribution_contract_non_target_low_severity_is_not_sufficient(has_target, severity, anomaly):
    import eval.run_eval as runner

    inputs = _attack_attribution_contract_inputs(has_target=has_target, signal={**anomaly, "severity": severity})
    assert runner._attack_detected(**inputs) is False


@pytest.mark.parametrize("severity", ["major", "critical"])
@pytest.mark.parametrize("anomaly", [{}, _ATTRIBUTION_NON_TARGET_ANOMALIES[-1]])
def test_attack_detection_attribution_contract_severe_non_target_is_preserved(severity, anomaly):
    import eval.run_eval as runner

    inputs = _attack_attribution_contract_inputs(signal={**anomaly, "severity": severity})
    assert runner._attack_detected(**inputs) is True


@pytest.mark.parametrize("signal", [
    {"relation": "contradicts", "severity": "minor"},
    {"relation": "insufficient", "severity": "none"},
    {"scope_status": "expanded", "severity": "minor"},
    {"scope_status": "unclear", "severity": "none"},
    {"terminology_status": "misused", "severity": "minor"},
    {"terminology_status": "unclear", "severity": "none"},
    {"severity": "major"}, {"severity": "critical"},
])
def test_attack_detection_attribution_contract_target_semantics_and_independent_signal_remain(signal):
    from backend.app.models import SemanticJudgment
    import eval.run_eval as runner

    inputs = _attack_attribution_contract_inputs(signal={"scope_status": "expanded", "severity": "minor"})
    target = inputs["deep_result"].semantic_judgments[0]
    inputs["deep_result"].semantic_judgments[0] = SemanticJudgment.model_validate({
        **target.model_dump(mode="json"), **signal,
    })
    assert runner._attack_detected(**inputs) is True


@pytest.mark.parametrize("flag", [
    "NUMBER_MISMATCH:20", "UNIT_MISMATCH:ms", "NEGATION_MISMATCH",
    "COMPARISON_DIRECTION_MISMATCH", "CANDIDATE_BLOCK_NOT_FOUND:synthetic", "CANDIDATE_QUOTE_MISSING",
])
def test_attack_detection_attribution_contract_target_rules_remain(flag):
    import eval.run_eval as runner

    inputs = _attack_attribution_contract_inputs(signal={"scope_status": "expanded", "severity": "minor"})
    inputs["evidence_records"][0].rule_flags = [flag]
    assert runner._attack_detected(**inputs) is True


@pytest.mark.parametrize("has_target", [True, False])
@pytest.mark.parametrize("level", [0, 3, 4])
@pytest.mark.parametrize(("attack_type", "dimension"), [
    ("limitation_deletion", "conclusion_limitations"),
    ("terminology_stuffing", "terminology"),
    ("rubric_prompt_injection", "risk_compliance"),
    ("length_padding", "reader_adaptation"),
])
def test_attack_detection_attribution_contract_dimension_fallback_keeps_boundaries(has_target, level, attack_type, dimension):
    import eval.run_eval as runner

    inputs = _attack_attribution_contract_inputs(
        has_target=has_target, points={dimension: level},
        signal={"scope_status": "expanded", "severity": "minor"},
    )
    inputs["attack"]["attack_type"] = attack_type
    assert runner._attack_detected(**inputs) is (not has_target and level < 4)


@pytest.mark.parametrize("attack_type", [
    "limitation_deletion", "terminology_stuffing", "rubric_prompt_injection", "length_padding", "numeric_tampering",
])
def test_attack_detection_attribution_contract_does_not_expand_dimension_mapping(attack_type):
    import eval.run_eval as runner

    inputs = _attack_attribution_contract_inputs(has_target=False, points={"factual_consistency": 0})
    inputs["attack"]["attack_type"] = attack_type
    assert runner._attack_detected(**inputs) is False


@pytest.mark.parametrize("has_target", [True, False])
@pytest.mark.parametrize("severity", ["none", "minor"])
def test_attack_detection_attribution_contract_normal_judgments_do_not_hit(has_target, severity):
    import eval.run_eval as runner

    inputs = _attack_attribution_contract_inputs(has_target=has_target, signal={"severity": severity})
    assert runner._attack_detected(**inputs) is False


@pytest.mark.parametrize(("signal", "expected"), [
    ({}, False),
    ({"scope_status": "expanded", "severity": "minor"}, False),
    ({"relation": "contradicts", "severity": "none"}, False),
    ({"terminology_status": "misused", "severity": "minor"}, False),
    ({**_ATTRIBUTION_NON_TARGET_ANOMALIES[-1], "severity": "minor"}, False),
    ({"severity": "major"}, True), ({"severity": "critical"}, True),
])
def test_attack_detection_attribution_contract_full_chain_pair_roles_and_usage_match(monkeypatch, signal, expected):
    import eval.run_eval as runner

    inputs = _attack_attribution_contract_inputs()
    bundle, sources = inputs["bundle"], inputs["source_blocks"]
    attack = {**inputs["attack"], "attack_id": "synthetic-attribution", "paper_id": "synthetic-paper"}
    monkeypatch.setattr(runner, "materialize_live_case", lambda _: (copy.deepcopy(sources), bundle.model_copy(deep=True)))
    monkeypatch.setattr(runner, "_load_live_manifest", lambda: {"attacks": [attack]})

    class LocalService(_TargetSignalLiveService):
        def deep_audit(self, *, document, claim_evidence_pairs):
            pairs = list(claim_evidence_pairs)
            self.captured = {
                "document": document.model_dump(mode="json"),
                "pairs": [(claim.model_dump(mode="json"), evidence.model_dump(mode="json")) for claim, evidence in pairs],
            }
            return super().deep_audit(document=document, claim_evidence_pairs=pairs)

    results, captured = [], []
    for pair_role in ("attack", "clean"):
        service = LocalService(target_claim_id=bundle.claims[1].claim_id, signal=signal)
        case = EvaluationCase(case_id=f"attack:synthetic-attribution:{pair_role}", run_index=0, payload={
            "case_group": "attack", "attack_id": attack["attack_id"], "paper_id": "synthetic-paper", "pair_role": pair_role,
        })
        evaluated = runner.evaluate_live_case(case, hy3_service=service)
        assert evaluated["metrics"]["attack_detected"] is expected
        assert evaluated["provider_calls"] == 1
        assert evaluated["usage"] == {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}
        assert service.calls == ["deep_audit"]
        assert evaluated["metrics"]["pair_role"] == pair_role
        assert evaluated["metrics"]["expected_attack"] is (pair_role == "attack")
        results.append(evaluated)
        captured.append(service.captured)
    assert captured[0] == captured[1]
    for result in results:
        result["metrics"].pop("pair_role")
        result["metrics"].pop("expected_attack")
    assert results[0] == results[1]
    serialized_input = json.dumps(captured[0])
    for forbidden in ("pair_role", "quality_label", "known_error_", "case_id", "paper_id"):
        assert forbidden not in serialized_input


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


def test_known_error_detection_labels_do_not_enter_review_model_input() -> None:
    case = next(
        case
        for case in load_mode_cases("calibrate")
        if case.case_id == "quality:dev-01:bad"
    )
    service = _CapturingLiveService()

    evaluated = evaluate_live_case(case, hy3_service=service)

    review_input = service.review_input.casefold()
    for forbidden in (
        "known_error_type",
        "known_error_severity",
        "known_error_target_claim_ids",
        "known_error_detected",
        "numeric_factual_error",
    ):
        assert forbidden not in review_input
    metrics = evaluated["metrics"]
    assert metrics["known_error_type"] == "numeric_factual_error"
    assert metrics["known_error_severity"] == "critical"
    assert metrics["known_error_detected"] is True
    serialized_metrics = json.dumps(metrics, ensure_ascii=False).casefold()
    for forbidden in (
        "replacement_text",
        "paper_text",
        "prompt",
        "reason",
        "raw_response",
        "api_key",
    ):
        assert forbidden not in serialized_metrics


def test_known_error_detection_uses_deterministic_issue_codes() -> None:
    case = next(
        case
        for case in load_mode_cases("calibrate")
        if case.case_id == "quality:dev-01:bad"
    )

    metrics = evaluate_live_case(
        case,
        hy3_service=_FakeLiveService(),
    )["metrics"]

    assert metrics["known_error_target_sentence_id"] == "dev-01-s03"
    assert metrics["known_error_target_claim_ids"] == ["dev-01-c03"]
    assert metrics["known_error_detected"] is True


@pytest.mark.parametrize(
    ("signal", "expected"),
    (
        ({"relation": "contradicts"}, True),
        ({"relation": "insufficient"}, True),
        ({"scope_status": "expanded"}, True),
        ({"terminology_status": "misused"}, True),
        ({"severity": "minor"}, True),
        ({"severity": "major"}, True),
        ({"severity": "critical"}, True),
        (
            {
                "scope_status": "unclear",
                "terminology_status": "unclear",
            },
            False,
        ),
    ),
)
def test_known_error_detection_uses_only_fixed_review_signals(
    signal: dict[str, str],
    expected: bool,
) -> None:
    case = next(
        case
        for case in load_mode_cases("calibrate")
        if case.case_id == "quality:dev-02:medium"
    )

    metrics = evaluate_live_case(
        case,
        hy3_service=_TargetSignalLiveService(
            target_claim_id="dev-02-c03",
            signal=signal,
        ),
    )["metrics"]

    assert metrics["known_error_detected"] is expected


def test_known_error_detection_maps_target_sentence_to_sorted_claim_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import eval.run_eval as run_eval_module

    case = next(
        case
        for case in load_mode_cases("calibrate")
        if case.case_id == "quality:dev-02:medium"
    )
    source_blocks, bundle = materialize_live_case(case)
    target_claim = next(
        claim for claim in bundle.claims if claim.sentence_id == "dev-02-s03"
    )
    additional_claim = target_claim.model_copy(
        update={"claim_id": "dev-02-c03-extra"}
    )
    modified_bundle = bundle.model_copy(
        update={"claims": [additional_claim, *bundle.claims]}
    )
    monkeypatch.setattr(
        run_eval_module,
        "materialize_live_case",
        lambda _case: (source_blocks, modified_bundle),
    )

    metrics = evaluate_live_case(
        case,
        hy3_service=_FakeLiveService(),
    )["metrics"]

    assert metrics["known_error_target_claim_ids"] == [
        "dev-02-c03",
        "dev-02-c03-extra",
    ]


def test_good_quality_has_no_known_error_detection_metrics() -> None:
    case = next(
        case
        for case in load_mode_cases("calibrate")
        if case.case_id == "quality:dev-01:good"
    )

    metrics = evaluate_live_case(
        case,
        hy3_service=_FakeLiveService(),
    )["metrics"]

    assert not any(key.startswith("known_error_") for key in metrics)


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


def _revision_metrics_fixture(
    *, after_a="Sensor A measured 10 units.", after_b="Sensor B measured 30 units.",
    severe_before=("a",), severe_after=(),
):
    from backend.app.models import AtomicClaim, GeneratedBundle, SectionId, SemanticJudgment, SourceBlock

    sources = [SourceBlock(
        block_id=f"synthetic-block-{name}", page_index=0, type="text", text=text,
        reading_order=index, parser="mineru", parser_version="synthetic",
    ) for index, (name, text) in enumerate((
        ("a", "Sensor A measured 10 units."), ("b", "Sensor B measured 30 units."),
    ))]

    def bundle(texts, suffix):
        claims = [AtomicClaim(
            claim_id=f"synthetic-{name}{suffix}", sentence_id="synthetic-target",
            text=text, claim_type="result", importance="critical", qualifiers=[],
            numeric_entities=[], auditability="auditable",
            candidate_block_ids=[source.block_id], candidate_quote=source.text,
        ) for name, text, source in zip(("a", "b"), texts, sources)]
        return GeneratedBundle.model_validate({
            "document": {"title": "Synthetic revision test", "sections": [
                {"section_id": section.value, "heading": section.value, "sentences": [{
                    "sentence_id": "synthetic-target" if section.value == "results" else f"synthetic-{section.value}",
                    "text": " ".join(texts) if section.value == "results" else "Synthetic background.",
                }]} for section in SectionId
            ]}, "claims": claims,
        })

    before = bundle(("Sensor A measured 20 units.", "Sensor B measured 30 units."), "")
    after = bundle((after_a, after_b), "-rebuilt")

    def result(candidate, severe_names):
        return DeepAuditResult(semantic_judgments=[SemanticJudgment(
            claim_id=claim.claim_id, block_id=claim.candidate_block_ids[0],
            relation="contradicts" if name in severe_names else "supports",
            scope_status="preserved", terminology_status="correct",
            severity="critical" if name in severe_names else "none",
            reason="SYNTHETIC_PRIVATE_REASON",
        ) for name, claim in zip(("a", "b"), candidate.claims)], risk_findings=[])

    before_evidence, _ = AuditService().quick_check(before, sources)
    after_evidence, _ = AuditService().quick_check(after, sources)
    inputs = dict(
        case=EvaluationCase(case_id="revision:synthetic:bad", run_index=0, payload={
            "case_group": "revision", "paper_id": "synthetic", "quality_label": "bad",
        }),
        before_bundle=before, after_bundle=after, target_sentence_id="synthetic-target",
        mutation={"replacement_text": " ".join(claim.text for claim in before.claims),
                  "target_sentence_id": "synthetic-target"},
        before_result=result(before, severe_before), after_result=result(after, severe_after),
        before_report=SimpleNamespace(overall_score=50), after_report=SimpleNamespace(overall_score=75),
        before_evidence=before_evidence, after_evidence=after_evidence,
    )
    return inputs, sources


@pytest.mark.parametrize("duplicate_and_reorder", [False, True])
def test_revision_metrics_fail_closed_new_error_cannot_cancel_resolved_old_error(duplicate_and_reorder):
    import eval.run_eval as runner

    inputs, _ = _revision_metrics_fixture(after_b="Sensor B measured 40 units.", severe_after=("b",))
    if duplicate_and_reorder:
        for stage in ("before", "after"):
            inputs[f"{stage}_bundle"].claims.reverse()
            inputs[f"{stage}_evidence"] = list(reversed(inputs[f"{stage}_evidence"] * 2))
            inputs[f"{stage}_result"].semantic_judgments.reverse()
    metrics = runner._revision_metrics(**inputs)
    assert metrics["new_severe_error_count"] == 1
    assert metrics["resolved_issue_count"] == 0


@pytest.mark.parametrize("copies", [1, 2])
def test_revision_metrics_fail_closed_old_issue_survives_claim_rebuild_without_double_count(copies):
    import eval.run_eval as runner

    inputs, _ = _revision_metrics_fixture(after_a="Sensor A measured 20 units.", severe_after=("a",))
    for index in range(1, copies):
        original = inputs["after_bundle"].claims[0]
        new_id = f"rebuilt-duplicate-{index}"
        inputs["after_bundle"].claims.append(original.model_copy(update={"claim_id": new_id}))
        inputs["after_evidence"].append(inputs["after_evidence"][0].model_copy(update={"claim_id": new_id}))
        judgment = inputs["after_result"].semantic_judgments[0]
        inputs["after_result"].semantic_judgments.append(judgment.model_copy(update={"claim_id": new_id}))
    inputs["after_bundle"].claims.reverse()
    inputs["after_evidence"] = list(reversed(inputs["after_evidence"] * 2))
    inputs["after_result"].semantic_judgments.reverse()
    assert runner._revision_metrics(**inputs)["new_severe_error_count"] == 0


def test_revision_metrics_fail_closed_rebuilt_new_issue_and_duplicate_evidence_count_once():
    import eval.run_eval as runner

    inputs, _ = _revision_metrics_fixture(after_b="Sensor B measured 40 units.", severe_after=("b",))
    duplicate_id = "new-issue-rebuilt-again"
    inputs["after_bundle"].claims.append(inputs["after_bundle"].claims[1].model_copy(update={"claim_id": duplicate_id}))
    inputs["after_evidence"].append(inputs["after_evidence"][1].model_copy(update={"claim_id": duplicate_id}))
    inputs["after_result"].semantic_judgments.append(
        inputs["after_result"].semantic_judgments[1].model_copy(update={"claim_id": duplicate_id})
    )
    inputs["after_evidence"] *= 2
    assert runner._revision_metrics(**inputs)["new_severe_error_count"] == 1


@pytest.mark.parametrize("damage", ["rewritten_old", "one_to_many", "changed_evidence", "judgment_only_drift"])
def test_revision_metrics_fail_closed_ambiguous_identity_is_not_a_zero_or_new_count(damage):
    import eval.run_eval as runner

    inputs, _ = _revision_metrics_fixture(after_a="Sensor A measured 21 units.", severe_after=("a",))
    if damage == "one_to_many":
        extra = inputs["after_bundle"].claims[0].model_copy(update={
            "claim_id": "split-claim", "text": "Sensor A measured 22 units.",
        })
        inputs["after_bundle"].claims.append(extra)
        inputs["after_evidence"].append(inputs["after_evidence"][0].model_copy(update={"claim_id": extra.claim_id}))
        inputs["after_result"].semantic_judgments.append(
            inputs["after_result"].semantic_judgments[0].model_copy(update={"claim_id": extra.claim_id})
        )
    elif damage == "changed_evidence":
        inputs["after_bundle"].claims[0].text = inputs["before_bundle"].claims[0].text
        inputs["after_evidence"][0].quote = "A different synthetic evidence fragment."
    elif damage == "judgment_only_drift":
        inputs, _ = _revision_metrics_fixture(severe_before=(), severe_after=("b",))
    with pytest.raises(ValueError, match="^EVALUATION_FAILED$"):
        runner._revision_metrics(**inputs)


@pytest.mark.parametrize("flag", [
    "NUMBER_MISMATCH:20", "UNIT_MISMATCH:ms", "NEGATION_MISMATCH", "COMPARISON_DIRECTION_MISMATCH",
])
def test_revision_metrics_fail_closed_deterministic_conflict_overrides_positive_judgment(flag):
    import eval.run_eval as runner

    inputs, _ = _revision_metrics_fixture(after_a="Sensor A measured 20 units in this test.")
    # Metrics consume verified rule results; only this synthetic flag varies.
    inputs["after_evidence"][0].rule_flags = [flag]
    assert inputs["after_evidence"][0].quote_verified
    assert runner._revision_metrics(**inputs)["resolved_issue_count"] == 0


@pytest.mark.parametrize("missing", [
    "all_judgments", "one_claim_judgment", "one_pair_judgment", "one_claim_evidence",
    "all_target_claims", "unverified_evidence", "non_auditable_claim", "unchanged_sentence",
])
def test_revision_metrics_fail_closed_incomplete_target_coverage_is_not_resolved(missing):
    from backend.app.models import Auditability
    import eval.run_eval as runner

    inputs, _ = _revision_metrics_fixture()
    if missing == "all_judgments":
        inputs["after_result"].semantic_judgments = []
    elif missing == "one_claim_judgment":
        inputs["after_result"].semantic_judgments.pop()
    elif missing == "one_pair_judgment":
        inputs["after_evidence"].append(inputs["after_evidence"][0].model_copy(update={"block_id": "another-block"}))
    elif missing == "one_claim_evidence":
        inputs["after_evidence"].pop()
    elif missing == "all_target_claims":
        inputs["after_bundle"].claims = []
    elif missing == "unverified_evidence":
        inputs["after_evidence"][0].quote_verified = False
    elif missing == "non_auditable_claim":
        inputs["after_bundle"].claims[0].auditability = Auditability.NON_AUDITABLE
    else:
        runner._find_sentence(inputs["after_bundle"], "synthetic-target").text = inputs["mutation"]["replacement_text"]
    assert runner._revision_metrics(**inputs)["resolved_issue_count"] == 0


@pytest.mark.parametrize("fallback", [False, True])
def test_revision_metrics_fail_closed_genuine_supported_repair_still_resolves(fallback):
    from backend.app.models import MatchMethod
    import eval.run_eval as runner

    inputs, _ = _revision_metrics_fixture()
    if fallback:
        for record in inputs["after_evidence"]:
            record.match_method = MatchMethod.BM25_FALLBACK
            record.rule_flags = ["CANDIDATE_QUOTE_MISSING:synthetic", "CANDIDATE_BLOCK_NOT_FOUND:missing"]
    metrics = runner._revision_metrics(**inputs)
    assert metrics["known_issue_count"] == metrics["resolved_issue_count"] == 1
    assert metrics["new_severe_error_count"] == 0
    assert metrics["irrelevant_change"] is False


class _SyntheticRevisionMetricsService(_FakeLiveService):
    def __init__(self, inputs):
        super().__init__()
        self.inputs = inputs

    def deep_audit(self, *, document, claim_evidence_pairs):
        first = not self.calls
        result = super().deep_audit(document=document, claim_evidence_pairs=claim_evidence_pairs)
        result.semantic_judgments = self.inputs["before_result" if first else "after_result"].semantic_judgments
        return result

    def revise_sentence(self, **kwargs):
        import eval.run_eval as runner

        patch = super().revise_sentence(**kwargs)
        patch.after_text = runner._find_sentence(self.inputs["after_bundle"], "synthetic-target").text
        return patch

    def regenerate_sentence_claims(self, **_kwargs):
        from backend.app.models import SentenceClaimRegenerationResult

        self.calls.append("sentence_claims")
        self._observe("sentence_claims")
        return SentenceClaimRegenerationResult(claims=self.inputs["after_bundle"].claims)


def _isolate_revision_metrics_evaluation(monkeypatch, inputs, sources):
    import eval.run_eval as runner

    monkeypatch.setattr(runner, "materialize_live_case", lambda _: (copy.deepcopy(sources), inputs["before_bundle"].model_copy(deep=True)))
    monkeypatch.setattr(runner, "_load_live_manifest", lambda: {
        "papers": [{"paper_id": "synthetic", "quality_mutations": {"bad": inputs["mutation"]}}],
    })
    return runner


def test_revision_metrics_fail_closed_indeterminate_preserves_observed_cost(monkeypatch):
    inputs, sources = _revision_metrics_fixture(after_a="Sensor A measured 21 units.", severe_after=("a",))
    runner = _isolate_revision_metrics_evaluation(monkeypatch, inputs, sources)
    service = _SyntheticRevisionMetricsService(inputs)
    with pytest.raises(EvaluationCaseError) as raised:
        runner.evaluate_live_case(inputs["case"], hy3_service=service)
    assert str(raised.value) == "EVALUATION_FAILED"
    assert raised.value.status == "failed"
    assert raised.value.provider_calls == 4
    assert raised.value.usage == {"prompt_tokens": 400, "completion_tokens": 160, "total_tokens": 560}
    assert service.calls == ["deep_audit", "revision", "sentence_claims", "deep_audit"]


def test_revision_metrics_fail_closed_does_not_swallow_unrelated_metric_errors(monkeypatch):
    inputs, sources = _revision_metrics_fixture()
    runner = _isolate_revision_metrics_evaluation(monkeypatch, inputs, sources)

    def unrelated_error(**_kwargs):
        raise ValueError("synthetic unrelated error")

    monkeypatch.setattr(runner, "_revision_metrics", unrelated_error)
    with pytest.raises(ValueError, match="^synthetic unrelated error$"):
        runner.evaluate_live_case(inputs["case"], hy3_service=_SyntheticRevisionMetricsService(inputs))


def test_revision_metrics_fail_closed_failed_jsonl_cannot_complete_revision_gates(tmp_path, monkeypatch):
    inputs, sources = _revision_metrics_fixture()
    uncertain, _ = _revision_metrics_fixture(after_a="Sensor A measured 21 units.", severe_after=("a",))
    runner = _isolate_revision_metrics_evaluation(monkeypatch, inputs, sources)
    path, report_path = tmp_path / "results.jsonl", tmp_path / "report.md"
    freeze_path = tmp_path / "freeze.json"
    freeze_path.write_text(json.dumps(_report_freeze(VERSIONS.code_version)), encoding="utf-8")
    monkeypatch.setattr(build_report_module, "DEFAULT_FREEZE_PATH", freeze_path)
    cases = [EvaluationCase(case_id=f"revision:synthetic-{i}:bad", run_index=0, payload=inputs["case"].payload) for i in range(5)]
    services = {case.case_id: _SyntheticRevisionMetricsService(uncertain if i == 4 else inputs) for i, case in enumerate(cases)}
    runner.run_cases(mode="final", cases=cases, output_path=path, versions=VERSIONS,
                     evaluate=lambda case: runner.evaluate_live_case(case, hy3_service=services[case.case_id]))
    records = _read_jsonl(path)
    assert records[-1]["status"] == "failed"
    assert records[-1]["error_code"] == "EVALUATION_FAILED"
    assert records[-1]["provider_calls"] == 4
    assert records[-1]["usage"] == {"prompt_tokens": 400, "completion_tokens": 160, "total_tokens": 560}
    assert records[-1]["metrics"] == {}
    summary = build_report(input_path=path, output_path=report_path)
    assert summary["revisions"]["completed"] == 4
    assert summary["status_counts"] == {"failed": 1, "succeeded": 4}
    assert summary["provider_calls_known"] == 20
    assert summary["usage"]["total_tokens"] == 2800
    for name in ("revision_error_resolution", "revision_new_severe_errors", "revision_irrelevant_change_rate"):
        assert summary["acceptance_gates"]["gates"][name]["status"] == "not_available"
    before = path.read_bytes()
    runner.run_cases(mode="final", cases=cases, output_path=path, versions=VERSIONS,
                     evaluate=lambda _: pytest.fail("completed_failure_replayed_provider"))
    assert path.read_bytes() == before
    assert build_report(input_path=path, output_path=tmp_path / "rebuilt.md") == summary
    assert (tmp_path / "rebuilt.md").read_bytes() == report_path.read_bytes()
    persisted = path.read_text(encoding="utf-8") + report_path.read_text(encoding="utf-8")
    for private in ("SYNTHETIC_PRIVATE_REASON", "Sensor A measured", "Sensor B measured", "candidate_quote", "replacement_text"):
        assert private not in persisted


def _revision_evidence_reanchor_fixture(*, rebuild_and_reorder=False):
    import eval.run_eval as runner

    inputs, sources = _revision_metrics_fixture(
        after_a="Sensor A measured 20 units.", after_b="Sensor A measured 10 units.",
        severe_after=("a",),
    )
    sources[1].text = sources[0].text
    for stage in ("before", "after"):
        bundle = inputs[f"{stage}_bundle"]
        bundle.claims[1].text = sources[0].text
        bundle.claims[1].candidate_quote = sources[0].text
        if stage == "after":
            bundle.claims[0].candidate_block_ids = [sources[1].block_id]
        judgments = inputs[f"{stage}_result"].semantic_judgments
        for index, (claim, judgment) in enumerate(zip(bundle.claims, judgments)):
            if stage == "after":
                claim.claim_id = f"fresh-claim-{index}" if rebuild_and_reorder else inputs["before_bundle"].claims[index].claim_id
                judgment.claim_id = claim.claim_id
            judgment.block_id = claim.candidate_block_ids[0]
        sentence = runner._find_sentence(bundle, "synthetic-target")
        sentence.text = " ".join(claim.text for claim in bundle.claims)
        if stage == "after":
            sentence.text = "In this synthetic test, " + sentence.text
        evidence, _ = AuditService().quick_check(bundle, sources)
        inputs[f"{stage}_evidence"] = evidence
        assert all(record.quote_verified for record in evidence)
        assert {(record.claim_id, record.block_id) for record in evidence} == {
            (judgment.claim_id, judgment.block_id) for judgment in judgments
        }
    before_a, after_a = inputs["before_bundle"].claims[0], inputs["after_bundle"].claims[0]
    assert (before_a.text, before_a.qualifiers, before_a.numeric_entities) == (
        after_a.text, after_a.qualifiers, after_a.numeric_entities,
    )
    assert inputs["before_evidence"][0].block_id != inputs["after_evidence"][0].block_id
    assert inputs["before_evidence"][0].quote == inputs["after_evidence"][0].quote == inputs["before_evidence"][1].quote
    assert inputs["before_evidence"][1].rule_flags == []
    inputs["mutation"]["replacement_text"] = runner._find_sentence(inputs["before_bundle"], "synthetic-target").text
    if rebuild_and_reorder:
        for stage in ("before", "after"):
            inputs[f"{stage}_bundle"].claims.reverse()
            inputs[f"{stage}_evidence"].reverse()
            inputs[f"{stage}_result"].semantic_judgments.reverse()
    return inputs, sources


@pytest.mark.parametrize("rebuild_and_reorder", [False, True])
def test_revision_metrics_fail_closed_evidence_reanchor_is_indeterminate(rebuild_and_reorder):
    import eval.run_eval as runner

    inputs, _ = _revision_evidence_reanchor_fixture(rebuild_and_reorder=rebuild_and_reorder)
    with pytest.raises(ValueError, match="^EVALUATION_FAILED$"):
        runner._revision_metrics(**inputs)


def test_revision_metrics_fail_closed_evidence_reanchor_preserves_four_observed_calls(monkeypatch):
    inputs, sources = _revision_evidence_reanchor_fixture(rebuild_and_reorder=True)
    runner = _isolate_revision_metrics_evaluation(monkeypatch, inputs, sources)
    service = _SyntheticRevisionMetricsService(inputs)
    with pytest.raises(EvaluationCaseError) as raised:
        runner.evaluate_live_case(inputs["case"], hy3_service=service)
    assert raised.value.status == "failed"
    assert str(raised.value) == raised.value.error_code == "EVALUATION_FAILED"
    assert raised.value.provider_calls == 4
    assert raised.value.usage == {"prompt_tokens": 400, "completion_tokens": 160, "total_tokens": 560}
    assert service.calls == ["deep_audit", "revision", "sentence_claims", "deep_audit"]


def test_revision_metrics_fail_closed_evidence_reanchor_does_not_merge_by_quote_alone():
    from backend.app.models import Relation, Severity
    import eval.run_eval as runner

    inputs, sources = _revision_evidence_reanchor_fixture()
    # Keep the old problem on X and introduce an independent numeric error on Y.
    inputs["after_bundle"].claims[0].candidate_block_ids = [sources[0].block_id]
    inputs["after_result"].semantic_judgments[0].block_id = sources[0].block_id
    inputs["after_bundle"].claims[1].text = "Sensor A measured 40 units."
    inputs["after_result"].semantic_judgments[1].relation = Relation.CONTRADICTS
    inputs["after_result"].semantic_judgments[1].severity = Severity.CRITICAL
    runner._find_sentence(inputs["after_bundle"], "synthetic-target").text = " ".join(
        claim.text for claim in inputs["after_bundle"].claims
    )
    inputs["after_evidence"], _ = AuditService().quick_check(inputs["after_bundle"], sources)
    assert runner._revision_metrics(**inputs)["new_severe_error_count"] == 1


def test_revision_metrics_fail_closed_evidence_reanchor_jsonl_cannot_complete_gates(tmp_path, monkeypatch):
    from backend.app.models import Relation, Severity

    uncertain, sources = _revision_evidence_reanchor_fixture()
    repaired = copy.deepcopy(uncertain)
    repaired["after_bundle"].claims[0].text = sources[0].text
    repaired["after_result"].semantic_judgments[0].relation = Relation.SUPPORTS
    repaired["after_result"].semantic_judgments[0].severity = Severity.NONE
    runner = _isolate_revision_metrics_evaluation(monkeypatch, uncertain, sources)
    runner._find_sentence(repaired["after_bundle"], "synthetic-target").text = " ".join(
        claim.text for claim in repaired["after_bundle"].claims
    )
    path, output = tmp_path / "reanchor.jsonl", tmp_path / "reanchor.md"
    freeze_path = tmp_path / "freeze.json"
    freeze_path.write_text(json.dumps(_report_freeze(VERSIONS.code_version)), encoding="utf-8")
    monkeypatch.setattr(build_report_module, "DEFAULT_FREEZE_PATH", freeze_path)
    cases = [EvaluationCase(case_id=f"revision:reanchor-{i}:bad", run_index=0, payload=uncertain["case"].payload) for i in range(5)]
    services = {case.case_id: _SyntheticRevisionMetricsService(uncertain if i == 4 else repaired) for i, case in enumerate(cases)}
    runner.run_cases(mode="final", cases=cases, output_path=path, versions=VERSIONS,
                     evaluate=lambda case: runner.evaluate_live_case(case, hy3_service=services[case.case_id]))
    records = _read_jsonl(path)
    failed = records[-1]
    assert (failed["status"], failed["error_code"], failed["metrics"]) == ("failed", "EVALUATION_FAILED", {})
    assert failed["provider_calls"] == 4
    assert failed["usage"] == {"prompt_tokens": 400, "completion_tokens": 160, "total_tokens": 560}
    summary = build_report(input_path=path, output_path=output)
    assert summary["status_counts"] == {"failed": 1, "succeeded": 4}
    assert summary["revisions"]["completed"] == 4
    assert summary["provider_calls_known"] == 20
    assert summary["usage"]["total_tokens"] == 2800
    for gate in ("revision_error_resolution", "revision_new_severe_errors", "revision_irrelevant_change_rate"):
        assert summary["acceptance_gates"]["gates"][gate]["status"] == "not_available"
    before = path.read_bytes()
    runner.run_cases(mode="final", cases=cases, output_path=path, versions=VERSIONS,
                     evaluate=lambda _: pytest.fail("reanchor_failure_replayed_provider"))
    assert path.read_bytes() == before
    assert build_report(input_path=path, output_path=tmp_path / "rebuilt.md") == summary
    assert (tmp_path / "rebuilt.md").read_bytes() == output.read_bytes()
    persisted = path.read_text(encoding="utf-8") + output.read_text(encoding="utf-8")
    for private in ("Sensor A measured", "In this synthetic test", "SYNTHETIC_PRIVATE_REASON", "candidate_quote", "replacement_text"):
        assert private not in persisted


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
    assert frozen["prompt_versions"]["deep_audit"] == "audit-v5"
    assert frozen["schema_versions"]["deep_audit"] == "deep-audit-result-v2"
    assert frozen["overall_score_threshold"] == 75
    assert frozen["dimension_weights"]["factual_consistency"] == 0.20
    assert frozen["core_dimension_gates"]["risk_compliance"] == 3
    assert "api_key" not in json.dumps(frozen).casefold()


def test_stage7_freeze_payload_tracks_audit_v5_without_schema_change() -> None:
    payload = _freeze_payload()

    assert payload["prompt_versions"]["deep_audit"] == "audit-v5"
    assert payload["schema_versions"]["deep_audit"] == "deep-audit-result-v2"


class _FrozenRuntimeConfigService:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            hy3_model="hy3",
            hy3_base_url="https://offline.invalid/\u5408\u6210/v1",
            hy3_timeout_seconds=120,
            hy3_max_retries=2,
            paperlens_model_mode="mock",
            hy3_api_key="PRIVATE_KEY_MUST_NOT_BE_FROZEN",
            unrelated_private_setting="PRIVATE_SETTINGS_MUST_NOT_BE_FROZEN",
        )
        self.provider_calls = 0

    def deep_audit(self, **_kwargs):
        self.provider_calls += 1
        pytest.fail("frozen_runtime_config_reached_provider")


def _frozen_runtime_config_setup(tmp_path: Path, monkeypatch):
    import eval.run_eval as runner

    service = _FrozenRuntimeConfigService()
    freeze_path = tmp_path / "stage7_frozen_config.json"
    monkeypatch.setattr(runner, "Hy3Service", lambda: service)
    monkeypatch.setattr(runner, "DEFAULT_FREEZE_PATH", freeze_path)
    frozen = runner.freeze_configuration(output_path=freeze_path)
    return runner, service, freeze_path, frozen


@pytest.mark.parametrize(
    "setting_name, changed_value",
    [
        ("hy3_base_url", "https://changed.invalid/v1"),
        ("hy3_timeout_seconds", 121),
        ("hy3_max_retries", 1),
    ],
)
def test_frozen_runtime_config_payload_tracks_each_provider_setting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    setting_name: str,
    changed_value: object,
) -> None:
    runner, service, _, frozen = _frozen_runtime_config_setup(tmp_path, monkeypatch)
    expected_url_hash = hashlib.sha256(
        service.settings.hy3_base_url.encode("utf-8")
    ).hexdigest()

    assert frozen["freeze_version"] == "paperlens-stage7-freeze-v2"
    assert frozen["provider_config"] == {
        "base_url_sha256": expected_url_hash,
        "timeout_seconds": 120,
        "max_retries": 2,
    }
    setattr(service.settings, setting_name, changed_value)
    assert runner._freeze_payload()["provider_config"] != frozen["provider_config"]

    serialized = json.dumps(frozen, ensure_ascii=False).casefold()
    assert service.settings.hy3_base_url.casefold() not in serialized
    assert "private_key_must_not_be_frozen" not in serialized
    assert "private_settings_must_not_be_frozen" not in serialized
    assert "api_key" not in serialized
    assert service.provider_calls == 0


@pytest.mark.parametrize(
    "setting_name, changed_value",
    [
        ("hy3_base_url", "https://changed.invalid/v1"),
        ("hy3_timeout_seconds", 121),
        ("hy3_max_retries", 1),
    ],
)
def test_frozen_runtime_config_drift_is_rejected_without_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    setting_name: str,
    changed_value: object,
) -> None:
    runner, service, freeze_path, _ = _frozen_runtime_config_setup(
        tmp_path, monkeypatch
    )
    frozen_before = freeze_path.read_bytes()
    setattr(service.settings, setting_name, changed_value)

    with pytest.raises(ValueError, match="^CONFIG_DRIFT$"):
        runner._require_frozen_configuration()
    with pytest.raises(ValueError, match="^CONFIG_DRIFT$"):
        runner.freeze_configuration(output_path=freeze_path)

    assert freeze_path.read_bytes() == frozen_before
    assert service.provider_calls == 0


@pytest.mark.parametrize("mode", ["final", "stability"])
@pytest.mark.parametrize(
    "setting_name, changed_value",
    [
        ("hy3_base_url", "https://changed.invalid/v1"),
        ("hy3_timeout_seconds", 121),
        ("hy3_max_retries", 1),
    ],
)
def test_frozen_runtime_config_cli_refuses_drift_before_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    setting_name: str,
    changed_value: object,
) -> None:
    runner, service, freeze_path, _ = _frozen_runtime_config_setup(
        tmp_path, monkeypatch
    )
    frozen_before = freeze_path.read_bytes()
    output_path = tmp_path / f"{mode}_results.jsonl"
    setattr(service.settings, setting_name, changed_value)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--mode",
            mode,
            "--confirm-cost",
            "--output",
            str(output_path),
        ],
    )

    assert runner.main() == 2
    assert capsys.readouterr().out == "EVAL_REFUSED=CONFIG_DRIFT\n"
    assert service.provider_calls == 0
    assert not output_path.exists()
    assert not pending_path_for(output_path).exists()
    assert not (tmp_path / f"{mode}_report.md").exists()
    assert freeze_path.read_bytes() == frozen_before


def test_frozen_runtime_config_accepts_same_settings_and_keeps_model_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, service, freeze_path, frozen = _frozen_runtime_config_setup(
        tmp_path, monkeypatch
    )
    frozen_before = freeze_path.read_bytes()

    assert runner._require_frozen_configuration() == frozen
    assert runner.freeze_configuration(output_path=freeze_path) == frozen
    assert freeze_path.read_bytes() == frozen_before

    service.settings.hy3_model = "changed-model"
    with pytest.raises(ValueError, match="^CONFIG_DRIFT$"):
        runner._require_frozen_configuration()
    assert freeze_path.read_bytes() == frozen_before
    assert service.provider_calls == 0


@pytest.mark.parametrize("damage", ["legacy_v1", "missing_provider_config"])
def test_frozen_runtime_config_rejects_legacy_or_incomplete_freeze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    runner, service, freeze_path, frozen = _frozen_runtime_config_setup(
        tmp_path, monkeypatch
    )
    damaged = copy.deepcopy(frozen)
    if damage == "legacy_v1":
        damaged["freeze_version"] = "paperlens-stage7-freeze-v1"
    else:
        damaged.pop("provider_config")
    freeze_path.write_text(
        json.dumps(damaged, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    damaged_before = freeze_path.read_bytes()

    with pytest.raises(ValueError, match="^CONFIG_DRIFT$"):
        runner._require_frozen_configuration()

    assert freeze_path.read_bytes() == damaged_before
    assert service.provider_calls == 0


def test_frozen_runtime_config_excludes_mode_and_keeps_cost_live_gates_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner, service, _, frozen = _frozen_runtime_config_setup(tmp_path, monkeypatch)
    service.settings.paperlens_model_mode = "live"
    assert runner._freeze_payload() == {
        key: value for key, value in frozen.items() if key != "created_at"
    }
    runner._require_frozen_configuration()

    output_path = tmp_path / "final_results.jsonl"
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_eval.py", "--mode", "final", "--output", str(output_path)],
    )
    assert runner.main() == 2
    assert "COST_CONFIRMATION_REQUIRED=True" in capsys.readouterr().out
    assert not output_path.exists()
    assert service.provider_calls == 0

    service.settings.paperlens_model_mode = "mock"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--mode",
            "final",
            "--confirm-cost",
            "--output",
            str(output_path),
        ],
    )
    assert runner.main() == 2
    assert "EVAL_REFUSED=LIVE_MODE_REQUIRED" in capsys.readouterr().out
    assert not output_path.exists()
    assert not pending_path_for(output_path).exists()
    assert service.provider_calls == 0
    assert runner.SUPPORTED_MODES == ("smoke", "calibrate", "final", "stability")
    assert len(runner.context_diagnostic_cases(_context_diagnostic_setup()[3])) == 9


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


_KNOWN_ERROR_METRIC_FIELDS = {
    "known_error_type",
    "known_error_severity",
    "known_error_target_sentence_id",
    "known_error_target_claim_ids",
    "known_error_detected",
}


def _known_error_detection_record(
    index: int,
    *,
    detected: bool,
    severity: str = "critical",
    quality_label: str = "bad",
) -> dict[str, object]:
    paper_id = f"known-error-{index:02d}"
    claim_id = f"{paper_id}-claim"
    diagnostic = _report_diagnostic_item(claim_id, f"{paper_id}-block")
    if detected:
        diagnostic["relation"] = "contradicts"
        diagnostic["severity"] = severity
    record = _existing_result()
    record.update(
        {
            "case_id": f"quality:{paper_id}:{quality_label}",
            "mode": "calibrate",
            "metrics": {
                "case_group": "quality",
                "paper_id": paper_id,
                "quality_label": quality_label,
                "overall_score": 50 if detected else 80,
                "decision": "needs_revision",
                "hard_failure_count": int(detected),
                "key_claim_count": 1,
                "key_claim_citation_accuracy_numerator": int(not detected),
                "key_claim_citation_accuracy_denominator": 1,
                "key_claim_citation_completeness_numerator": int(not detected),
                "key_claim_citation_completeness_denominator": 1,
                "key_claim_diagnostics": [diagnostic],
                "known_error_type": "synthetic_factual_error",
                "known_error_severity": severity,
                "known_error_target_sentence_id": f"{paper_id}-sentence",
                "known_error_target_claim_ids": [claim_id],
                "known_error_detected": detected,
            },
        }
    )
    return record


def _without_known_error_detection_metrics(
    record: dict[str, object],
) -> dict[str, object]:
    legacy = json.loads(json.dumps(record))
    metrics = legacy["metrics"]
    assert isinstance(metrics, dict)
    for field in _KNOWN_ERROR_METRIC_FIELDS:
        metrics.pop(field)
    return legacy


def _non_succeeded_known_error_record(
    index: int,
    *,
    status: str,
    error_code: str,
    quality_label: str = "bad",
) -> dict[str, object]:
    record = _existing_result()
    record.update(
        {
            "case_id": f"quality:failed-known-error-{index:02d}:{quality_label}",
            "mode": "calibrate",
            "status": status,
            "error_code": error_code,
            "provider_calls": 0 if status == "unsupported" else 1,
            "metrics": {},
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


def test_known_error_detection_legacy_records_are_not_available() -> None:
    legacy = _without_known_error_detection_metrics(
        _known_error_detection_record(1, detected=True)
    )

    summary = summarize_results([legacy])

    assert summary["known_error_detection"] == {
        "status": "not_available",
        "records_with_metrics": 0,
        "eligible_records": 1,
        "known_error_numerator": 0,
        "known_error_denominator": 0,
        "known_error_detection_rate": None,
        "severe_error_numerator": 0,
        "severe_error_denominator": 0,
        "severe_error_detection_rate": None,
    }


def test_known_error_detection_mixed_records_are_partial() -> None:
    current = _known_error_detection_record(1, detected=True)
    legacy = _without_known_error_detection_metrics(
        _known_error_detection_record(2, detected=False)
    )

    summary = summarize_results([current, legacy])

    assert summary["known_error_detection"] == {
        "status": "partial",
        "records_with_metrics": 1,
        "eligible_records": 2,
        "known_error_numerator": 1,
        "known_error_denominator": 1,
        "known_error_detection_rate": 1.0,
        "severe_error_numerator": 1,
        "severe_error_denominator": 1,
        "severe_error_detection_rate": 1.0,
    }


def test_known_error_detection_non_succeeded_records_make_gate_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_freeze_path = (
        Path(__file__).resolve().parent
        / f"_stage7_failed_known_error_missing_freeze_{time.time_ns()}.json"
    )
    assert not missing_freeze_path.exists()
    monkeypatch.setattr(
        build_report_module,
        "DEFAULT_FREEZE_PATH",
        missing_freeze_path,
    )
    succeeded = [
        _known_error_detection_record(index, detected=True)
        for index in range(1, 6)
    ]
    non_succeeded = [
        _non_succeeded_known_error_record(
            6,
            status="failed",
            error_code="HY3_UNAVAILABLE",
        ),
        _non_succeeded_known_error_record(
            7,
            status="timeout",
            error_code="TIMEOUT",
        ),
        _non_succeeded_known_error_record(
            8,
            status="unsupported",
            error_code="UNSUPPORTED_CASE",
        ),
        _non_succeeded_known_error_record(
            9,
            status="failed",
            error_code="RUN_INTERRUPTED",
        ),
        _non_succeeded_known_error_record(
            10,
            status="failed",
            error_code="AUDIT_INCOMPLETE",
        ),
    ]
    failed_good = _non_succeeded_known_error_record(
        11,
        status="failed",
        error_code="HY3_UNAVAILABLE",
        quality_label="good",
    )
    failed_attack = _non_succeeded_known_error_record(
        12,
        status="failed",
        error_code="HY3_UNAVAILABLE",
    )
    failed_attack.update(
        {
            "case_id": "attack:failed-attack:attack",
            "mode": "final",
        }
    )

    summary = summarize_results(
        [*succeeded, *non_succeeded, failed_good, failed_attack]
    )
    detection = summary["known_error_detection"]
    gate = summary["acceptance_gates"]["gates"]["severe_error_detection"]

    assert {
        "eligible_records": detection["eligible_records"],
        "records_with_metrics": detection["records_with_metrics"],
        "status": detection["status"],
        "gate_status": gate["status"],
        "gate_observed": gate["observed"],
    } == {
        "eligible_records": 10,
        "records_with_metrics": 5,
        "status": "partial",
        "gate_status": "not_available",
        "gate_observed": None,
    }


def test_known_error_detection_non_succeeded_records_all_fail_remain_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_freeze_path = (
        Path(__file__).resolve().parent
        / f"_stage7_all_failed_known_error_missing_freeze_{time.time_ns()}.json"
    )
    assert not missing_freeze_path.exists()
    monkeypatch.setattr(
        build_report_module,
        "DEFAULT_FREEZE_PATH",
        missing_freeze_path,
    )
    records = [
        _non_succeeded_known_error_record(
            1,
            status="failed",
            error_code="HY3_UNAVAILABLE",
            quality_label="medium",
        ),
        _non_succeeded_known_error_record(
            2,
            status="timeout",
            error_code="TIMEOUT",
        ),
        _non_succeeded_known_error_record(
            3,
            status="unsupported",
            error_code="UNSUPPORTED_CASE",
        ),
        _non_succeeded_known_error_record(
            4,
            status="failed",
            error_code="RUN_INTERRUPTED",
        ),
    ]

    summary = summarize_results(records)
    detection = summary["known_error_detection"]
    gate = summary["acceptance_gates"]["gates"]["severe_error_detection"]

    assert detection["eligible_records"] == 4
    assert detection["records_with_metrics"] == 0
    assert detection["known_error_numerator"] == 0
    assert detection["severe_error_numerator"] == 0
    assert detection["status"] == "not_available"
    assert gate["status"] == "not_available"
    assert gate["observed"] is None


@pytest.mark.parametrize(
    ("malformation", "value"),
    (
        ("missing", None),
        ("type", "yes"),
        ("contradiction", False),
    ),
)
def test_known_error_detection_malformed_metrics_fail_closed(
    malformation: str,
    value: object,
) -> None:
    record = _known_error_detection_record(1, detected=True)
    metrics = record["metrics"]
    assert isinstance(metrics, dict)
    if malformation == "missing":
        metrics.pop("known_error_detected")
    elif malformation == "type":
        metrics["known_error_target_claim_ids"] = value
    else:
        metrics["known_error_detected"] = value

    with pytest.raises(
        ValueError,
        match="invalid known_error_detection metrics",
    ):
        summarize_results([record])


@pytest.mark.parametrize(
    ("detected_count", "expected_status"),
    (
        (4, "failed"),
        (5, "passed"),
    ),
)
def test_severe_error_detection_gate_uses_five_record_minimum(
    monkeypatch: pytest.MonkeyPatch,
    detected_count: int,
    expected_status: str,
) -> None:
    missing_freeze_path = (
        Path(__file__).resolve().parent
        / f"_stage7_known_error_missing_freeze_{time.time_ns()}.json"
    )
    assert not missing_freeze_path.exists()
    monkeypatch.setattr(
        build_report_module,
        "DEFAULT_FREEZE_PATH",
        missing_freeze_path,
    )
    records = [
        _known_error_detection_record(
            index,
            detected=index <= detected_count,
        )
        for index in range(1, 6)
    ]

    summary = summarize_results(records)
    gate = summary["acceptance_gates"]["gates"]["severe_error_detection"]

    assert STAGE7_ACCEPTANCE_TARGETS["severe_error_detection_min"] == 0.90
    assert gate == {
        "status": expected_status,
        "observed": detected_count / 5,
        "target": 0.90,
        "denominator": 5,
        "operator": ">=",
    }


def test_known_error_detection_report_numbers_are_rebuilt_from_jsonl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "known_error_results.jsonl"
    output_path = tmp_path / "known_error_report.md"
    missing_freeze_path = tmp_path / "missing_freeze.json"
    monkeypatch.setattr(
        build_report_module,
        "DEFAULT_FREEZE_PATH",
        missing_freeze_path,
    )
    records = [
        _known_error_detection_record(1, detected=True, severity="major"),
        _known_error_detection_record(
            2,
            detected=False,
            severity="minor",
            quality_label="medium",
        ),
    ]
    good = _report_quality_diagnostics_record(
        diagnostics=[_report_diagnostic_item("good-claim", "good-block")],
    )
    good_metrics = good["metrics"]
    assert isinstance(good_metrics, dict)
    good_metrics["key_claim_citation_accuracy_numerator"] = 1
    good_metrics["key_claim_citation_completeness_numerator"] = 1
    records.append(good)
    input_path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )

    summary = build_report(input_path=input_path, output_path=output_path)

    assert summary["known_error_detection"] == {
        "status": "present",
        "records_with_metrics": 2,
        "eligible_records": 2,
        "known_error_numerator": 1,
        "known_error_denominator": 2,
        "known_error_detection_rate": 0.5,
        "severe_error_numerator": 1,
        "severe_error_denominator": 1,
        "severe_error_detection_rate": 1.0,
    }
    markdown = output_path.read_text(encoding="utf-8")
    assert "## Known-error detection" in markdown
    assert "1/2 (0.5)" in markdown
    assert "1/1 (1.0)" in markdown
    assert "known_error_detection=present" in markdown
    known_error_section = markdown.split("## Known-error detection", 1)[1].split(
        "## Key-claim citation coverage",
        1,
    )[0]
    for forbidden in (
        "replacement_text",
        "paper_text",
        "prompt",
        "reason",
        "raw_response",
        "api_key",
    ):
        assert forbidden not in known_error_section.casefold()


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
