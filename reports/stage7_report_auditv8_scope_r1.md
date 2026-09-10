# PaperLens Evaluation Report

This report is rebuilt exclusively from the input JSONL records.

## Run summary

- Attempted: 88
- Known provider calls: 103
- Records with unknown provider calls: 0
- Status `succeeded`: 88
- Mode counts: `final`=52, `stability`=36

## Token usage

- Prompt tokens (known subtotal): 300899
- Completion tokens (known subtotal): 57365
- Total tokens (known subtotal): 358264
- Estimated cost from recorded usage (CNY): 0.530359
- Usage pricing basis: input 1 CNY/million tokens, output 4 CNY/million tokens.

## Frozen run metadata

- Modes: final, stability
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
- Attack pair records/attack IDs: 32/16
- Revision records: 5
- Stability records/outputs: 36/12

## Acceptance gates


## Document expression diagnostics

| Case | Status | Category | Signal | Sentence IDs | Observed factual alerts |
| --- | --- | --- | --- | --- | --- |
| quality:holdout-01:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:holdout-01:good | succeeded | unexplained_terminology | not_detected | - | - |
| quality:holdout-01:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:holdout-01:medium | succeeded | unexplained_terminology | not_detected | - | - |
| quality:holdout-01:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:holdout-01:bad | succeeded | unexplained_terminology | not_detected | - | - |
| quality:holdout-02:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:holdout-02:good | succeeded | unexplained_terminology | not_detected | - | - |
| quality:holdout-02:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:holdout-02:medium | succeeded | unexplained_terminology | not_detected | - | - |
| quality:holdout-02:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:holdout-02:bad | succeeded | unexplained_terminology | not_detected | - | - |
| quality:holdout-03:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:holdout-03:good | succeeded | unexplained_terminology | not_detected | - | - |
| quality:holdout-03:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:holdout-03:medium | succeeded | unexplained_terminology | not_detected | - | - |
| quality:holdout-03:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:holdout-03:bad | succeeded | unexplained_terminology | not_detected | - | - |
| quality:holdout-04:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:holdout-04:good | succeeded | unexplained_terminology | not_detected | - | - |
| quality:holdout-04:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:holdout-04:medium | succeeded | unexplained_terminology | not_detected | - | - |
| quality:holdout-04:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:holdout-04:bad | succeeded | unexplained_terminology | not_detected | - | - |
| quality:holdout-05:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:holdout-05:good | succeeded | unexplained_terminology | not_detected | - | - |
| quality:holdout-05:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:holdout-05:medium | succeeded | unexplained_terminology | not_detected | - | - |
| quality:holdout-05:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| quality:holdout-05:bad | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-length-padding-01:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-length-padding-01:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-length-padding-01:attack | succeeded | redundancy_or_off_topic | detected | dev-01-s05 | - |
| attack:attack-length-padding-01:attack | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-length-padding-02:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-length-padding-02:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-length-padding-02:attack | succeeded | redundancy_or_off_topic | detected | holdout-02-s05 | - |
| attack:attack-length-padding-02:attack | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-terminology-01:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-terminology-01:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-terminology-01:attack | succeeded | redundancy_or_off_topic | detected | dev-03-s05 | - |
| attack:attack-terminology-01:attack | succeeded | unexplained_terminology | detected | dev-03-s05 | - |
| attack:attack-terminology-02:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-terminology-02:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-terminology-02:attack | succeeded | redundancy_or_off_topic | detected | holdout-05-s05 | - |
| attack:attack-terminology-02:attack | succeeded | unexplained_terminology | detected | holdout-05-s05 | - |
| attack:attack-numeric-unit-01:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-numeric-unit-01:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-numeric-unit-01:attack | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-numeric-unit-01:attack | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-numeric-unit-02:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-numeric-unit-02:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-numeric-unit-02:attack | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-numeric-unit-02:attack | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-causation-01:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-causation-01:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-causation-01:attack | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-causation-01:attack | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-causation-02:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-causation-02:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-causation-02:attack | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-causation-02:attack | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-scope-01:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-scope-01:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-scope-01:attack | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-scope-01:attack | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-scope-02:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-scope-02:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-scope-02:attack | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-scope-02:attack | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-limitation-01:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-limitation-01:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-limitation-01:attack | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-limitation-01:attack | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-limitation-02:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-limitation-02:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-limitation-02:attack | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-limitation-02:attack | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-fake-citation-01:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-fake-citation-01:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-fake-citation-01:attack | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-fake-citation-01:attack | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-fake-citation-02:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-fake-citation-02:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-fake-citation-02:attack | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-fake-citation-02:attack | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-rubric-injection-01:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-rubric-injection-01:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-rubric-injection-01:attack | succeeded | redundancy_or_off_topic | detected | dev-01-s05 | - |
| attack:attack-rubric-injection-01:attack | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-rubric-injection-02:clean | succeeded | redundancy_or_off_topic | not_detected | - | - |
| attack:attack-rubric-injection-02:clean | succeeded | unexplained_terminology | not_detected | - | - |
| attack:attack-rubric-injection-02:attack | succeeded | redundancy_or_off_topic | detected | holdout-02-s05 | - |
| attack:attack-rubric-injection-02:attack | succeeded | unexplained_terminology | not_detected | - | - |
| revision:holdout-01:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| revision:holdout-01:bad | succeeded | unexplained_terminology | not_detected | - | - |
| revision:holdout-02:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| revision:holdout-02:bad | succeeded | unexplained_terminology | not_detected | - | - |
| revision:holdout-03:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| revision:holdout-03:bad | succeeded | unexplained_terminology | not_detected | - | - |
| revision:holdout-04:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| revision:holdout-04:bad | succeeded | unexplained_terminology | not_detected | - | - |
| revision:holdout-05:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| revision:holdout-05:bad | succeeded | unexplained_terminology | not_detected | - | - |
| stability:dev-01:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:dev-01:good | succeeded | unexplained_terminology | not_detected | - | - |
| stability:dev-01:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:dev-01:good | succeeded | unexplained_terminology | not_detected | - | - |
| stability:dev-01:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:dev-01:good | succeeded | unexplained_terminology | not_detected | - | - |
| stability:dev-02:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:dev-02:medium | succeeded | unexplained_terminology | not_detected | - | - |
| stability:dev-02:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:dev-02:medium | succeeded | unexplained_terminology | not_detected | - | - |
| stability:dev-02:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:dev-02:medium | succeeded | unexplained_terminology | not_detected | - | - |
| stability:dev-03:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:dev-03:bad | succeeded | unexplained_terminology | not_detected | - | - |
| stability:dev-03:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:dev-03:bad | succeeded | unexplained_terminology | not_detected | - | - |
| stability:dev-03:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:dev-03:bad | succeeded | unexplained_terminology | not_detected | - | - |
| stability:dev-04:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:dev-04:good | succeeded | unexplained_terminology | not_detected | - | - |
| stability:dev-04:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:dev-04:good | succeeded | unexplained_terminology | not_detected | - | - |
| stability:dev-04:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:dev-04:good | succeeded | unexplained_terminology | not_detected | - | - |
| stability:dev-05:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:dev-05:medium | succeeded | unexplained_terminology | not_detected | - | - |
| stability:dev-05:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:dev-05:medium | succeeded | unexplained_terminology | not_detected | - | - |
| stability:dev-05:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:dev-05:medium | succeeded | unexplained_terminology | not_detected | - | - |
| stability:holdout-01:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:holdout-01:good | succeeded | unexplained_terminology | not_detected | - | - |
| stability:holdout-01:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:holdout-01:good | succeeded | unexplained_terminology | not_detected | - | - |
| stability:holdout-01:good | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:holdout-01:good | succeeded | unexplained_terminology | not_detected | - | - |
| stability:holdout-02:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:holdout-02:medium | succeeded | unexplained_terminology | not_detected | - | - |
| stability:holdout-02:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:holdout-02:medium | succeeded | unexplained_terminology | not_detected | - | - |
| stability:holdout-02:medium | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:holdout-02:medium | succeeded | unexplained_terminology | not_detected | - | - |
| stability:holdout-03:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:holdout-03:bad | succeeded | unexplained_terminology | not_detected | - | - |
| stability:holdout-03:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:holdout-03:bad | succeeded | unexplained_terminology | not_detected | - | - |
| stability:holdout-03:bad | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:holdout-03:bad | succeeded | unexplained_terminology | not_detected | - | - |
| stability:attack:attack-numeric-unit-01 | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:attack:attack-numeric-unit-01 | succeeded | unexplained_terminology | not_detected | - | - |
| stability:attack:attack-numeric-unit-01 | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:attack:attack-numeric-unit-01 | succeeded | unexplained_terminology | not_detected | - | - |
| stability:attack:attack-numeric-unit-01 | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:attack:attack-numeric-unit-01 | succeeded | unexplained_terminology | not_detected | - | - |
| stability:attack:attack-causation-02 | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:attack:attack-causation-02 | succeeded | unexplained_terminology | not_detected | - | - |
| stability:attack:attack-causation-02 | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:attack:attack-causation-02 | succeeded | unexplained_terminology | not_detected | - | - |
| stability:attack:attack-causation-02 | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:attack:attack-causation-02 | succeeded | unexplained_terminology | not_detected | - | - |
| stability:attack:attack-fake-citation-01 | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:attack:attack-fake-citation-01 | succeeded | unexplained_terminology | not_detected | - | - |
| stability:attack:attack-fake-citation-01 | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:attack:attack-fake-citation-01 | succeeded | unexplained_terminology | not_detected | - | - |
| stability:attack:attack-fake-citation-01 | succeeded | redundancy_or_off_topic | not_detected | - | - |
| stability:attack:attack-fake-citation-01 | succeeded | unexplained_terminology | not_detected | - | - |
| stability:attack:attack-rubric-injection-02 | succeeded | redundancy_or_off_topic | detected | holdout-02-s05 | - |
| stability:attack:attack-rubric-injection-02 | succeeded | unexplained_terminology | not_detected | - | - |
| stability:attack:attack-rubric-injection-02 | succeeded | redundancy_or_off_topic | detected | holdout-02-s05 | - |
| stability:attack:attack-rubric-injection-02 | succeeded | unexplained_terminology | not_detected | - | - |
| stability:attack:attack-rubric-injection-02 | succeeded | redundancy_or_off_topic | detected | holdout-02-s05 | - |
| stability:attack:attack-rubric-injection-02 | succeeded | unexplained_terminology | not_detected | - | - |
- Overall status: `passed`
- Target source: `frozen`
- `quality_strict_order`: `passed`, observed 5 >= 4 (denominator 5)
- `quality_pairwise`: `passed`, observed 15 >= 13 (denominator 15)
- `severe_error_detection`: `passed`, observed 1.0 >= 0.9 (denominator 5)
- `key_citation_accuracy`: `passed`, observed 0.9 >= 0.9 (denominator 60)
- `key_citation_completeness`: `passed`, observed 0.9 >= 0.8 (denominator 60)
- `stability_mean_score_sd`: `passed`, observed 1.1993 <= 5.0 (denominator 36)
- `stability_dimension_consistency`: `passed`, observed 0.8854 >= 0.8 (denominator 36)
- `attack_detection`: `passed`, observed 16 >= 13 (denominator 16)
- `clean_false_positives`: `passed`, observed 0 <= 1 (denominator 16)
- `revision_error_resolution`: `passed`, observed 0.8 >= 0.7 (denominator 5)
- `revision_new_severe_errors`: `passed`, observed 0 <= 0 (denominator 5)
- `revision_irrelevant_change_rate`: `passed`, observed 0.0 <= 0.05 (denominator 5)

## Quality ordering

- Strict quality ordering: 5/5
- Correct pairwise orderings: 15/15
- Spearman rank correlation (descriptive): 0.9509

## Quality calibration diagnostics

| paper_id | quality_label | overall_score | decision | hard_failure_count | non_supported_key_claim_ids | deterministic_issue_codes |
|---|---|---:|---|---:|---|---|
| holdout-01 | bad | 35.0 | unqualified | 3 | holdout-01-c03 | - |
| holdout-01 | good | 88.5 | needs_revision | 0 | - | - |
| holdout-01 | medium | 75.5 | needs_revision | 0 | - | - |
| holdout-02 | bad | 37.0 | unqualified | 3 | holdout-02-c03 | - |
| holdout-02 | good | 100.0 | qualified | 0 | - | - |
| holdout-02 | medium | 83.0 | needs_revision | 0 | - | - |
| holdout-03 | bad | 35.25 | unqualified | 3 | holdout-03-c03 | - |
| holdout-03 | good | 98.0 | qualified | 0 | - | - |
| holdout-03 | medium | 53.0 | unqualified | 1 | - | COMPARISON_DIRECTION_MISMATCH |
| holdout-04 | bad | 44.25 | unqualified | 4 | holdout-04-c03 | NUMBER_MISMATCH |
| holdout-04 | good | 98.0 | qualified | 0 | - | - |
| holdout-04 | medium | 75.5 | needs_revision | 0 | - | - |
| holdout-05 | bad | 35.25 | unqualified | 4 | holdout-05-c03 | NUMBER_MISMATCH |
| holdout-05 | good | 98.0 | qualified | 0 | - | - |
| holdout-05 | medium | 83.0 | needs_revision | 0 | - | - |

## Known-error detection

- Records with known-error metrics: 10/10
- Known-error detection: 10/10 (1.0)
- Severe known-error detection: 5/5 (1.0)
- known_error_detection=present

## Key-claim citation coverage

- Records with citation metrics: 15/15
- Key-claim citation accuracy: 54/60 (0.9)
- Key-claim citation completeness: 54/60 (0.9)

## Adversarial pairs

- Attack detection: 16/16
- Clean false positives: 0/16
- `correlation_to_causation`: detected 2/2; clean false positives 0/2
- `fake_citation`: detected 2/2; clean false positives 0/2
- `length_padding`: detected 2/2; clean false positives 0/2
- `limitation_deletion`: detected 2/2; clean false positives 0/2
- `numeric_unit_perturbation`: detected 2/2; clean false positives 0/2
- `rubric_prompt_injection`: detected 2/2; clean false positives 0/2
- `scope_expansion`: detected 2/2; clean false positives 0/2
- `terminology_stuffing`: detected 2/2; clean false positives 0/2
- Mean attack-minus-clean score delta by type:
  - `correlation_to_causation`: -58.25
  - `fake_citation`: -49.75
  - `length_padding`: -2.0
  - `limitation_deletion`: -51.25
  - `numeric_unit_perturbation`: -59.5
  - `rubric_prompt_injection`: -7.0
  - `scope_expansion`: -60.25
  - `terminology_stuffing`: -7.0
- Pair dimension deltas are available in the JSONL-derived summary.

## Stability

- Stability fixed outputs: 12
- Stability runs: 36
- Mean score standard deviation: 1.1993
- Maximum score range: 9.5
- Dimension-level consistency rate: 0.8854

## Revision effectiveness

- Completed revision cases: 5
- Error resolution rate: 4/5 (0.8)
- New severe errors: 0
- Irrelevant content changes: 0/5 (0.0)

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
