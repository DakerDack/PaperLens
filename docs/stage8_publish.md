# 发布与独立验收说明

日期：2026-09-10。目标仓库：<https://github.com/DakerDack/PaperLens>。

`STAGE_7=PASS_FIXED_SAMPLE`；`STAGE_8=PASS`；`REVIEW_STATE=APPROVED`；`PRODUCTION_READY=NO`。

本次发布经权利人授权，项目 MIT 选择已由权利人在独立验收任务中明确确认。通过范围为 Windows 本地单用户源码交付，不是公开在线服务的生产就绪认证。

## 独立验证

| 项目 | 结果 |
| --- | --- |
| 现有 Python 环境完整回归 | 1141 passed、1 skipped |
| 全新 Python 环境按锁安装及完整回归 | 1141 passed、1 skipped；pip check 通过 |
| 全新 Node 安装、独立 npm 和 Chromium 缓存 | 安装成功；前端 74 passed、Playwright 6 passed |
| 类型检查、生产构建、npm audit | 通过；audit 0 vulnerabilities |
| 新输出路径的参考 smoke 与报告重建 | 11 条新结果，无供应商调用 |
| 真实本地 API、pdfplumber、明确 Mock 结果的闭环 | 上传至修订、复核、回退和导出通过 |
| 1440×900 / 390×844 视觉核查 | PDF、证据、状态、移动切换及长错误可见 |
| 交付演示视频 | 50.04 秒；明确标注预置模型结果 |
| 阶段7保护核查 | 8 项核心摘要匹配，未重新冻结或评测 |

保留真实 MinerU opt-in 跳过、既有弃用与 bundle 警告。完整手机真实 API 演练属于开发者交付证据；独立核验覆盖手机 E2E、实际截图和桌面真实 API 闭环。Mock 演示不能证明实时 Hy3 效果；固定样本实验不代表盲测或泛化。

## 发布内容与历史

本次承接本地实现历史和 GitHub 已有历史。已验收工作区基线为 `1e3a3c67fa47f68c97d742ad336bb9960adbeb96` 加阶段8交付；原阶段7评测代码基线仍为 `b625ec75a9fffbde430ed4fdba3db938e818a096`。发布合并不替换实验冻结的代码基线。

包含应用源码、锁文件、MIT、第三方许可与归属、合成测试夹具、发布记录、明确标注的演示及参考 smoke 原始结果。`.env`、凭据、本地数据库、Python/Node 环境、历史临时报告、锁和 pending 文件、私有输入及阶段7外部证据目录不随本次源码发布上传。

根目录 `paperlens_project_proposal.md` 是 GitHub 已有的原始方案，保留作为历史；当前开发契约见 `docs/DEV_PLAN.md`，阶段7实验结论见 `docs/stage7_closeout.md`。原阶段8开发报告及扫描 inventory 是发布前的证据快照，其中“尚未提交”“无 remote”和本机路径保留为当时事实，不代表 GitHub 发布后的状态，也不是克隆后所有文件的实时扫描结果。

本次新增发布说明及 README 导航，不修改业务实现、测试断言、API、Schema、依赖、评分或错误码。未运行 Live 或真实 MinerU。

安装、启动与合成演练按 [README](../README.md)；视频见 [stage8_demo.webm](../reports/stage8_demo.webm)。演示使用真实本地 API 与 pdfplumber，模型回答为显式 Mock。真实论文、外部 API 和对外部署仍须分别处理权限、费用及使用场景的验证。
