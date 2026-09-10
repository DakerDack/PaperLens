# PaperLens

PaperLens 是一个基于 Hy3 的可信学术解读、主张级证据审计与对话式修订项目。

这是个人项目，非腾讯官方产品。已实现上传、解析、五区联合生成、证据核验、快速/完整审计、页码和摘录、句子/全文补丁预览与确认、版本回退及 Markdown 导出。阶段 8 已通过独立验收，范围限本地源码交付；`PRODUCTION_READY=NO`。参见 [发布与独立验收说明](docs/stage8_publish.md) 和 [开发交付实测记录](docs/stage8_release_check.md)。

模型默认 **Mock（预置合成结果）**，不是实时 Hy3；Live 失败不会回退 Mock。Mock 只适合仓库合成 PDF 演练，不用于解读任意论文。快速检查不产生完整评分；完整审计仍可能出错，需人工核对原文。

## 从零安装应用（Windows PowerShell）

先安装 [Python 3.13](https://www.python.org/downloads/windows/) 和 [Node.js](https://nodejs.org/en/download)。本轮使用 Python 3.13.3、Node 24.13.0；不要使用全局 Python 业务包或全局 Vite。以下命令从仓库根目录运行，保留已有 `.env`：

```powershell
$ErrorActionPreference = 'Stop'
py -3.13 -m venv .venv313
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
```

## 启动与合成演练

在仓库根目录的第一个终端执行。演练使用独立数据目录，显式禁用真实 MinerU 命令以走 pdfplumber 文本解析；这不是 MinerU 集成验证：

```powershell
$ErrorActionPreference = 'Stop'
$env:PAPERLENS_MODEL_MODE = 'mock'
$env:PAPERLENS_DATA_DIR = './data/demo'
$env:MINERU_COMMAND = './.venv-mineru-disabled/Scripts/mineru.exe'
& '.\.venv313\Scripts\python.exe' -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
if ($LASTEXITCODE -ne 0) { throw 'Backend stopped with an error' }
```

第二个终端从仓库根目录执行：

```powershell
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath frontend
try {
    & npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
    if ($LASTEXITCODE -ne 0) { throw 'Frontend stopped with an error' }
} finally { Pop-Location }
```

打开 [工作台](http://127.0.0.1:5173)；[API 文档](http://127.0.0.1:8000/api/docs)。上传 `backend/tests/fixtures/simple_2page.pdf` 并确认处理权限，生成后检查 Mock 标记和快速检查，再运行完整审计。点击第二页句子查看摘录；修改意图输入“只追加安全标点”，预览后接受、重新深审、回退到版本 1 并导出。全文修改也可预览、接受和复核，但可能出现证据不足或不合格，不能把完成审计等同合格。

阶段 8 曾发现 pdfplumber 分块与静态 Mock 夹具不一致导致 `AUDIT_INCOMPLETE`，已增加仅针对内容完全匹配合成页的适配，并保留坏夹具拒绝测试。Mock 仅支持该固定合成材料及明确预置修订；不是通用模型。演示使用真实本地 API 与 pdfplumber，模型回答为明确标注的预置结果，见 [视频](reports/stage8_demo.webm)。其他材料及未知修订仍可能失败。

本地 PDF 与项目历史保存在数据目录，不会在关闭浏览器时自动删除；应用没有项目删除界面。不要上传私密论文或审稿材料。两个终端分别按 Ctrl+C 停止服务。

## Live

仅在有处理权限和费用授权时，将本机 `.env` 的 `HY3_API_KEY` 填入有效密钥，设置 `PAPERLENS_MODEL_MODE=live`，并移除终端里覆盖模式的环境变量后启动。不得提交 `.env`。使用上面的 Mock 启动脚本仍会显式覆盖成 Mock。Live 会将来源块、生成文档和修订上下文发送至 TokenHub，页面必须显示 Live；供应商不可用、结构无效或审计不完整会明确失败。

页码和引文来自解析与代码核验；分数及门槛由代码计算，均不由 Hy3 直接决定。普通文本 PDF 支持 pdfplumber 降级。P1 文本查找高亮已实现，匹配失败时保留页码和摘录；P2 bbox 精确覆盖和复杂 OCR/表格没有正式验收，不宣称支持。

阶段 7 仅在已查看的固定样本上通过，非盲测或泛化证明；holdout-02 的修订未解决。完整数据位置、哈希和失败限制见 [阶段 7 收尾](docs/stage7_closeout.md)。`eval/run_eval.py --mode smoke` 是参考回放，无供应商调用，不替代正式评测。

## 环境

- PaperLens 应用：Python 3.13（当前实测 3.13.3）
- MinerU 独立工具：CPython 3.12（当前实测 3.12.14）
- Node.js 24.13.0（本轮使用独立官方 Windows x64 归档复现）
- MinerU 3.4.5 `pipeline`，只通过独立 CLI 调用
- 腾讯云大模型服务平台 TokenHub 的 Hy3 访问权限，进入真实模型探针阶段后配置

## 可选：Windows 上重建 MinerU 工具环境

这是历史锁定的独立工具安装记录；阶段 8 的全新环境验证使用 pdfplumber，未重新下载/运行 MinerU 模型。MinerU 不安装到应用的 `.venv313`。需要此可选工具时，先安装官方 CPython 3.12，并确保 `py -3.12` 可用，然后在仓库根目录执行：

```powershell
$ErrorActionPreference = "Stop"

py -3.12 -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"
py -3.12 -m venv ".venv-mineru312"
$MinerUPython = (Resolve-Path ".\.venv-mineru312\Scripts\python.exe").Path

& $MinerUPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'MinerU pip upgrade failed' }
& $MinerUPython -m pip install --index-url "https://download.pytorch.org/whl/cpu" "torch==2.8.0+cpu" "torchvision==0.23.0+cpu"
if ($LASTEXITCODE -ne 0) { throw 'MinerU torch installation failed' }
& $MinerUPython -m pip install "mineru[pipeline]==3.4.5" "onnxruntime==1.29.0" "six==1.17.0"
if ($LASTEXITCODE -ne 0) { throw 'MinerU installation failed' }
& $MinerUPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'MinerU dependency check failed' }

$MinerU = (Resolve-Path ".\.venv-mineru312\Scripts\mineru.exe").Path
& $MinerU -v
```

`six==1.17.0` 是 MinerU 3.4.5 pipeline 当前实际启动所需的隔离工具依赖，不加入 PaperLens 的 `pyproject.toml`。

MinerU 使用独立工具环境，不写入应用的 `requirements.lock`。PaperLens 默认通过 `./.venv-mineru312/Scripts/mineru.exe` 调用它。

首次下载模型并用公开合成夹具冒烟验证：

```powershell
$ErrorActionPreference = "Stop"
$MinerU = (Resolve-Path ".\.venv-mineru312\Scripts\mineru.exe").Path
$SmokeOutput = Join-Path (Resolve-Path ".").Path ".test-tmp-mineru-smoke"

$env:MINERU_MODEL_SOURCE = "modelscope"
$env:MODELSCOPE_CACHE = Join-Path (Resolve-Path ".").Path ".venv-mineru312\models"
$env:HF_HOME = Join-Path (Resolve-Path ".").Path ".venv-mineru312\hf-cache"

& $MinerU -p "backend\tests\fixtures\simple_2page.pdf" -o $SmokeOutput -b pipeline -m txt
if ($LASTEXITCODE -ne 0) { throw "MinerU smoke test failed" }
# 保留输出供检查；不要将模型缓存或解析原文提交到仓库。
```

默认配置会从仓库内 `./.venv-mineru312/Scripts/mineru.exe` 调用 MinerU；也可通过本机 `.env` 的 `MINERU_COMMAND` 显式覆盖。

## 应用 Python 依赖锁

`requirements.lock` 是应用 `.venv313` 的完整非 editable 冻结结果，不包含独立 MinerU 环境。新环境使用以下命令安装和检查：

```powershell
$ErrorActionPreference = "Stop"
$Python = (Resolve-Path ".\.venv313\Scripts\python.exe").Path

& $Python -m pip install -r "requirements.lock"
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed: $LASTEXITCODE" }
& $Python -m pip check
if ($LASTEXITCODE -ne 0) { throw "Python dependency check failed: $LASTEXITCODE" }
```

维护者只能用 `.\.venv313\Scripts\python.exe -m pip freeze --exclude-editable` 的实际输出更新该文件，并保持 UTF-8 无 BOM；不得写入 `file:///`、本机绝对路径、editable 项目或密钥。

## 当前验证

```powershell
$ErrorActionPreference = "Stop"
$Python = (Resolve-Path ".\.venv313\Scripts\python.exe").Path

& $Python -m pip check
if ($LASTEXITCODE -ne 0) { throw "Python dependency check failed: $LASTEXITCODE" }
& $Python -m pytest "backend/tests" "eval/test_eval.py" -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Final Python tests failed: $LASTEXITCODE" }
& $Python "eval/run_eval.py" --mode smoke
if ($LASTEXITCODE -ne 0) { throw "Final smoke evaluation failed: $LASTEXITCODE" }

Push-Location "frontend"
try {
    & npm.cmd run test -- --run
    if ($LASTEXITCODE -ne 0) { throw "Final frontend tests failed: $LASTEXITCODE" }
    & npm.cmd run typecheck
    if ($LASTEXITCODE -ne 0) { throw "Final typecheck failed: $LASTEXITCODE" }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "Final production build failed: $LASTEXITCODE" }
    & '.\node_modules\.bin\playwright.cmd' test
    if ($LASTEXITCODE -ne 0) { throw "Final Playwright tests failed: $LASTEXITCODE" }
    & npm.cmd audit
    if ($LASTEXITCODE -ne 0) { throw "Final npm audit failed: $LASTEXITCODE" }
}
finally {
    Pop-Location
}
```

API Key 只能写入本机 `.env`。不要上传私密论文、未公开稿件、审稿材料或许可不明全文。

项目自有代码使用 [MIT](LICENSE)。MinerU、PDF.js、Hy3、依赖、论文署名和 AI 使用边界见 [第三方归属与数据说明](docs/THIRD_PARTY_NOTICES.md)。
