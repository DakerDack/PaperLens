# PaperLens Evaluation Report

This report is rebuilt exclusively from the input JSONL records.

## Run summary

- Attempted: 15
- Known provider calls: 15
- Records with unknown provider calls: 0
- Status `succeeded`: 15
- Mode counts: `calibrate`=15

## Token usage

- Prompt tokens (known subtotal): 46401
- Completion tokens (known subtotal): 8377
- Total tokens (known subtotal): 54778
- Estimated cost from recorded usage (CNY): 0.079909
- Usage pricing basis: input 1 CNY/million tokens, output 4 CNY/million tokens.

## Frozen run metadata

- Modes: calibrate
- Models: hy3
- Prompt versions: audit-v8
- Schema versions: deep-audit-result-v3
- Data versions: paperlens-plos-abstracts-v1
- Code versions: workspace-content-35623d9b0da0b4ad7c1573b12a9c634980058b7248ad3b44187c14d4091dcbd1

## Sample selection and licenses

- Papers: 10 (development 5, holdout 5)
- License names: CC BY

| paper_id | split | DOI | license | source | license evidence |
|---|---|---|---|---|---|
| dev-01 | development | 10.1371/journal.pone.0197002 | CC BY | [official source](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0197002) | [license policy](https://plos.org/open-science-policies/#licenses-and-copyright) |
| dev-02 | development | 10.1371/journal.pone.0069841 | CC BY | [official source](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0069841) | [license policy](https://plos.org/open-science-policies/#licenses-and-copyright) |
| dev-03 | development | 10.1371/journal.pone.0187779 | CC BY | [official source](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0187779) | [license policy](https://plos.org/open-science-policies/#licenses-and-copyright) |
| dev-04 | development | 10.1371/journal.pone.0043007 | CC BY | [official source](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0043007) | [license policy](https://plos.org/open-science-policies/#licenses-and-copyright) |
| dev-05 | development | 10.1371/journal.pmed.1000316 | CC BY | [official source](https://journals.plos.org/plosmedicine/article?id=10.1371/journal.pmed.1000316) | [license policy](https://plos.org/open-science-policies/#licenses-and-copyright) |
| holdout-01 | holdout | 10.1371/journal.pone.0191713 | CC BY | [official source](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0191713) | [license policy](https://plos.org/open-science-policies/#licenses-and-copyright) |
| holdout-02 | holdout | 10.1371/journal.pone.0216362 | CC BY | [official source](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0216362) | [license policy](https://plos.org/open-science-policies/#licenses-and-copyright) |
| holdout-03 | holdout | 10.1371/journal.pone.0263069 | CC BY | [official source](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0263069) | [license policy](https://plos.org/open-science-policies/#licenses-and-copyright) |
| holdout-04 | holdout | 10.1371/journal.pgph.0000016 | CC BY | [official source](https://journals.plos.org/globalpublichealth/article?id=10.1371/journal.pgph.0000016) | [license policy](https://plos.org/open-science-policies/#licenses-and-copyright) |
| holdout-05 | holdout | 10.1371/journal.pbio.1001127 | CC BY | [official source](https://journals.plos.org/plosbiology/article?id=10.1371/journal.pbio.1001127) | [license policy](https://plos.org/open-science-policies/#licenses-and-copyright) |

## Sample scale rebuilt from JSONL

- Expression diagnostics: present
- Quality records/papers: 15/5
- Attack pair records/attack IDs: 0/0
- Revision records: 0
- Stability records/outputs: 0/0

## Acceptance gates


## Document expression diagnostics

| Case | Status | Category | Signal | Sentence IDs | Observed factual alerts |
| --- | --- | --- | --- | --- | --- |
| quality:dev-01:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:dev-01:good | succeeded | unexplained_terminology | not_detected | - | - |
| quality:dev-01:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:dev-01:medium | succeeded | unexplained_terminology | not_detected | - | - |
| quality:dev-01:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:dev-01:bad | succeeded | unexplained_terminology | not_detected | - | - |
| quality:dev-02:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:dev-02:good | succeeded | unexplained_terminology | not_detected | - | - |
| quality:dev-02:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:dev-02:medium | succeeded | unexplained_terminology | not_detected | - | - |
| quality:dev-02:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:dev-02:bad | succeeded | unexplained_terminology | not_detected | - | - |
| quality:dev-03:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:dev-03:good | succeeded | unexplained_terminology | not_detected | - | - |
| quality:dev-03:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:dev-03:medium | succeeded | unexplained_terminology | not_detected | - | - |
| quality:dev-03:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:dev-03:bad | succeeded | unexplained_terminology | not_detected | - | - |
| quality:dev-04:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:dev-04:good | succeeded | unexplained_terminology | not_detected | - | - |
| quality:dev-04:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:dev-04:medium | succeeded | unexplained_terminology | not_detected | - | - |
| quality:dev-04:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:dev-04:bad | succeeded | unexplained_terminology | not_detected | - | - |
| quality:dev-05:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:dev-05:good | succeeded | unexplained_terminology | not_detected | - | - |
| quality:dev-05:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:dev-05:medium | succeeded | unexplained_terminology | not_detected | - | - |
| quality:dev-05:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:dev-05:bad | succeeded | unexplained_terminology | not_detected | - | - |
- Overall status: `incomplete`
- Target source: `frozen`
- `quality_strict_order`: `passed`, observed 4 >= 4 (denominator 5)
- `quality_pairwise`: `passed`, observed 14 >= 13 (denominator 15)
- `severe_error_detection`: `passed`, observed 1.0 >= 0.9 (denominator 5)
- `key_citation_accuracy`: `passed`, observed 0.9167 >= 0.9 (denominator 60)
- `key_citation_completeness`: `passed`, observed 0.9167 >= 0.8 (denominator 60)
- `stability_mean_score_sd`: `not_available`, observed not available <= 5.0 (denominator 36)
- `stability_dimension_consistency`: `not_available`, observed not available >= 0.8 (denominator 36)
- `attack_detection`: `not_available`, observed not available >= 13 (denominator 16)
- `clean_false_positives`: `not_available`, observed not available <= 1 (denominator 16)
- `revision_error_resolution`: `not_available`, observed not available >= 0.7 (denominator 0)
- `revision_new_severe_errors`: `not_available`, observed not available <= 0 (denominator 0)
- `revision_irrelevant_change_rate`: `not_available`, observed not available <= 0.05 (denominator 0)

## Quality ordering

- Strict quality ordering: 4/5
- Correct pairwise orderings: 14/15
- Spearman rank correlation (descriptive): 0.9421

## Quality calibration diagnostics

| paper_id | quality_label | overall_score | decision | hard_failure_count | non_supported_key_claim_ids | deterministic_issue_codes |
|---|---|---:|---|---:|---|---|
| dev-01 | bad | 55.0 | unqualified | 2 | dev-01-c03 | NUMBER_MISMATCH |
| dev-01 | good | 100.0 | qualified | 0 | - | - |
| dev-01 | medium | 83.0 | needs_revision | 0 | - | - |
| dev-02 | bad | 35.25 | unqualified | 3 | dev-02-c03 | - |
| dev-02 | good | 98.0 | qualified | 0 | - | - |
| dev-02 | medium | 83.0 | needs_revision | 0 | - | - |
| dev-03 | bad | 48.0 | unqualified | 2 | dev-03-c03 | - |
| dev-03 | good | 98.0 | qualified | 0 | - | - |
| dev-03 | medium | 98.0 | qualified | 0 | - | - |
| dev-04 | bad | 45.5 | unqualified | 3 | dev-04-c03 | COMPARISON_DIRECTION_MISMATCH |
| dev-04 | good | 100.0 | qualified | 0 | - | - |
| dev-04 | medium | 83.0 | needs_revision | 0 | - | - |
| dev-05 | bad | 35.25 | unqualified | 4 | dev-05-c03 | NUMBER_MISMATCH |
| dev-05 | good | 100.0 | qualified | 0 | - | - |
| dev-05 | medium | 83.0 | needs_revision | 0 | - | - |

## Known-error detection

- Records with known-error metrics: 10/10
- Known-error detection: 9/10 (0.9)
- Severe known-error detection: 5/5 (1.0)
- known_error_detection=present

## Key-claim citation coverage

- Records with citation metrics: 15/15
- Key-claim citation accuracy: 55/60 (0.9167)
- Key-claim citation completeness: 55/60 (0.9167)

## Adversarial pairs

- Not available in these JSONL records.

## Stability

- Not available in these JSONL records.

## Revision effectiveness

- Not available in these JSONL records.

## Configuration freeze

- Status: `present`
- Freeze version: `paperlens-stage7-freeze-v2`
- Frozen code matches result code: `True`

## Failed, timed out, unsupported, or interrupted cases

| case_id | run_index | status | error_code |
|---|---:|---|---|
| none | - | - | - |

## Limitations

- Labels are fixed reference answers created by one project author; no inter-annotator agreement is claimed.
- Repeated model runs measure observed repeatability for this fixed configuration, not universal model reliability.
- Correlation or ordering on this small set is descriptive evidence, not absolute validity proof.
