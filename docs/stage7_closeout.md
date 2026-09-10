# 阶段7最终收尾与阶段8交接

日期：2026-09-10。结论：`STAGE_7=PASS_FIXED_SAMPLE`。

## 基线

- 已验收实现提交：`b625ec75a9fffbde430ed4fdba3db938e818a096`。
- 评测内容指纹：`workspace-content-35623d9b0da0b4ad7c1573b12a9c634980058b7248ad3b44187c14d4091dcbd1`。
- 版本：`audit-v8 / revision-v3 / sentence-claims-v3 / deep-audit-result-v3 / paperlens-stage7-method-v3`。
- 本文与 DEV_PLAN 的文档收尾提交是交接基线，不替代上述评测代码基线。可用 `git log -- docs/stage7_closeout.md` 定位文档提交；不为记录自身提交号再次修改文档。
- 无 Git remote，未推送；本地提交完成不等于远程备份。

## 结果及验收

| 项目 | 结果 |
| --- | --- |
| calibrate | 15/15 成功，15 次供应商尝试 |
| final | 52/52 成功，67 次供应商尝试 |
| stability | 36/36 成功，36 次供应商尝试 |
| 严格排序 / 成对排序 | 5/5、15/15 |
| 严重错误检出 | 5/5 |
| 引用准确率 / 完整率 | 54/60、54/60 |
| 攻击检出 / clean 误报 | 16/16、0/16 |
| 修订问题解决 | 4/5；新增严重错误与无关修改均为 0 |
| 平均总分总体标准差 | 1.1993288533809268，门槛不超过 5 |
| 8维三次完全一致 | 85/96 = 88.54%，门槛不低于 80% |

各批无重试、失败、中断或未知计量，未解决 pending 为 0。正式汇总只使用本轮 final 与 stability 的 88 条原始行，不混入开发集或历史实验。全部冻结门槛通过，汇总报告重建逐字节一致；稳定性使用标准库独立复算。冻结未改写。

实施独立回归为 1137 passed、1 skipped（真实 MinerU opt-in），保留既有弃用警告。通过后源码未变化；不以离线测试替代模型实验。

## 本地证据归档

证据根目录为 `D:\Hy3-stage7-auditv8-scope-r1`，是非 Git 的独立副本。48 份源码、材料及文档的快照摘要记录于 `snapshot_hashes.json`。原始结果和报告保留在该副本，不移入主仓库，不覆盖旧报告或数据库。

下表除清单外均位于副本 `reports/`，SHA256 用于复核，不包含密钥或正文。

| 文件 | SHA256 |
| --- | --- |
| `reviewed_scope_manifest.json`（副本根目录） | `e6c86a6218cd6e4c8adea8daaf7dfdc8de59b890b8c0aa5252414f38031c1d78` |
| `stage7_frozen_config.json` | `64198896fa0ec6398a4b66de5108d0bfc144ca56d0ff1a34eac64de228a8a955` |
| `calibrate_results_auditv8_scope_r1.jsonl` | `c9abcdf9027ce15fde7816929f12bb3840649507fb7ec00a37c931bad9839048` |
| `final_results_auditv8_scope_r1.jsonl` | `fd1f4ff03f6d26f9f5ae2a1a5934a773967a0ee85f13d9d27eeec267c3f03bd0` |
| `stability_results_auditv8_scope_r1.jsonl` | `07c4f26fabfd3efb56723d1315ef8dfbc8dbec573f879eeca1520d31244ce2d8` |
| `stage7_results_auditv8_scope_r1.jsonl` | `0e0ed06263cba254fb81d6b13aa4c4158821833caa2236e674b00ee24f37fe30` |
| `stage7_report_auditv8_scope_r1.md` | `9c1c0c16dc7a144ebb6299c5e1cef73490295930d5d401f2cf7d2db97b4dc8d2` |
| `stage7_acceptance_auditv8_scope_r1.json` | `fd8056fc93aaa9e08a64df72eff43a5243c210ece2a11afaff1c5646bd844232` |

验收 JSON 还列有各独立报告及 lock/pending 文件摘要。`scope_review_record.json` 记录人工审核依据；清单摘要已由用户在会话明确确认，早期草稿中等待确认标记保留为历史状态。清单只对 `revision:holdout-04:bad` 关联两块范围信息，不自动扩展到其他槽。

主库、43 份主 reports、历史冻结和 final 及副本源码在实验前后核验一致。非 Git 产物仍依赖此本地目录保存，尚无远程备份；不得删除 pending/lock 以伪造干净状态。本收尾不处理旧敏感证据目录，其授权和保留期限仍独立适用。

## 限制与交接

- 样本已查看，结论限于获批固定样本，不能描述为新的盲测或独立泛化证据。
- holdout-02 修订仍未解决；4/5 达到预先冻结门槛，不补跑取优。
- 人工关联的语义正确性依赖审核记录，摘要只绑定已批准内容；材料构造页码不等于论文真实页码。
- final + stability 按代码单价估算费用为 ¥0.530359，另 calibrate 为 ¥0.079909；均非已确认供应商账单。
- 阶段8负责全新环境重建、前后端发布回归、Playwright、发布内容和许可证检查，以及不超过两分钟的完整闭环演示。不能因阶段7通过而宣称生产就绪。

下一步按 DEV_PLAN 阶段8执行发布检查；不再修改阶段7冻结或重新付费取优。
