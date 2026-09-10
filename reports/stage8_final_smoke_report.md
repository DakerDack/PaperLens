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
- Code versions: workspace-content-f5fd7b29d88de538c1868cedf70b786f64c291866faaede1aa00b498b3099f10

## Sample selection and licenses

- Sample selection metadata: `not_live_data`

## Sample scale rebuilt from JSONL

- Expression diagnostics: not_available
- Quality records/papers: 3/1
- Attack pair records/attack IDs: 4/4
- Revision records: 0
- Stability records/outputs: 4/2

## Acceptance gates

- Overall status: `invalid_freeze`
- Target source: `invalid`

## Quality ordering

- Strict quality ordering: 1/1
- Correct pairwise orderings: 3/3
- Spearman rank correlation (descriptive): 1.0

## Quality calibration diagnostics

| paper_id | quality_label | overall_score | decision | hard_failure_count | non_supported_key_claim_ids | deterministic_issue_codes |
|---|---|---:|---|---:|---|---|
- diagnostics=not_available

## Known-error detection

- Records with known-error metrics: 0/2
- Known-error detection: 0/0 (None)
- Severe known-error detection: 0/0 (None)
- known_error_detection=not_available

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
- Freeze version: `paperlens-stage7-freeze-v2`
- Frozen code matches result code: `False`

## Failed, timed out, unsupported, or interrupted cases

| case_id | run_index | status | error_code |
|---|---:|---|---|
| none | - | - | - |

## Limitations

- Labels are fixed reference answers created by one project author; no inter-annotator agreement is claimed.
- Repeated model runs measure observed repeatability for this fixed configuration, not universal model reliability.
- Correlation or ordering on this small set is descriptive evidence, not absolute validity proof.
