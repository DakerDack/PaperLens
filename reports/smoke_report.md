# PaperLens Evaluation Report

This report is rebuilt exclusively from the input JSONL records.

## Run summary

- Attempted: 11
- Known provider calls: 0
- Records with unknown provider calls: 0
- Status `succeeded`: 11
- Mode counts: `smoke`=11

## Token usage

- Prompt tokens (known subtotal): 0
- Completion tokens (known subtotal): 0
- Total tokens (known subtotal): 0
- Estimated cost from recorded usage (CNY): 0.0
- Usage pricing basis: input 1 CNY/million tokens, output 4 CNY/million tokens.

## Frozen run metadata

- Modes: smoke
- Models: hy3-reference-replay
- Prompt versions: audit-v2
- Schema versions: deep-audit-result-v2
- Data versions: paperlens-smoke-synthetic-v1
- Code versions: workspace-content-aeb08971985daa5907ee8740e9e37f351249278aa6d22370aa170429003f94be

## Sample selection and licenses

- Sample selection metadata: `not_live_data`

## Sample scale rebuilt from JSONL

- Quality records/papers: 3/1
- Attack pair records/attack IDs: 4/4
- Revision records: 0
- Stability records/outputs: 4/2

## Acceptance gates

- Overall status: `not_available`
- Target source: `frozen`
- `quality_strict_order`: `not_available`, observed not available >= 4 (denominator 5)
- `quality_pairwise`: `not_available`, observed not available >= 13 (denominator 15)
- `key_citation_accuracy`: `not_available`, observed not available >= 0.9 (denominator 0)
- `key_citation_completeness`: `not_available`, observed not available >= 0.8 (denominator 0)
- `stability_mean_score_sd`: `not_available`, observed not available <= 5.0 (denominator 36)
- `stability_dimension_consistency`: `not_available`, observed not available >= 0.8 (denominator 36)
- `attack_detection`: `not_available`, observed not available >= 13 (denominator 16)
- `clean_false_positives`: `not_available`, observed not available <= 1 (denominator 16)
- `revision_error_resolution`: `not_available`, observed not available >= 0.7 (denominator 0)
- `revision_new_severe_errors`: `not_available`, observed not available <= 0 (denominator 0)
- `revision_irrelevant_change_rate`: `not_available`, observed not available <= 0.05 (denominator 0)

## Quality ordering

- Strict quality ordering: 1/1
- Correct pairwise orderings: 3/3
- Spearman rank correlation (descriptive): 1.0

## Quality calibration diagnostics

| paper_id | quality_label | overall_score | decision | hard_failure_count | non_supported_key_claim_ids | deterministic_issue_codes |
|---|---|---:|---|---:|---|---|
- diagnostics=not_available

## Key-claim citation coverage

- Not available in these JSONL records.

## Adversarial pairs

- Attack detection: 2/2
- Clean false positives: 0/2
- `fake_citation`: detected 1/1; clean false positives 0/1
- `numeric_tampering`: detected 1/1; clean false positives 0/1

## Stability

- Stability fixed outputs: 2
- Stability runs: 4
- Mean score standard deviation: 0.0
- Maximum score range: 0.0
- Dimension-level consistency rate: 1.0

## Revision effectiveness

- Not available in these JSONL records.

## Configuration freeze

- Status: `present`
- Freeze version: `paperlens-stage7-freeze-v1`
- Frozen code matches result code: `True`

## Failed, timed out, unsupported, or interrupted cases

| case_id | run_index | status | error_code |
|---|---:|---|---|
| none | - | - | - |

## Limitations

- Labels are fixed reference answers created by one project author; no inter-annotator agreement is claimed.
- Repeated model runs measure observed repeatability for this fixed configuration, not universal model reliability.
- Correlation or ordering on this small set is descriptive evidence, not absolute validity proof.
