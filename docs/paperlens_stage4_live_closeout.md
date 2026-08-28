# PaperLens 阶段 4 Live 收口证据

## 结论与适用范围

本报告记录 2026-08-28 完成的阶段 6 入口 Live 硬门槛独立验收。最终场景 A 和场景 B 同时通过，`LIVE-BLOCKER-01` 可以关闭，阶段 6 入口可以从 `HOLD` 切换为 `READY`。

本结论对应代码基线 `000385977b95b900d23f866f01a7e8aac0ba23a4`（`fix: harden Hy3 live structured outputs`）。核验时当前 HEAD 为 `783824b07071c00ff680a1cb6d2fa4ef9eeb490f`，其 `backend/` 和 `frontend/` 代码树与该代码基线无差异。本文只持久化脱敏验收摘要，不修改公共 API、Schema、Token、重试上限、代码或数据库。

```ini
STAGE_5_GATE=PASS
STAGE_6_LIVE_GATE=PASS
LIVE-BLOCKER-01=CLOSED
STAGE_6_ENTRY=READY
PRODUCTION_READY=NO
```

风险分级：P0 无；P1 无阶段 6 入口阻塞；P2 为既有弃用及构建体积警告，不影响本次门槛。阶段 6 功能和生产就绪性不在本报告验收范围内。

## 输入与数据安全

- 获授权输入文件名：`attention-is-all-you-need_arxiv-1706.03762.pdf`
- 文件大小：`2215244` bytes
- SHA-256：`bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697`
- 扩展名、PDF 文件头、存在性和用户授权均在 Live 前确认。
- 没有记录或提交 API Key、Prompt、论文正文、供应商原始响应、风险原因或修复建议等自由文本。
- 每个场景使用独立数据目录；持久化写入后通过公共项目读取接口复核，再停止服务并删除隔离目录。因此默认 `data/paperlens.db` 为空是预期清理结果，不代表 Live 未执行，也不能替代下述运行证据。
- 未调用 `/v1/models`，未使用 Mock fallback，未把失败伪装为成功。

## 场景 A：真实 risk-only 闭环

场景 A 满足全部入口条件：

| 检查项 | 脱敏结果 |
| --- | --- |
| 上传与解析 | HTTP 201；`parsed`；`SourceBlock=122` |
| Generation | HTTP 200；`model_mode=live`；`claims=0`；`evidence=0`；`verified_evidence=0` |
| Generation 供应商调用 | 1 attempt；0 retries；`completion_tokens=627`；上限 16384；`finish_reason=stop`；无校验边界 |
| Deep Audit | HTTP 200；`stage=deep_audited`；`audit_status=deep_complete` |
| 语义判断 | `semantic_judgments=0` |
| 评分维度 | 恰好 8 个且 8 个唯一维度 |
| 风险类别 | 恰好 3 条、3 个唯一类别，sensitive / author / academic 各一次 |
| Deep Audit 供应商调用 | 1 attempt；0 retries；`completion_tokens=221`；上限 4096；`finish_reason=stop`；无校验边界 |
| 持久化复核 | 项目读取 HTTP 200；`deep_audited/live`；无错误码；审计仍为 `deep_complete` |
| 安全结果 | `Mock fallback=false`；`AUDIT_INCOMPLETE=false`；供应商输出修改=false |

公共 API 调用严格为一次健康检查、一次上传、一次 Generation、一次 Deep Audit 和一次最终项目读取；没有公共 API 重试。

## 场景 B：获授权真实文本型 PDF 闭环

场景 B 满足全部入口条件：

| 检查项 | 脱敏结果 |
| --- | --- |
| 上传与解析 | HTTP 201；`parsed`；`SourceBlock=122` |
| Generation | HTTP 200；`model_mode=live`；`claims=23`（23 个唯一 claim） |
| 证据 | `evidence=25`；`verified_evidence=20`；证据均链接到 claim |
| 语义输入与结果 | 20 个唯一 claim/evidence 输入对；运行时 exact-set 校验通过；接受的 `SemanticJudgment=20` |
| Generation 供应商调用 | 1 attempt；0 retries；`completion_tokens=4523`；上限 16384；`finish_reason=stop`；无校验边界 |
| Deep Audit | HTTP 200；`stage=deep_audited`；`audit_status=deep_complete` |
| 评分维度 | 恰好 8 个且 8 个唯一维度 |
| 风险类别 | 恰好 3 条、3 个唯一类别，sensitive / author / academic 各一次 |
| Deep Audit 供应商调用 | 1 attempt；0 retries；`completion_tokens=1632`；上限 4096；`finish_reason=stop`；无校验边界 |
| 持久化复核 | 项目读取 HTTP 200；`deep_audited/live`；无错误码；保留 23 claims、25 evidence、20 verified evidence 和完整审计 |
| 安全结果 | `Mock fallback=false`；`AUDIT_INCOMPLETE=false`；供应商输出修改=false |

语义配对结论不是按数量推测：Deep Audit 请求包含 20 个唯一实际输入对，运行时边界要求结果集合与该输入集合严格相等；本次校验通过后才接受 20 个判断。公共 API 调用同样没有重试。

## 不变量与失败边界核验

- Generation 保持 `gen-v4`、`generated-bundle-v1`、16384 Token。
- Deep Audit 保持 `audit-v2`、`deep-audit-result-v2`、4096 Token。
- 两条链路均保持最多 3 attempts / 2 retries；最终 A/B 各阶段实际均为 1 attempt / 0 retries。
- `extra="forbid"`、semantic pair exact-set、三类风险完整性、证据验证和评分维度完整性均未弱化。
- 没有补齐、过滤、删除、排序、去重或改写供应商输出。
- 没有 Mock fallback；`AUDIT_INCOMPLETE` 仍保留为真实失败路径，只是最终 A/B 没有触发。
- Live 安全诊断仅保存固定、有限、脱敏的计数、枚举、布尔值和资源上限。

## 独立回归

同一代码树上的独立结果：

- models + Hy3Service：`169 passed`
- Hy3Service + AuditService + API：`254 passed`
- 后端全量：`352 passed, 1 skipped`
- 前端 Vitest：`5 files / 16 tests passed`
- TypeScript typecheck：通过
- 前端生产构建：通过，`1823 modules transformed`
- Playwright：`3 passed`，三个预设流程全部通过
- `git diff --check`：通过

唯一后端跳过项为需显式启用的真实 MinerU 集成测试。非阻塞警告为既有 Starlette/httpx 弃用警告、Vite `use client` 提示、前端大 chunk 提示，以及 Playwright 环境颜色变量提示。

## 清理、未验证内容和交接

- Live 服务已停止，端口 8765 已关闭，隔离数据目录无残留。
- Live 持久化在清理前通过最终项目读取得到验证；没有把临时数据库或供应商原始内容提交到仓库。
- 未验证内容：阶段 6 的修订、版本、回退和 Markdown 导出实现；生产部署、安全运营及更广泛模型/供应商泛化能力。
- 下一步唯一动作：按 `DEV_PLAN.md` 新开阶段 6 独立原子任务，从失败测试开始实现；不得把本报告解释为阶段 6 已完成或 `PRODUCTION_READY=YES`。
