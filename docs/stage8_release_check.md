# 阶段 8 最终交付与发布检查

执行日期：2026-09-10（Asia/Shanghai）。`STAGE_8=PASS_LOCAL_RELEASE`；`P0=PASS`；`REVIEW_STATE=READY_FOR_REVIEW`；`PRODUCTION_READY=NO`。

本交付范围是可从源码复现的本地学术解读闭环，不是公开运营服务。阶段 7 的 `PASS_FIXED_SAMPLE` 及其冻结保持原状；本轮没有新增付费 Live 调用或真实 MinerU 集成测试。此报告取代首轮未完成记录中的当前状态；首轮 1137/73 的结果属于修复前基线，不充当最终结果。

## 验收范围和修改边界

发布清单：干净环境安装与启动、Python/eval/前端/浏览器完整回归、密钥与全文扫描、环境变量/许可证/AI 归属、双尺寸视觉检查、两分钟演示、原始评测与报告对应。

允许发布文件是 README、LICENSE、第三方归属/AI 与数据许可说明、环境示例、演示和验收材料。用户已有未跟踪历史报告、锁文件、数据库及阶段 7 独立副本均保留，没有 reset、清理、提交或推送。`.env.example` 复核通过，无需修改。

仅修复两项已复现缺陷，每项不超过四个源文件/测试文件，先红测再最小实现：

| 修复 | 文件 | 红测与固定契约 |
| --- | --- | --- |
| 完整审计后快速检查误显示未完成 | `frontend/src/components/SidePanel.tsx`、同目录 `SidePanel.test.tsx`、`frontend/e2e/workbench.spec.ts` | 状态测试先失败；修复后聚焦 8 passed。只修正完成状态展示，不改评分/API。 |
| 默认 Mock 与真实 pdfplumber 分块不匹配，完整审计 502 | `backend/app/hy3_service.py`、`backend/tests/test_api.py`、`backend/tests/test_hy3_service.py` | 两个句子/全文真实 API 闭环测试先因 `AUDIT_INCOMPLETE` 失败；修复后 Hy3+API 284 passed。仅匹配合成页内容和明确 Mock 修订；坏夹具、缺字段、未知修改仍拒绝，Live 和评分不变。用户已明确授权该适配。 |

未新增产品功能、依赖、API、Schema、数据表或后台服务。项目 MIT 由权利人明确确认。

## P0 / P1 / P2

| 范围 | 状态和证据 |
| --- | --- |
| P0 上传、解析、五区联合生成、证据核验、快速/完整审计、页码与摘录 | 通过 Python 全测、真实本地 API 演示及 PDF 画布检查；供应商失败仍保留错误。 |
| P0 句子/全文修订、补丁确认、复核、历史、回退、Markdown 导出 | 两种修订真实 API 集成测试通过；桌面/手机实际完成句子修订到导出。预览不写版本，确认后写入；回退保留历史。 |
| P0 最终评测和失败案例 | 阶段 7 已通过冻结固定样本门槛，原始 88 行与报告/验收摘要复核一致。holdout-02 修订未解决、旧批失败和审计错误用例均保留。 |
| P1 文本查找高亮 | 已实现并实看第二页定位和高亮；找不到文本时降级到页码/摘录。不保证所有 PDF 的文本层匹配。 |
| P2 bbox 精确覆盖、复杂 OCR/表格正式支持 | 未验收，不宣称完成。真实 MinerU 是可选独立工具，本轮跳过对应 opt-in 测试。 |

## 环境与实际输出摘要

Windows PowerShell；Python 3.13.3、pip 25.0.1；Node 24.13.0、npm 11.6.2；Playwright 1.62.1 / Chromium 151.0.7922.34。新 Python venv 的 `include-system-site-packages=false`，40 个非 editable 包与 `requirements.lock` 一致，`pip check` 为 `No broken requirements found`。

在忽略的临时干净副本重新创建 venv，从锁文件安装全部依赖；pip 实际使用机器已配置的镜像源，未使用全局业务包。Node 来自官方 Windows x64 ZIP，SHA256 与官方 SHASUMS256 一致；使用独立 npm cache 和官方 npm registry 执行 `npm ci`，81 packages added、82 audited。Chromium/ffmpeg 下载到独立浏览器目录，未依赖全局浏览器缓存。按 README 启动 uvicorn 和 Vite，`/api/health`、首页和入口模块均返回 HTTP 200。

| 验证 | 最终主目录真实结果 | 干净副本真实结果 |
| --- | --- | --- |
| `python -m pytest backend/tests eval/test_eval.py -q -p no:cacheprovider` | **1141 passed, 1 skipped, 1 warning in 28.43s** | **1141 passed, 1 skipped in 30.69s** |
| `python eval/run_eval.py --mode smoke` | REQUESTED=11、APPENDED=0、SKIPPED=11（已有结果续跑） | REQUESTED=11、APPENDED=11、SKIPPED=0 |
| `npm.cmd run test -- --run` | **5 files / 74 passed**, 2.57s | **74 passed**, 4.12s |
| `npm.cmd run typecheck` | exit 0 | exit 0 |
| `npm.cmd run build` | exit 0，1823 modules，3.21s | exit 0，3.28s；产物内容一致 |
| `playwright.cmd test` | **6 passed**, 8.8s | **6 passed**, 8.8s |
| `npm.cmd audit` | **found 0 vulnerabilities** | **found 0 vulnerabilities** |
| 新文件 smoke（修复后） | **11 succeeded / 0 supplier calls** | 不以参考回放冒充 Live |
| 手机真实 API 脚本 | **MOBILE_REAL_API_COMPLETE**；390×844，PDF ink/修订/复核/回退/导出均 true | 使用干净副本服务 |

主目录最后全回归的前端开始时间为 16:03:14；手机最终记录为 16:28:23。跳过项是 `backend/tests/test_document_service.py` 的真实 MinerU opt-in。没有删除失败测试或用 `--passWithNoTests` 放行。

保留的警告：Starlette/httpx 弃用、lucide use-client 忽略、JS bundle 超过 500 kB（635.58 kB，gzip 193.15 kB；PDF worker 1239.05 kB）、NO_COLOR/FORCE_COLOR。首次沙箱回归遇到 pytest 临时 `.lock` PermissionError，Vite 遇到 spawn EPERM；确认是沙箱执行问题后按相同命令在获准环境重跑通过，没有据此修改业务。手机验收脚本曾因生成/版本刷新后未重新切换面板而超时；修正脚本时序后完整通过，不算产品失败被忽略。

## 安全、许可证及数据检查

扫描器为 [stage8_scan.py](stage8_scan.py)，机器可读结果为 [stage8_scan.json](../reports/stage8_scan.json)。覆盖 Git 跟踪和可交付未跟踪文件、全部 `reports/`（包括被 Git 忽略的 JSONL）、`frontend/dist`。对本机 Settings 中的实际 Key 只作匹配，绝不输出值或命中正文；另查高置信度 Key、私钥、带凭据 URL、PDF、日志/长文本/原始响应字段及绝对路径。最终 blocking findings 为 0，12 个环境变量与 DEV_PLAN 完全一致，示例 Key 为空、模式为 mock。每个扫描文件有 SHA256；扫描输出自身不递归纳入。

三个仓库 PDF 均按内容/哈希确认是合成测试夹具：正常两页 149 字符、空白扫描一页 0 字符、刻意损坏 PDF。无私密 PDF 或许可不明全文。`eval/live_cases.json` 是公开论文的短证据整理与错误注入，不含论文全文。媒体只展示合成内容，已人工查看截图和视频关键帧。既有测试中的固定假 Key 只按文件加精确摘要列为 reviewed_test_sentinel，不排除整个测试目录。

绝对本机路径**并非零匹配**：DEV_PLAN、会话模板、阶段 7 归档位置及本报告用户指定的验收命令含已知本地路径，逐项记录为历史/运行说明；未删除审计证据以伪造零匹配。下方三段复现脚本没有绝对临时路径、明文密钥或占位符。开发环境、数据目录、缓存、私有 `.env` 不属于交付包。扫描是有边界的检测，不声称数学证明所有未知秘密不存在。

[LICENSE](../LICENSE) 为经确认的 MIT。[第三方/AI/数据归属](THIRD_PARTY_NOTICES.md) 覆盖 MinerU、PDF.js、Hy3、运行时依赖、AI 辅助开发与生成限制及 10 篇公开论文的作者/正式标题/来源。当前许可和安装方法已核对官方页面及锁定包内文件。MinerU 为 Apache 2.0 加附加条款，不能简写纯 Apache；Hy3 API/模型不随项目 MIT 授权。前端所含第三方完整许可另见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。若单独分发 dist，须随附此文件、归属说明及项目 LICENSE；本次交付未打包 Python/Node 环境或模型权重。

阶段 7 外部汇总原始结果、报告、验收 JSON 也只读核对了摘要和 Key 模式，结果见 [外部证据复核](../reports/stage8_external_evidence.json)。没有复制私密原文到仓库。

## 视觉、演示与评测文件

实际查看了 Playwright 1440×900 桌面和 390×844 手机截图，而非仅看 DOM：PDF 非空；桌面三栏无重叠；手机按页签切换，长错误可滚动且不横向溢出；状态、错误码、重试按钮可见。错误页来自明确的 E2E 预置路由，界面的 Live 字样是失败场景模拟，不是本轮真实 Live 证据。

- [约 50 秒完整演示](../reports/stage8_demo.webm)：媒体时长 50.04 秒、1440×900；脚本流程 48.866 秒。全程标明“Mock 模型预置结果、非实时 Hy3、真实本地 API/文本解析”。
- [演示步骤记录](../reports/stage8_demo_steps.json)、[排练记录](../reports/stage8_demo_rehearsal.json)、[导出 Markdown](../reports/stage8_demo_export.md)。画面完整包含上传、解析、生成/自动快审、深审、句子页码/摘录、修订预览、接受、复核、历史回退与导出。没有新的 Live 授权，故采用用户允许的显式预置模型结果方式。
- [桌面页码/摘录](../reports/stage8_demo_evidence.png)、[补丁预览](../reports/stage8_demo_preview.png)、[手机真实 PDF](../reports/stage8_mobile_real_pdf.png)、[手机真实审计](../reports/stage8_mobile_real_audit.png)、[手机完成结果](../reports/stage8_mobile_result.json)。
- 标准 E2E 截图位于 `frontend/test-results/` 的三个 workbench-preset 目录；视频抽帧 `reports/stage8_video_final_2.png`、`18.png`、`27.png`、`43.png` 已用于逐步实看。
- 最终新 smoke：`reports/stage8_final_smoke_results.jsonl` 与 [对应报告](../reports/stage8_final_smoke_report.md)。11 条成功、0 供应商调用；版本是 `hy3-reference-replay / audit-v2 / deep-audit-result-v2`，不能作为当前 Live 模型效果证明。
- 正式 final/stability 仍按 [阶段 7 收尾](stage7_closeout.md) 中的独立归档位置保存。88 条原始结果、报告和验收 JSON 的 SHA256 与该记录一致，未重新付费或改写冻结。固定样本：严格排序 5/5、成对 15/15、严重错误 5/5、引用准确/完整均 54/60、攻击 16/16、clean 误报 0/16、修订 4/5、稳定性标准差 1.19932885、八维一致 85/96。结论限于已查看固定样本。

| 本轮关键产物 | SHA256 |
| --- | --- |
| stage8_final_smoke_results.jsonl | d778662e72ae0a432b9a56063903582972dd0a7c4246de913518b9023a3c511d |
| stage8_final_smoke_report.md | a7576a4004dcd6d02f5d44519f4382c0533dee62c1ee2d3e8d1ea569ee18da5f |
| stage8_demo.webm | 18de906d89f128200bd6bf81c039621b5c1977d5f3417cee987c08b6e940588d |

## 三段完整 PowerShell 复现脚本

三段均从源码仓库根目录运行。解释器前提与 README 一致：官方 Python 3.13 和 Node 24.13.0，Git 可用；业务依赖全部本地安装。脚本不会输出明文 Key，不覆盖已有 `.env`；扫描通过 Settings 读取 Key 作本地匹配。演练显式 Mock，使用合成 PDF。每个安装/测试外部命令检查退出码。

三段脚本均从本报告逐字提取后执行通过，另含最终回归的全部 4 个 PowerShell 块语法解析通过。脚本 1 在此前已验证的干净副本复跑，pip check 无损坏、npm ci 81 packages/0 vulnerabilities、两个服务 HTTP 200，打印的停止命令也实际验证；这次脚本复跑不另冒充一次全新 Python 安装。脚本 2 覆盖 158 个文件、0 blocking findings。脚本 3 在隔离副本重新完成全部步骤，输出 `Mock demo complete`、流程 48.801 秒，未覆盖上方交付视频及其哈希。

### 1. 全新环境安装并启动

从新取得的源码根目录执行；8000/5173 须空闲。启动后打印可直接执行的停止命令，服务在后台运行。

```powershell
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Get-Location).Path
if (-not (Test-Path -LiteralPath 'requirements.lock')) { throw 'Run from repository root' }
foreach ($Port in @(8000,5173)) {
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { throw "Port $Port is busy" }
}
& py -3.13 -m venv .venv313
if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed' }
$Python = (Resolve-Path '.\.venv313\Scripts\python.exe').Path
& $Python -m pip install -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Python installation failed' }
& $Python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Python dependency check failed' }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
Push-Location -LiteralPath frontend
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Node dependency installation failed' }
    & '.\node_modules\.bin\playwright.cmd' install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Browser installation failed' }
} finally { Pop-Location }
$Node = (Get-Command node.exe -ErrorAction Stop).Source
$env:PAPERLENS_MODEL_MODE = 'mock'
$env:PAPERLENS_DATA_DIR = './data/demo'
$env:MINERU_COMMAND = './.venv-mineru-disabled/Scripts/mineru.exe'
$Backend = Start-Process -FilePath $Python -ArgumentList '-m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000' -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
$Frontend = $null
try {
    $Frontend = Start-Process -FilePath $Node -ArgumentList 'node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort' -WorkingDirectory (Join-Path $ProjectRoot 'frontend') -WindowStyle Hidden -PassThru
    foreach ($Url in @('http://127.0.0.1:8000/api/health','http://127.0.0.1:5173')) {
        $Ready = $false
        for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
            if ($Backend.HasExited -or $Frontend.HasExited) { throw 'Service exited during startup' }
            try { $Ready = (Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { $Ready = $false }
            if ($Ready) { break }
            Start-Sleep -Milliseconds 500
        }
        if (-not $Ready) { throw "Service not ready: $Url" }
    }
    Write-Output 'PaperLens Mock: http://127.0.0.1:5173'
    Write-Output "Stop-Process -Id $($Backend.Id),$($Frontend.Id)"
} catch {
    if ($Frontend -and -not $Frontend.HasExited) { Stop-Process -Id $Frontend.Id }
    if (-not $Backend.HasExited) { Stop-Process -Id $Backend.Id }
    throw
}
```

### 2. 密钥、私密全文及交付产物扫描

同时列出需人工解释的绝对路径；不回显命中内容。结果写入 `reports/stage8_scan.json`，非零阻塞数会失败。已审查 PDF 仅按精确摘要允许，新增 PDF 必须重新确认权限和内容。

```powershell
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath 'docs/stage8_scan.py')) { throw 'Run from repository root' }
$Python = (Resolve-Path '.\.venv313\Scripts\python.exe').Path
& $Python 'docs/stage8_scan.py'
if ($LASTEXITCODE -ne 0) { throw "Release content scan failed: $LASTEXITCODE" }
$Scan = Get-Content -LiteralPath 'reports/stage8_scan.json' -Raw | ConvertFrom-Json
$Scan.findings | Select-Object path,line,category,blocking | Format-Table -AutoSize
if (-not $Scan.env_contract_passed -or $Scan.blocking_findings -ne 0) { throw 'Release content gate failed' }
Write-Output "Scanned $($Scan.files_scanned) files; blocking findings: $($Scan.blocking_findings)"
```

### 3. 两分钟演示启动、录制并停止服务

先结束脚本 1 启动的服务，再运行本段。没有占用已有服务，也不会调用真实 Hy3；录制脚本上传后要求页面确认为 Mock 才生成。将覆盖本轮同名演示产物，失败则非零退出。

```powershell
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Get-Location).Path
if (-not (Test-Path -LiteralPath 'docs/stage8_demo.cjs')) { throw 'Run from repository root' }
foreach ($Port in @(8000,5173)) {
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { throw "Port $Port is busy" }
}
$Python = (Resolve-Path '.\.venv313\Scripts\python.exe').Path
$Node = (Get-Command node.exe -ErrorAction Stop).Source
$env:PAPERLENS_MODEL_MODE = 'mock'
$env:PAPERLENS_DATA_DIR = './data/demo'
$env:MINERU_COMMAND = './.venv-mineru-disabled/Scripts/mineru.exe'
$Backend = $null
$Frontend = $null
try {
    $Backend = Start-Process -FilePath $Python -ArgumentList '-m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000' -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
    $Frontend = Start-Process -FilePath $Node -ArgumentList 'node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort' -WorkingDirectory (Join-Path $ProjectRoot 'frontend') -WindowStyle Hidden -PassThru
    foreach ($Url in @('http://127.0.0.1:8000/api/health','http://127.0.0.1:5173')) {
        $Ready = $false
        for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
            if ($Backend.HasExited -or $Frontend.HasExited) { throw 'Service exited during startup' }
            try { $Ready = (Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { $Ready = $false }
            if ($Ready) { break }
            Start-Sleep -Milliseconds 500
        }
        if (-not $Ready) { throw "Service not ready: $Url" }
    }
    & $Node 'docs/stage8_demo.cjs'
    if ($LASTEXITCODE -ne 0) { throw "Demo failed: $LASTEXITCODE" }
    $Demo = Get-Content -LiteralPath 'reports/stage8_demo_steps.json' -Raw | ConvertFrom-Json
    if (-not $Demo.complete -or $Demo.real_hy3 -or $Demo.duration_seconds -gt 120) { throw 'Demo acceptance failed' }
    Write-Output "Mock demo complete: reports/stage8_demo.webm; flow $($Demo.duration_seconds) seconds"
} finally {
    if ($Frontend -and -not $Frontend.HasExited) { Stop-Process -Id $Frontend.Id }
    if ($Backend -and -not $Backend.HasExited) { Stop-Process -Id $Backend.Id }
}
```

## 用户指定的最终完整回归命令

以下命令已在最终产品源码上完整执行，结果对应上表；之后仅补充发布说明、许可文本、扫描和演示证据。

```powershell
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath "D:\Hy3"
$Python = (Resolve-Path ".\.venv313\Scripts\python.exe").Path
& $Python -m pytest "backend/tests" "eval/test_eval.py" -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Final Python tests failed: $LASTEXITCODE" }
& $Python "eval/run_eval.py" --mode smoke
if ($LASTEXITCODE -ne 0) { throw "Final smoke evaluation failed: $LASTEXITCODE" }
Push-Location -LiteralPath "D:\Hy3\frontend"
try {
    & npm.cmd run test -- --run
    if ($LASTEXITCODE -ne 0) { throw "Final frontend tests failed: $LASTEXITCODE" }
    & npm.cmd run typecheck
    if ($LASTEXITCODE -ne 0) { throw "Final typecheck failed: $LASTEXITCODE" }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "Final production build failed: $LASTEXITCODE" }
    & ".\node_modules\.bin\playwright.cmd" test
    if ($LASTEXITCODE -ne 0) { throw "Final Playwright tests failed: $LASTEXITCODE" }
    & npm.cmd audit
    if ($LASTEXITCODE -ne 0) { throw "Final npm audit failed: $LASTEXITCODE" }
}
finally {
    Pop-Location
}
```

最终新 smoke 的可重建报告命令（从根目录、使用本地 Python）：`python eval/build_report.py --input reports/stage8_final_smoke_results.jsonl --output reports/stage8_final_smoke_report.md`。原始 JSONL 被仓库既有忽略规则排除，交付时须单独保留，不能只复制 Git 跟踪文件而丢失结果。

## 已知限制和交付边界

1. 本地源码发布验收通过，不等于公开部署、安全多租户或通用论文可靠性认证；应用没有项目删除界面，数据保留在配置的数据目录。
2. Mock 只支持固定合成材料和预置修订。真实演示的 85 分“不合格”由现有引用判定产生，未隐藏、改阈值或伪装合格。完成操作链不等同任何内容都通过审计。
3. 本轮未进行实时 Hy3 或重新运行真实 MinerU；保留所有 Live 错误边界，OCR/bbox 等 P2 不承诺。正式模型效果仍仅引用阶段 7 固定样本结果及其失败案例。
4. 历史审计材料含本机路径；正式评测原始数据在独立本地归档，尚无 Git remote 或远程备份。未获授权不搬迁旧敏感目录、不改写历史、不提交或推送。
5. 阶段 8 源码、许可和演示材料尚为本地未提交工作，已有用户改动完整保留。交付依据是本报告、扫描清单与实际产物，不是不存在的发布提交。
