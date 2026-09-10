# 阶段8首轮发布检查

日期：2026-09-10。`STAGE_7=PASS_FIXED_SAMPLE`；`STAGE_8=IN_PROGRESS`；`PRODUCTION_READY=NO`。

阶段7收尾提交为 `290ac86f429b7e242058bbbfa519747b458b9907`，评测代码基线为 `b625ec75a9fffbde430ed4fdba3db938e818a096`。本轮未修改应用代码、实验冻结或历史结果。阶段7证据见 [收尾记录](stage7_closeout.md)。

## 实际执行结果

均在现有开发环境执行，不能据此认定全新环境安装通过。Python 使用现有 `.venv313/Scripts/python.exe`；前端命令在 `frontend/` 执行。

| 命令 | 结果 |
| --- | --- |
| `python -B -m pytest backend/tests eval/test_eval.py -q --tb=short -p no:cacheprovider` | 1137 passed、1 skipped，47.35秒 |
| `npm.cmd run typecheck` | 通过 |
| `npm.cmd run test -- --run` | 73 passed |
| `npm.cmd run build` | 通过 |
| `npx.cmd playwright test` | 6 passed，13.0秒 |
| `python -B eval/run_eval.py --mode smoke --output .test-tmp-stage8-20260910/smoke_results.jsonl` | 11条完成，参考回放，无供应商调用 |
| `python -B eval/build_report.py --input .test-tmp-stage8-20260910/smoke_results.jsonl --output .test-tmp-stage8-20260910/smoke_report.md` | 报告生成成功 |
| `python -m pip check` | 无损坏依赖 |
| `git diff --check` | 通过 |

跳过项为真实 MinerU opt-in。既有 Starlette/httpx 弃用警告保留；前端构建报告 lucide 的 use-client 指令忽略及大于500 kB的 bundle 警告，未改阈值掩盖。Playwright 使用显式预置 API 路由，验证交互行为，不证明真实后端或模型端到端能力，也不等同于演示录屏。

## 发布材料检查及剩余工作

1. README 仍称仅阶段1实现，缺少完整应用环境创建、前后端启动步骤。需更新并在全新环境按文档实际验证。
2. Git 跟踪文件中未发现项目 LICENSE 或第三方 NOTICE。项目授权方式需权利人确认，不能由执行助手替作者选择；依赖归属及 AI 说明需补齐。
3. `.env.example` 的 Hy3 密钥为空，默认 mock。仅扫描 tracked 文本中的高置信度密钥模式，未发现匹配；这不是完整密钥审计，未读取真实 `.env`。
4. tracked PDF 仅列出三个测试夹具；未逐份完成发布许可证/内容复核。绝对本机路径出现在 DEV_PLAN、STAGE_SESSION_PROMPTS 和阶段7本地证据登记中，应区分本地审计记录与对外发布材料，不删除证据以通过扫描。
5. 不超过两分钟的完整闭环演示尚未录制。演示必须使用真实调用或明确标注的预置结果；本轮未增加 Live 或真实 MinerU 授权。
6. 当前没有 Git remote，未推送，也未建立远程证据备份。

下一任务集中补齐发布文档和归属说明、完成全新环境验证及明确标注的演示。上述缺口关闭前保持生产未就绪；阶段7结果无需重跑。
