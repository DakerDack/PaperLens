# PaperLens

PaperLens 是一个基于 Hy3 的可信学术解读、主张级证据审计与对话式修订项目。

当前正在按照 [`docs/DEV_PLAN.md`](docs/DEV_PLAN.md) 逐阶段开发。阶段 1 已实现 PDF 解析与规范化证据块；后续生成、核验和审计能力尚未实现。

## 环境

- PaperLens 应用：Python 3.13（当前实测 3.13.3）
- MinerU 独立工具：CPython 3.12（当前实测 3.12.14）
- 当前维护中的 Node.js LTS 或更新版本
- MinerU 3.4.5 `pipeline`，只通过独立 CLI 调用
- 腾讯云大模型服务平台 TokenHub 的 Hy3 访问权限，进入真实模型探针阶段后配置

## Windows 上重建 MinerU 工具环境

MinerU 不安装到应用的 `.venv313`。先安装官方 CPython 3.12，并确保 `py -3.12` 可用，然后在仓库根目录执行：

```powershell
$ErrorActionPreference = "Stop"

py -3.12 -c "import sys; assert sys.version_info[:3] == (3, 12, 14), sys.version"
py -3.12 -m venv ".venv-mineru312"
$MinerUPython = (Resolve-Path ".\.venv-mineru312\Scripts\python.exe").Path
$MinerU = (Resolve-Path ".\.venv-mineru312\Scripts\mineru.exe" -ErrorAction SilentlyContinue)

& $MinerUPython -m pip install --upgrade pip
& $MinerUPython -m pip install --index-url "https://download.pytorch.org/whl/cpu" "torch==2.8.0+cpu" "torchvision==0.23.0+cpu"
& $MinerUPython -m pip install "mineru[pipeline]==3.4.5" "onnxruntime==1.29.0" "six==1.17.0"
& $MinerUPython -m pip check

$MinerU = (Resolve-Path ".\.venv-mineru312\Scripts\mineru.exe").Path
& $MinerU -v
```

`six==1.17.0` 是 MinerU 3.4.5 pipeline 当前实际启动所需的隔离工具依赖，不加入 PaperLens 的 `pyproject.toml`。

首次下载模型并用公开合成夹具冒烟验证：

```powershell
$ErrorActionPreference = "Stop"
$MinerU = (Resolve-Path ".\.venv-mineru312\Scripts\mineru.exe").Path
$SmokeOutput = Join-Path (Resolve-Path ".").Path ".test-tmp-mineru-smoke"

$env:MINERU_MODEL_SOURCE = "modelscope"
$env:MODELSCOPE_CACHE = Join-Path (Resolve-Path ".").Path ".venv-mineru312\models"
$env:HF_HOME = Join-Path (Resolve-Path ".").Path ".venv-mineru312\hf-cache"

try {
    & $MinerU -p "backend\tests\fixtures\simple_2page.pdf" -o $SmokeOutput -b pipeline -m txt
    if ($LASTEXITCODE -ne 0) { throw "MinerU smoke test failed" }
}
finally {
    if (Test-Path -LiteralPath $SmokeOutput) {
        Remove-Item -LiteralPath $SmokeOutput -Recurse -Force
    }
}
```

默认配置会从仓库内 `.venv-mineru312\Scripts\mineru.exe` 调用 MinerU；也可通过本机 `.env` 的 `MINERU_COMMAND` 显式覆盖。

## 当前验证

```powershell
.\.venv313\Scripts\python.exe -m pytest backend\tests\test_document_service.py -q -p no:cacheprovider
.\.venv313\Scripts\python.exe -m pytest backend\tests -q -p no:cacheprovider
```

API Key 只能写入本机 `.env`。不要上传私密论文、未公开稿件、审稿材料或许可不明全文。
