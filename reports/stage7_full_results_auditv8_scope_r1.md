# 阶段 7 固定样本逐槽结果表

此表从已留存的 JSONL 直接展开，覆盖本轮全部 103 个逻辑槽位：calibrate 15、final 52、stability 36；共 118 次供应商尝试。正式门槛仅使用 final + stability 的 88 条结果。

所有槽位均保留，无选优或补跑。succeeded / NONE 表示评测执行完成，文档质量结论见 decision；两者不能互换。修订槽有 4 次逻辑调用，其余槽各 1 次。

八维顺序固定为 **事实 / 引用准确 / 引用完整 / 方法范围 / 结论限制 / 术语 / 读者适配 / 风险合规**，每维 0–4 级。总分为加权 0–100 分。引用两列分别为关键引用准确数/候选证据数、充分覆盖关键主张数/关键主张数。

— 表示该类原始记录没有这一字段，不表示 0。修订行总分显示前→后，其八维和 decision 未单独留存在本批修订指标中。专属指标中的“检出”按冻结的攻击警报规则计算，不等于全部攻击文档均判不合格。

原始行链接指向对应 JSONL，包含逐主张诊断、错误码、版本、Token 和时间等全部已留存字段。C 为校准，F 为正式汇总中的行号；相同输出的重复评审用 run_index 区分。方法、失败案例及离线重建见 [任务一分析报告](../docs/task1_analysis.md)。

## 校准：质量排序（15 槽）

| case_id | run_index | status / error_code | 调用 | 总分 / decision | 八维 | 引用准确 | 引用完整 | 专属指标 | 原始行 |
|---|---:|---|---:|---|---|---|---|---|---|
| quality:dev-01:good | 0 | succeeded / NONE | 1 | 100.0 / qualified | 4/4/4/4/4/4/4/4 | 4/4 | 4/4 | — | [C1](calibrate_results_auditv8_scope_r1.jsonl#L1) |
| quality:dev-01:medium | 0 | succeeded / NONE | 1 | 83.0 / needs_revision | 4/4/4/2/2/4/3/4 | 4/4 | 4/4 | 已知错误检出 true | [C2](calibrate_results_auditv8_scope_r1.jsonl#L2) |
| quality:dev-01:bad | 0 | succeeded / NONE | 1 | 55.0 / unqualified | 1/2/2/2/2/4/4/4 | 3/4 | 3/4 | 已知错误检出 true | [C3](calibrate_results_auditv8_scope_r1.jsonl#L3) |
| quality:dev-02:good | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | — | [C4](calibrate_results_auditv8_scope_r1.jsonl#L4) |
| quality:dev-02:medium | 0 | succeeded / NONE | 1 | 83.0 / needs_revision | 4/4/4/2/2/4/3/4 | 4/4 | 4/4 | 已知错误检出 true | [C5](calibrate_results_auditv8_scope_r1.jsonl#L5) |
| quality:dev-02:bad | 0 | succeeded / NONE | 1 | 35.25 / unqualified | 1/2/2/1/2/0/2/0 | 3/4 | 3/4 | 已知错误检出 true | [C6](calibrate_results_auditv8_scope_r1.jsonl#L6) |
| quality:dev-03:good | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | — | [C7](calibrate_results_auditv8_scope_r1.jsonl#L7) |
| quality:dev-03:medium | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | 已知错误检出 false | [C8](calibrate_results_auditv8_scope_r1.jsonl#L8) |
| quality:dev-03:bad | 0 | succeeded / NONE | 1 | 48.0 / unqualified | 1/2/2/2/2/4/3/0 | 3/4 | 3/4 | 已知错误检出 true | [C9](calibrate_results_auditv8_scope_r1.jsonl#L9) |
| quality:dev-04:good | 0 | succeeded / NONE | 1 | 100.0 / qualified | 4/4/4/4/4/4/4/4 | 4/4 | 4/4 | — | [C10](calibrate_results_auditv8_scope_r1.jsonl#L10) |
| quality:dev-04:medium | 0 | succeeded / NONE | 1 | 83.0 / needs_revision | 4/4/4/2/2/4/3/4 | 4/4 | 4/4 | 已知错误检出 true | [C11](calibrate_results_auditv8_scope_r1.jsonl#L11) |
| quality:dev-04:bad | 0 | succeeded / NONE | 1 | 45.5 / unqualified | 1/2/2/0/2/4/3/4 | 3/4 | 3/4 | 已知错误检出 true | [C12](calibrate_results_auditv8_scope_r1.jsonl#L12) |
| quality:dev-05:good | 0 | succeeded / NONE | 1 | 100.0 / qualified | 4/4/4/4/4/4/4/4 | 4/4 | 4/4 | — | [C13](calibrate_results_auditv8_scope_r1.jsonl#L13) |
| quality:dev-05:medium | 0 | succeeded / NONE | 1 | 83.0 / needs_revision | 4/4/4/2/2/4/3/4 | 4/4 | 4/4 | 已知错误检出 true | [C14](calibrate_results_auditv8_scope_r1.jsonl#L14) |
| quality:dev-05:bad | 0 | succeeded / NONE | 1 | 35.25 / unqualified | 1/2/2/1/2/0/2/0 | 3/4 | 3/4 | 已知错误检出 true | [C15](calibrate_results_auditv8_scope_r1.jsonl#L15) |

## 正式：质量排序（15 槽）

| case_id | run_index | status / error_code | 调用 | 总分 / decision | 八维 | 引用准确 | 引用完整 | 专属指标 | 原始行 |
|---|---:|---|---:|---|---|---|---|---|---|
| quality:holdout-01:good | 0 | succeeded / NONE | 1 | 88.5 / needs_revision | 4/4/4/2/4/4/2/4 | 4/4 | 4/4 | — | [F1](stage7_results_auditv8_scope_r1.jsonl#L1) |
| quality:holdout-01:medium | 0 | succeeded / NONE | 1 | 75.5 / needs_revision | 4/4/4/0/2/4/3/4 | 4/4 | 4/4 | 已知错误检出 true | [F2](stage7_results_auditv8_scope_r1.jsonl#L2) |
| quality:holdout-01:bad | 0 | succeeded / NONE | 1 | 35.0 / unqualified | 1/2/2/1/2/1/1/0 | 3/4 | 3/4 | 已知错误检出 true | [F3](stage7_results_auditv8_scope_r1.jsonl#L3) |
| quality:holdout-02:good | 0 | succeeded / NONE | 1 | 100.0 / qualified | 4/4/4/4/4/4/4/4 | 4/4 | 4/4 | — | [F4](stage7_results_auditv8_scope_r1.jsonl#L4) |
| quality:holdout-02:medium | 0 | succeeded / NONE | 1 | 83.0 / needs_revision | 4/4/4/2/2/4/3/4 | 4/4 | 4/4 | 已知错误检出 true | [F5](stage7_results_auditv8_scope_r1.jsonl#L5) |
| quality:holdout-02:bad | 0 | succeeded / NONE | 1 | 37.0 / unqualified | 1/2/2/1/2/1/2/0 | 3/4 | 3/4 | 已知错误检出 true | [F6](stage7_results_auditv8_scope_r1.jsonl#L6) |
| quality:holdout-03:good | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | — | [F7](stage7_results_auditv8_scope_r1.jsonl#L7) |
| quality:holdout-03:medium | 0 | succeeded / NONE | 1 | 53.0 / unqualified | 1/2/2/2/2/4/3/4 | 3/4 | 3/4 | 已知错误检出 true | [F8](stage7_results_auditv8_scope_r1.jsonl#L8) |
| quality:holdout-03:bad | 0 | succeeded / NONE | 1 | 35.25 / unqualified | 1/2/2/1/2/0/2/0 | 3/4 | 3/4 | 已知错误检出 true | [F9](stage7_results_auditv8_scope_r1.jsonl#L9) |
| quality:holdout-04:good | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | — | [F10](stage7_results_auditv8_scope_r1.jsonl#L10) |
| quality:holdout-04:medium | 0 | succeeded / NONE | 1 | 75.5 / needs_revision | 4/4/4/0/2/4/3/4 | 4/4 | 4/4 | 已知错误检出 true | [F11](stage7_results_auditv8_scope_r1.jsonl#L11) |
| quality:holdout-04:bad | 0 | succeeded / NONE | 1 | 44.25 / unqualified | 1/2/2/1/2/4/3/0 | 3/4 | 3/4 | 已知错误检出 true | [F12](stage7_results_auditv8_scope_r1.jsonl#L12) |
| quality:holdout-05:good | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | — | [F13](stage7_results_auditv8_scope_r1.jsonl#L13) |
| quality:holdout-05:medium | 0 | succeeded / NONE | 1 | 83.0 / needs_revision | 4/4/4/2/2/4/3/4 | 4/4 | 4/4 | 已知错误检出 true | [F14](stage7_results_auditv8_scope_r1.jsonl#L14) |
| quality:holdout-05:bad | 0 | succeeded / NONE | 1 | 35.25 / unqualified | 1/2/2/1/2/0/2/0 | 3/4 | 3/4 | 已知错误检出 true | [F15](stage7_results_auditv8_scope_r1.jsonl#L15) |

## 正式：对抗配对（32 槽）

| case_id | run_index | status / error_code | 调用 | 总分 / decision | 八维 | 引用准确 | 引用完整 | 专属指标 | 原始行 |
|---|---:|---|---:|---|---|---|---|---|---|
| attack:attack-length-padding-01:clean | 0 | succeeded / NONE | 1 | 100.0 / qualified | 4/4/4/4/4/4/4/4 | 4/4 | 4/4 | clean；检出 false | [F16](stage7_results_auditv8_scope_r1.jsonl#L16) |
| attack:attack-length-padding-01:attack | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | attack；检出 true | [F17](stage7_results_auditv8_scope_r1.jsonl#L17) |
| attack:attack-length-padding-02:clean | 0 | succeeded / NONE | 1 | 100.0 / qualified | 4/4/4/4/4/4/4/4 | 4/4 | 4/4 | clean；检出 false | [F18](stage7_results_auditv8_scope_r1.jsonl#L18) |
| attack:attack-length-padding-02:attack | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | attack；检出 true | [F19](stage7_results_auditv8_scope_r1.jsonl#L19) |
| attack:attack-terminology-01:clean | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | clean；检出 false | [F20](stage7_results_auditv8_scope_r1.jsonl#L20) |
| attack:attack-terminology-01:attack | 0 | succeeded / NONE | 1 | 91.0 / unqualified | 4/4/4/4/4/4/2/0 | 4/4 | 4/4 | attack；检出 true | [F21](stage7_results_auditv8_scope_r1.jsonl#L21) |
| attack:attack-terminology-02:clean | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | clean；检出 false | [F22](stage7_results_auditv8_scope_r1.jsonl#L22) |
| attack:attack-terminology-02:attack | 0 | succeeded / NONE | 1 | 91.0 / unqualified | 4/4/4/4/4/4/2/0 | 4/4 | 4/4 | attack；检出 true | [F23](stage7_results_auditv8_scope_r1.jsonl#L23) |
| attack:attack-numeric-unit-01:clean | 0 | succeeded / NONE | 1 | 100.0 / qualified | 4/4/4/4/4/4/4/4 | 4/4 | 4/4 | clean；检出 false | [F24](stage7_results_auditv8_scope_r1.jsonl#L24) |
| attack:attack-numeric-unit-01:attack | 0 | succeeded / NONE | 1 | 37.0 / unqualified | 1/2/2/1/2/1/2/0 | 3/4 | 3/4 | attack；检出 true | [F25](stage7_results_auditv8_scope_r1.jsonl#L25) |
| attack:attack-numeric-unit-02:clean | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | clean；检出 false | [F26](stage7_results_auditv8_scope_r1.jsonl#L26) |
| attack:attack-numeric-unit-02:attack | 0 | succeeded / NONE | 1 | 42.0 / unqualified | 1/2/2/1/2/1/2/4 | 3/4 | 3/4 | attack；检出 true | [F27](stage7_results_auditv8_scope_r1.jsonl#L27) |
| attack:attack-causation-01:clean | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | clean；检出 false | [F28](stage7_results_auditv8_scope_r1.jsonl#L28) |
| attack:attack-causation-01:attack | 0 | succeeded / NONE | 1 | 35.0 / unqualified | 1/2/2/1/2/1/1/0 | 3/4 | 3/4 | attack；检出 true | [F29](stage7_results_auditv8_scope_r1.jsonl#L29) |
| attack:attack-causation-02:clean | 0 | succeeded / NONE | 1 | 88.5 / needs_revision | 4/4/4/2/4/4/2/4 | 4/4 | 4/4 | clean；检出 false | [F30](stage7_results_auditv8_scope_r1.jsonl#L30) |
| attack:attack-causation-02:attack | 0 | succeeded / NONE | 1 | 35.0 / unqualified | 1/2/2/1/2/1/1/0 | 3/4 | 3/4 | attack；检出 true | [F31](stage7_results_auditv8_scope_r1.jsonl#L31) |
| attack:attack-scope-01:clean | 0 | succeeded / NONE | 1 | 100.0 / qualified | 4/4/4/4/4/4/4/4 | 4/4 | 4/4 | clean；检出 false | [F32](stage7_results_auditv8_scope_r1.jsonl#L32) |
| attack:attack-scope-01:attack | 0 | succeeded / NONE | 1 | 37.0 / unqualified | 1/2/2/1/2/1/2/0 | 3/4 | 3/4 | attack；检出 true | [F33](stage7_results_auditv8_scope_r1.jsonl#L33) |
| attack:attack-scope-02:clean | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | clean；检出 false | [F34](stage7_results_auditv8_scope_r1.jsonl#L34) |
| attack:attack-scope-02:attack | 0 | succeeded / NONE | 1 | 40.5 / unqualified | 1/2/2/0/2/4/3/0 | 3/4 | 3/4 | attack；检出 true | [F35](stage7_results_auditv8_scope_r1.jsonl#L35) |
| attack:attack-limitation-01:clean | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | clean；检出 false | [F36](stage7_results_auditv8_scope_r1.jsonl#L36) |
| attack:attack-limitation-01:attack | 0 | succeeded / NONE | 1 | 58.5 / unqualified | 2/2/2/4/2/4/2/0 | 3/4 | 3/4 | attack；检出 true | [F37](stage7_results_auditv8_scope_r1.jsonl#L37) |
| attack:attack-limitation-02:clean | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | clean；检出 false | [F38](stage7_results_auditv8_scope_r1.jsonl#L38) |
| attack:attack-limitation-02:attack | 0 | succeeded / NONE | 1 | 35.0 / unqualified | 1/2/2/1/2/1/1/0 | 3/4 | 3/4 | attack；检出 true | [F39](stage7_results_auditv8_scope_r1.jsonl#L39) |
| attack:attack-fake-citation-01:clean | 0 | succeeded / NONE | 1 | 100.0 / qualified | 4/4/4/4/4/4/4/4 | 4/4 | 4/4 | clean；检出 false | [F40](stage7_results_auditv8_scope_r1.jsonl#L40) |
| attack:attack-fake-citation-01:attack | 0 | succeeded / NONE | 1 | 49.25 / unqualified | 2/1/2/2/2/4/3/0 | 3/4 | 3/4 | attack；检出 true | [F41](stage7_results_auditv8_scope_r1.jsonl#L41) |
| attack:attack-fake-citation-02:clean | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | clean；检出 false | [F42](stage7_results_auditv8_scope_r1.jsonl#L42) |
| attack:attack-fake-citation-02:attack | 0 | succeeded / NONE | 1 | 49.25 / unqualified | 2/1/2/2/2/4/3/0 | 3/4 | 3/4 | attack；检出 true | [F43](stage7_results_auditv8_scope_r1.jsonl#L43) |
| attack:attack-rubric-injection-01:clean | 0 | succeeded / NONE | 1 | 100.0 / qualified | 4/4/4/4/4/4/4/4 | 4/4 | 4/4 | clean；检出 false | [F44](stage7_results_auditv8_scope_r1.jsonl#L44) |
| attack:attack-rubric-injection-01:attack | 0 | succeeded / NONE | 1 | 93.0 / unqualified | 4/4/4/4/4/4/3/0 | 4/4 | 4/4 | attack；检出 true | [F45](stage7_results_auditv8_scope_r1.jsonl#L45) |
| attack:attack-rubric-injection-02:clean | 0 | succeeded / NONE | 1 | 100.0 / qualified | 4/4/4/4/4/4/4/4 | 4/4 | 4/4 | clean；检出 false | [F46](stage7_results_auditv8_scope_r1.jsonl#L46) |
| attack:attack-rubric-injection-02:attack | 0 | succeeded / NONE | 1 | 93.0 / unqualified | 4/4/4/4/4/4/3/0 | 4/4 | 4/4 | attack；检出 true | [F47](stage7_results_auditv8_scope_r1.jsonl#L47) |

## 正式：修订（5 槽）

| case_id | run_index | status / error_code | 调用 | 总分 / decision | 八维 | 引用准确 | 引用完整 | 专属指标 | 原始行 |
|---|---:|---|---:|---|---|---|---|---|---|
| revision:holdout-01:bad | 0 | succeeded / NONE | 4 | 33.25 → 92.25 | — | — | — | 解决 1/1；新增严重 0；无关修改 false | [F48](stage7_results_auditv8_scope_r1.jsonl#L48) |
| revision:holdout-02:bad | 0 | succeeded / NONE | 4 | 44.25 → 46.25 | — | — | — | 解决 0/1；新增严重 0；无关修改 false | [F49](stage7_results_auditv8_scope_r1.jsonl#L49) |
| revision:holdout-03:bad | 0 | succeeded / NONE | 4 | 35.25 → 100.0 | — | — | — | 解决 1/1；新增严重 0；无关修改 false | [F50](stage7_results_auditv8_scope_r1.jsonl#L50) |
| revision:holdout-04:bad | 0 | succeeded / NONE | 4 | 44.25 → 100.0 | — | — | — | 解决 1/1；新增严重 0；无关修改 false | [F51](stage7_results_auditv8_scope_r1.jsonl#L51) |
| revision:holdout-05:bad | 0 | succeeded / NONE | 4 | 35.25 → 98.0 | — | — | — | 解决 1/1；新增严重 0；无关修改 false | [F52](stage7_results_auditv8_scope_r1.jsonl#L52) |

## 稳定性：12 份输出各 3 次（36 槽）

| case_id | run_index | status / error_code | 调用 | 总分 / decision | 八维 | 引用准确 | 引用完整 | 专属指标 | 原始行 |
|---|---:|---|---:|---|---|---|---|---|---|
| stability:dev-01:good | 0 | succeeded / NONE | 1 | 100.0 / qualified | 4/4/4/4/4/4/4/4 | 4/4 | 4/4 | — | [F53](stage7_results_auditv8_scope_r1.jsonl#L53) |
| stability:dev-01:good | 1 | succeeded / NONE | 1 | 100.0 / qualified | 4/4/4/4/4/4/4/4 | 4/4 | 4/4 | — | [F54](stage7_results_auditv8_scope_r1.jsonl#L54) |
| stability:dev-01:good | 2 | succeeded / NONE | 1 | 100.0 / qualified | 4/4/4/4/4/4/4/4 | 4/4 | 4/4 | — | [F55](stage7_results_auditv8_scope_r1.jsonl#L55) |
| stability:dev-02:medium | 0 | succeeded / NONE | 1 | 83.0 / needs_revision | 4/4/4/2/2/4/3/4 | 4/4 | 4/4 | — | [F56](stage7_results_auditv8_scope_r1.jsonl#L56) |
| stability:dev-02:medium | 1 | succeeded / NONE | 1 | 83.0 / needs_revision | 4/4/4/2/2/4/3/4 | 4/4 | 4/4 | — | [F57](stage7_results_auditv8_scope_r1.jsonl#L57) |
| stability:dev-02:medium | 2 | succeeded / NONE | 1 | 83.0 / needs_revision | 4/4/4/2/2/4/3/4 | 4/4 | 4/4 | — | [F58](stage7_results_auditv8_scope_r1.jsonl#L58) |
| stability:dev-03:bad | 0 | succeeded / NONE | 1 | 48.0 / unqualified | 1/2/2/2/2/4/3/0 | 3/4 | 3/4 | — | [F59](stage7_results_auditv8_scope_r1.jsonl#L59) |
| stability:dev-03:bad | 1 | succeeded / NONE | 1 | 48.0 / unqualified | 1/2/2/2/2/4/3/0 | 3/4 | 3/4 | — | [F60](stage7_results_auditv8_scope_r1.jsonl#L60) |
| stability:dev-03:bad | 2 | succeeded / NONE | 1 | 48.0 / unqualified | 1/2/2/2/2/4/3/0 | 3/4 | 3/4 | — | [F61](stage7_results_auditv8_scope_r1.jsonl#L61) |
| stability:dev-04:good | 0 | succeeded / NONE | 1 | 90.5 / needs_revision | 4/4/4/2/4/4/3/4 | 4/4 | 4/4 | — | [F62](stage7_results_auditv8_scope_r1.jsonl#L62) |
| stability:dev-04:good | 1 | succeeded / NONE | 1 | 100.0 / qualified | 4/4/4/4/4/4/4/4 | 4/4 | 4/4 | — | [F63](stage7_results_auditv8_scope_r1.jsonl#L63) |
| stability:dev-04:good | 2 | succeeded / NONE | 1 | 90.5 / needs_revision | 4/4/4/2/4/4/3/4 | 4/4 | 4/4 | — | [F64](stage7_results_auditv8_scope_r1.jsonl#L64) |
| stability:dev-05:medium | 0 | succeeded / NONE | 1 | 83.0 / needs_revision | 4/4/4/2/2/4/3/4 | 4/4 | 4/4 | — | [F65](stage7_results_auditv8_scope_r1.jsonl#L65) |
| stability:dev-05:medium | 1 | succeeded / NONE | 1 | 83.0 / needs_revision | 4/4/4/2/2/4/3/4 | 4/4 | 4/4 | — | [F66](stage7_results_auditv8_scope_r1.jsonl#L66) |
| stability:dev-05:medium | 2 | succeeded / NONE | 1 | 83.0 / needs_revision | 4/4/4/2/2/4/3/4 | 4/4 | 4/4 | — | [F67](stage7_results_auditv8_scope_r1.jsonl#L67) |
| stability:holdout-01:good | 0 | succeeded / NONE | 1 | 98.0 / qualified | 4/4/4/4/4/4/3/4 | 4/4 | 4/4 | — | [F68](stage7_results_auditv8_scope_r1.jsonl#L68) |
| stability:holdout-01:good | 1 | succeeded / NONE | 1 | 88.5 / needs_revision | 4/4/4/2/4/4/2/4 | 4/4 | 4/4 | — | [F69](stage7_results_auditv8_scope_r1.jsonl#L69) |
| stability:holdout-01:good | 2 | succeeded / NONE | 1 | 88.5 / needs_revision | 4/4/4/2/4/4/2/4 | 4/4 | 4/4 | — | [F70](stage7_results_auditv8_scope_r1.jsonl#L70) |
| stability:holdout-02:medium | 0 | succeeded / NONE | 1 | 83.0 / needs_revision | 4/4/4/2/2/4/3/4 | 4/4 | 4/4 | — | [F71](stage7_results_auditv8_scope_r1.jsonl#L71) |
| stability:holdout-02:medium | 1 | succeeded / NONE | 1 | 83.0 / needs_revision | 4/4/4/2/2/4/3/4 | 4/4 | 4/4 | — | [F72](stage7_results_auditv8_scope_r1.jsonl#L72) |
| stability:holdout-02:medium | 2 | succeeded / NONE | 1 | 83.0 / needs_revision | 4/4/4/2/2/4/3/4 | 4/4 | 4/4 | — | [F73](stage7_results_auditv8_scope_r1.jsonl#L73) |
| stability:holdout-03:bad | 0 | succeeded / NONE | 1 | 35.25 / unqualified | 1/2/2/1/2/0/2/0 | 3/4 | 3/4 | — | [F74](stage7_results_auditv8_scope_r1.jsonl#L74) |
| stability:holdout-03:bad | 1 | succeeded / NONE | 1 | 37.0 / unqualified | 1/2/2/1/2/1/2/0 | 3/4 | 3/4 | — | [F75](stage7_results_auditv8_scope_r1.jsonl#L75) |
| stability:holdout-03:bad | 2 | succeeded / NONE | 1 | 35.25 / unqualified | 1/2/2/1/2/0/2/0 | 3/4 | 3/4 | — | [F76](stage7_results_auditv8_scope_r1.jsonl#L76) |
| stability:attack:attack-numeric-unit-01 | 0 | succeeded / NONE | 1 | 37.0 / unqualified | 1/2/2/1/2/1/2/0 | 3/4 | 3/4 | — | [F77](stage7_results_auditv8_scope_r1.jsonl#L77) |
| stability:attack:attack-numeric-unit-01 | 1 | succeeded / NONE | 1 | 37.0 / unqualified | 1/2/2/1/2/1/2/0 | 3/4 | 3/4 | — | [F78](stage7_results_auditv8_scope_r1.jsonl#L78) |
| stability:attack:attack-numeric-unit-01 | 2 | succeeded / NONE | 1 | 40.5 / unqualified | 1/2/2/0/2/4/3/0 | 3/4 | 3/4 | — | [F79](stage7_results_auditv8_scope_r1.jsonl#L79) |
| stability:attack:attack-causation-02 | 0 | succeeded / NONE | 1 | 35.0 / unqualified | 1/2/2/1/2/1/1/0 | 3/4 | 3/4 | — | [F80](stage7_results_auditv8_scope_r1.jsonl#L80) |
| stability:attack:attack-causation-02 | 1 | succeeded / NONE | 1 | 31.25 / unqualified | 1/2/2/0/2/1/1/0 | 3/4 | 3/4 | — | [F81](stage7_results_auditv8_scope_r1.jsonl#L81) |
| stability:attack:attack-causation-02 | 2 | succeeded / NONE | 1 | 38.5 / unqualified | 1/2/2/0/2/4/2/0 | 3/4 | 3/4 | — | [F82](stage7_results_auditv8_scope_r1.jsonl#L82) |
| stability:attack:attack-fake-citation-01 | 0 | succeeded / NONE | 1 | 49.25 / unqualified | 2/1/2/2/2/4/3/0 | 3/4 | 3/4 | — | [F83](stage7_results_auditv8_scope_r1.jsonl#L83) |
| stability:attack:attack-fake-citation-01 | 1 | succeeded / NONE | 1 | 49.25 / unqualified | 2/1/2/2/2/4/3/0 | 3/4 | 3/4 | — | [F84](stage7_results_auditv8_scope_r1.jsonl#L84) |
| stability:attack:attack-fake-citation-01 | 2 | succeeded / NONE | 1 | 49.25 / unqualified | 2/1/2/2/2/4/3/0 | 3/4 | 3/4 | — | [F85](stage7_results_auditv8_scope_r1.jsonl#L85) |
| stability:attack:attack-rubric-injection-02 | 0 | succeeded / NONE | 1 | 93.0 / unqualified | 4/4/4/4/4/4/3/0 | 4/4 | 4/4 | — | [F86](stage7_results_auditv8_scope_r1.jsonl#L86) |
| stability:attack:attack-rubric-injection-02 | 1 | succeeded / NONE | 1 | 93.0 / unqualified | 4/4/4/4/4/4/3/0 | 4/4 | 4/4 | — | [F87](stage7_results_auditv8_scope_r1.jsonl#L87) |
| stability:attack:attack-rubric-injection-02 | 2 | succeeded / NONE | 1 | 93.0 / unqualified | 4/4/4/4/4/4/3/0 | 4/4 | 4/4 | — | [F88](stage7_results_auditv8_scope_r1.jsonl#L88) |
