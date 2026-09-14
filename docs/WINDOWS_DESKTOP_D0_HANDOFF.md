# D0 独立验收交接单

请独立验收会话只读审查 D0，返回 PASS 或具体返修项。用户已选择手工转发；开发没有声称已通过工具通知对方。无需第三个总工会话。

## 卡号与基线

- 卡号：D0，现状核查和具体方案（纯文档卡）。
- 工作区：`D:\PaperLens`；分支：`codex/windows-desktop`。
- HEAD：`92b1ed6f64b6e88a5cb5cf95d429d143a54d1fa3`。
- 开工工作区干净；从 `codex/postrelease-b-direction-precondition` 同 HEAD 创建新分支。
- 创建分支首次因沙箱 `.git` 只读失败，随后获执行权限成功；不是代码或仓库损坏。
- 当前无 D0 提交、无推送、无 PR。PASS 后由开发提交并记录提交号，再进入 D1a。

## 差异和保护范围

仅三文件：

1. `docs/DEV_PLAN.md`：新增 Windows 阶段入口，登记 D0、白名单、验收门槛。
2. `docs/WINDOWS_DESKTOP_PLAN.md`：具体架构、现状证据、依赖审查、固定桌面控制契约、逐卡文件清单及验证命令。
3. `docs/WINDOWS_DESKTOP_D0_HANDOFF.md`：本交接单及核查记录。

无业务代码、依赖安装、构建产物、数据、历史结果、Prompt、Schema、评分变化；未读取真实 `.env`、既有凭据、私有论文或 D:\Hy3。未调用模型或 MinerU。

## D0 唯一 P2 返修交接（2026-09-12，待复核）

收到独立验收 `D0 FAIL`：仅缺少显式清除 Key 契约；架构选型不变。验收基线 HEAD 为 `92b1ed6f64b6e88a5cb5cf95d429d143a54d1fa3`，方案 SHA256 为 `66AAF65C8638B9C24F001398C0D3D4B4E12CFE385A22A1ED68E9BF902FF91511`，已于返修前核对一致。

本轮只修改方案和本交接单；`docs/DEV_PLAN.md` 未再修改。相对 HEAD 仍仅三份原约定文档变更。修订位置：

- 方案第 64 行：新增无参数 `clear_desktop_key()`，成功信封为 `{ok:true,value:{key_configured:false,restart_required:true}}`。
- 方案第 66 行：独立清除入口；凭据不存在/重复清除幂等成功；删除失败沿用 `DESKTOP_CREDENTIAL_UNAVAILABLE`，非法参数沿用 `DESKTOP_SETTINGS_INVALID`；不返回 Key，不把查询失败表示为 false。
- 方案第 68 行：持久化凭据立即清除，当前实例内存配置及进行中请求不变，明确提示重启生效；重启后保持模式且无 Key，live 沿用 `HY3_CONFIG_MISSING`。清除无网络调用，不操作论文、DB、其他设置或其他用户凭据。
- 方案第 118–120 行：D3a 覆盖本次专用合成目标的成功、失败、不存在/重复清除；D3b 覆盖桥、当前实例不变与重启后无 Key；D3c 覆盖显式入口、反馈、重复操作和原生重启验证。原四文件白名单不变，所有凭据验证仅用本次专用合成目标，不读取/操作真实凭据。

已执行用户指定四条命令：

```powershell
git diff --check
git diff -- docs/DEV_PLAN.md
git status --short
Get-FileHash docs/DEV_PLAN.md,docs/WINDOWS_DESKTOP_PLAN.md -Algorithm SHA256
```

结果：`git diff --check` 通过，仅既有 CRLF → LF 提示；DEV_PLAN diff 仍为原 D0 入口的 12 行新增，其字节哈希未变；status 仍为 DEV_PLAN 已修改及方案/交接单未跟踪，无其他文件。以下为本次复核应使用的哈希，后文旧哈希仅保留初次交接记录：

```text
docs/DEV_PLAN.md（未变）
DA39D315C51FD7220863650BD311BCFB95E0B4A065D8A572BE7501FFE0BFDFF5
docs/WINDOWS_DESKTOP_PLAN.md（返修后）
7677CF1C9C3B5A1F75A5511097AC0CFDAB1EBC020655FCD6B74225D1B3A31BC9
```

仅文档返修，未提前编写实现/测试脚本，未进入 D1a、提交、安装依赖或执行凭据/模型操作。请只复核上述缺项与返修差异。`D0_REWORK=COMPLETE`；`D0_REVIEW=PENDING`。**工作区已冻结，等待 D0 复核。** 收到 D0 PASS 后才可提交并进入 D1a。

## 初次开发核查记录（历史记录，不是独立验收）

记录路径：`D:\PaperLens\docs\WINDOWS_DESKTOP_D0_HANDOFF.md`（本节）；原始工具输出保留在开发任务。D0 没有运行时测试日志，不存在可交付安装包。

实际已执行的检查摘要：

```text
git branch --show-current
codex/windows-desktop

git rev-parse HEAD
92b1ed6f64b6e88a5cb5cf95d429d143a54d1fa3

git diff --check
退出码 0；仅 Git CRLF -> LF 提示，无空白错误。

git diff --stat
docs/DEV_PLAN.md | 12 ++++++++++++
1 file changed, 12 insertions(+)
说明：普通 git diff 不包含未跟踪文件，必须同时查看 git status 和新文件全文。

方案覆盖词检查（仅防漏项，不代表语义或实现验收）：
D0 coverage missing: []
Plan lines: 194

交接前最终白名单检查：
D0 whitelist: PASS (3 files)
Plan card sequence: PASS (17 cards including D0)
```

检查脚本首次把任务总数手误写为 18 而失败（实际 17）；已按 D0、D1a-e、D2a-d、D3a-c、D4a-b、D5a-b 的完整预期序列重测通过，未修改方案以迎合计数。该失败不是业务测试。

方案 SHA256（当前工作文件字节，Git 后续行尾归一化可能改变 DEV_PLAN 字节哈希）：

```text
docs/DEV_PLAN.md
DA39D315C51FD7220863650BD311BCFB95E0B4A065D8A572BE7501FFE0BFDFF5
docs/WINDOWS_DESKTOP_PLAN.md
66AAF65C8638B9C24F001398C0D3D4B4E12CFE385A22A1ED68E9BF902FF91511
```

读取 Settings/入口/DocumentService/API 客户端/PdfPane/App/package 配置及现有 Playwright 配置，确认方案第 2 节的适配点。通过 Python 元数据查询（未导入业务）确认本机 Python/依赖，通过系统注册表与目录确认 OS build 和 WebView2 目录；没有实测 WebView2 实例。官方资料链接见方案第 11 节。

后端、前端、E2E、原生窗口、冻包、安装、干净机器、Live/MinerU：全部本卡 **NOT_RUN**。D0 不需要业务红绿测试，后续代码卡必须先失败测试。历史测试结果不作为本次通过证据。

## 独立验收步骤

1. 核对 HEAD、分支、工作区仅上述三文件；不要切分支、暂存、提交或修改共享文件。
2. 阅读 AGENTS、DEV_PLAN 新桌面阶段、本方案全文；新文件不在普通 git diff 中，不得漏读。
3. 对照现有源码复查固定端口、导入 `.env`、MinerU 优先入口、PDF.js 资源、下载与项目恢复问题；不要导入可能读取真实配置的业务模块。
4. 审查 D0 是否覆盖用户要求的十一类问题，路线是否在首版范围；关注原生导航/桥权限、所有 API token 防护、配置导入隔离、关闭一致性、数据可恢复、安装失败处理与目标机器门槛。
5. 审查每个后续子卡最多四文件、依赖许可/移除路径、先红后绿及实际验证接口。D1+ 脚本是待实现接口，不能假定已存在或已通过。
6. 给出 PASS 或按严重度列出可复现矛盾/缺项。PASS 只表示 D0 方案通过，不表示依赖、桌面兼容性或最终产品已通过。

建议只读命令（PowerShell，仓库根目录）：

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git diff --check
git diff -- docs/DEV_PLAN.md
Get-Content docs/WINDOWS_DESKTOP_PLAN.md
Get-Content docs/WINDOWS_DESKTOP_D0_HANDOFF.md
Get-FileHash docs/DEV_PLAN.md,docs/WINDOWS_DESKTOP_PLAN.md -Algorithm SHA256
```

## 等待状态和回复格式

`D0_DEVELOPMENT=COMPLETE`；`D0_INDEPENDENT_ACCEPTANCE=PENDING`；`DESKTOP_COMPATIBILITY=NOT_VERIFIED`；`LIVE_MODEL_VALIDATION=NOT_RUN`。

开发在交接后冻结共享工作区，等用户转回验收结果。请返回：`D0 PASS` 或 `D0 FAIL`，所审 HEAD/文件哈希、问题及位置、必要返修范围。FAIL 只返修 D0；PASS 后提交 D0 并继续 D1a，不需要用户再次批准范围内方案。
