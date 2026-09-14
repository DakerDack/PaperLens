# Windows 桌面安装验收

## 当前状态

- D4a 已独立 PASS，提交 `64f2c41466fa48a15d1f2b690e54a8634123c43e`。
- D4b 安装脚本及 P2 返修独立审查 **PASS**，三文件以“验收脚本准备”提交 `c723ed1f6b52a1ab5dfea1b40cb193211be0e769`；**D4b 整卡验收 PENDING，实际安装执行 NOT_RUN**。
- 用户批准先完成 D5a-prep 材料准备，实际安装、升级、卸载及干净 Windows 环境验证暂缓；最终放行前补齐。用户计划在另一台电脑手工测试，目前尚未收到安装证据；本机没有创建 PaperLens 测试账户。
- **D5a-prep 独立验收 PENDING，最终交付放行 PENDING**。本卡不推送、不创建 PR、不合并或公开 Release。
- 未安装、升级、卸载 PaperLens；未执行 WebView2 安装器。开发 Windows 环境不能替代没有 Python/Node 的干净机器。

## 已冻结的 D4a 输入

- 安装包：`D:\PaperLens\dist\installer\71079916bc464a17abea8d944e4eb947\PaperLens-0.1.0-windows-x64-setup.exe`
- 安装包 SHA256：`E46F2E79DE87F31029690C4316EA9FC4A0C544AC1C28D4276A1EE67266C2573D`
- 清单：`D:\PaperLens\dist\Release-59dc8aced9e24ac493bcd08c5a60e9bb\PaperLens-manifest.json`，762 项。
- 清单 SHA256：`5B83CBD9AA4E84AAB25D3FED5A5EB2B7359410261178FB71B12AB43A0CBD2D73`。
- 对应 onedir EXE：上述清单同级 `PaperLens\PaperLens.exe`；SHA256 `D51B09026ED2B69EB45F08F8F600C64FB53DCDB466C63D41C6A02BFA9D409B76`。
- 安装器版本 0.1.0；修改验收脚本不改变该包内容。后续若修改产品或安装器，须重新构建、核验资源并更新本节，不能继续引用旧包作为新实现证据。

## D5a-prep 版本与材料核对

本卡基线为 `c723ed1f6b52a1ab5dfea1b40cb193211be0e769`。仅核对已有 D4a 产物，不重新构建、不执行安装器；核验脚本和日志在 `build/d5a-prep-evidence`，不进入产品。

| 对象 | 当前值及证据范围 |
| --- | --- |
| Python 项目、前端 package.json、package-lock.json 顶层及根包 | 均为 0.1.0 |
| packaging/windows.iss AppVersion、安装包文件名 | 0.1.0 |
| 最终 D4a 安装包 PE ProductVersion | 0.1.0；不等同于 onedir EXE 自身带有版本资源 |
| 安装包大小 | 257649971 字节；本文件上方记录 SHA256 |
| onedir 清单 | 762 项，总计 117901395 字节；逐项大小/SHA256 与实际文件核对 |
| 许可材料 | 项目 LICENSE、THIRD_PARTY_LICENSES.md、THIRD_PARTY_NOTICES.md、licenses 下原始许可文本；不以项目 MIT 替代第三方许可 |

本卡修订的许可说明与 D4a 包内旧版说明**不是同一份字节内容**。旧包内 `THIRD_PARTY_NOTICES.md` SHA256 为 `FCBD6CFDBE4E6EECD46158F091681CE8F0BD545E788E90725457180C34C3164D`；本卡只澄清范围，原始许可证代码块不变。最终发行前须重新构建或整理对应发行材料、更新清单和摘要，再完成必要验收；不能将旧摘要标作已包含新说明的最终包摘要。当前版本号一致不意味着最终代码、文档与发行内容已同步放行。

项目 MIT、前端许可证原文和随包 proxy_tools 固定正文分别核对；proxy_tools SHA256 应为 `A428FB8A2E762AF3EB0A6EDBBB88E9B42CCFEE80FD9B423958BCACF9B9ABBFE4`。Python/Inno Setup 和原生依赖许可按既有 762 项清单验证文件完整性；本卡不重新进行上游法律条款审查或下载依赖。

```powershell
powershell.exe -NoProfile -File build/d5a-prep-evidence/verify_materials.ps1
git diff --check
git status --short
Get-FileHash README.md,docs/DEV_PLAN.md,docs/THIRD_PARTY_NOTICES.md,docs/WINDOWS_DESKTOP_ACCEPTANCE.md -Algorithm SHA256
```

本卡核验结果见同目录 `materials.log` 与 `materials.json`；独立验收待交接后决定。D4b 最近开发者后端日志为 1426 passed, 1 skipped；独立验收实跑 18 项合成路径、5 项前置检查与 focused 4 passed。它们是继承证据，本卡不重复报告为新执行结果。文档修改不重跑 GUI、业务回归或构建。

D5a-prep 开发者本轮实际核验：版本项及安装包 ProductVersion 均为 0.1.0；762 个资源文件大小及 SHA256 零差异，包含 `licenses` 下 129 个文件；25 个本地文件链接目标存在；原始许可代码块不变。命令退出 0。核验工具最初两次因 PowerShell 传递代码的编码/反引号处理失败，修正工具后通过，失败日志保留为 `materials-attempt1.log`、`materials-attempt2.log`。这些结果不代表独立验收或最终交付通过。

## PR 标题／正文草稿（仅本地材料，未创建）

拟议目标分支：`main`；来源：`codex/windows-desktop`。以下是当前候选状态的草稿，发布前必须根据最终差异与补充验收重写状态和产物信息。

**标题：** feat(desktop): add Windows desktop candidate and installation verification tooling

**正文：**

PaperLens 原先需要用户准备 Python/Node 并在浏览器中运行。本分支保留 React/FastAPI，增加 pywebview 原生窗口、PyInstaller 冻结构建和每用户 Windows 安装器，目标是双击进入 PDF 阅读、解读、证据、审计、修订、回退和导出工作台。

桌面模式将资源与用户数据分离，加入单实例及退出处理、回环接口保护、最近项目恢复，以及 Windows 系统凭据存储和模型设置界面。基础包使用文本 PDF 解析；离线验收入口强制 Mock 与临时数据隔离，不读取正式配置。提供安装资源清单、许可材料和已独立审查的安装验收脚本。

验证状态：D4a 安装器生成与资源完整性独立 PASS；D4b 脚本及链接拒绝修复独立 PASS，18 项合成路径测试中的 12 个链接场景均在启动前拒绝，启动计数为 0。开发者后端最近结果 1426 passed, 1 skipped；跳过真实 MinerU。D5a-prep 材料独立验收仍 PENDING；本卡的只读材料核对不等于安装测试。

合并与最终交付仍受以下门槛阻断：实际安装、不同版本升级、卸载/重装数据保留、安装后完整原生工作流、WebView2 安装分支及无 Python/Node 的干净 Win10/Win11 验证均 PENDING。当前 D4a 包尚未包含最新文档说明，最终产物及摘要须同步。真实 Hy3/MinerU 未验证，Mock 不代表模型效果；候选安装包未做发布者代码签名。

本分支不包含自动更新、账号、付费系统、跨平台或内置 MinerU 模型，不变更历史 Prompt、业务 Schema、评分及实验结果。当前仅准备本地 PR 材料，没有推送、创建 PR、合并 main 或公开 Release；最终操作等待明确放行。

## 脚本契约与审查范围

`tools/verify_windows_install.ps1` 使用系统 PowerShell，不需要 Python/Node。输入安装包路径、预期 SHA256、清单路径；可选旧安装包及其 SHA256。`Stage` 为 Install、Upgrade、Reinstall、Uninstall，缺省 Install。默认仅检查输入，不查询用户数据、不执行安装。`-Execute -TestUserSid <SID>` 才执行；SID 必须与当前 Windows 用户一致。

**SID 校验只是显式指定运行账户，不能证明它是测试账户。** 操作者须先准备专用测试用户/VM，确认该账户没有既有 PaperLens 凭据、私有材料或正式安装；不要把当前日常账户 SID 填入命令。系统已有机器级 PaperLens 安装时也不应使用该环境。首次执行另检查本账户安装目录、数据根、验收目录、卸载注册项及两个快捷方式均不存在；任何一项存在即拒绝，不能通过删除现有内容来绕过。

脚本按以下顺序工作：

1. 核对安装包 SHA256、清单相对路径与摘要格式；未知参数由 PowerShell 拒绝。缺少旧包的 Upgrade 输出 `NOT_RUN` 并退出 0，不能当作升级通过。
2. 实际执行前验证指定 SID、无 PaperLens 进程及受控目录无链接；Install 创建 `%LOCALAPPDATA%\PaperLens-InstallAcceptance` 归属记录与 `%LOCALAPPDATA%\PaperLens\acceptance-synthetic.txt` 随机合成标记，不访问凭据。
3. 后续阶段只接受本次归属记录。Upgrade 需要已安装状态及与记录一致的旧包摘要，新旧包摘要必须不同；**不同摘要不证明不同产品版本**，升级前还须独立确认版本差异。同版本安装只能计为 Reinstall。
4. 启动安装器前检查安装目标、祖先及已有子目录是否含链接；目标不存在时逐级寻找已有祖先检查，只忽略路径不存在，不忽略访问错误。含链接返回 `INSTALL_LINK_REFUSED`，不会调用 `Start-Process`。随后隐藏启动安装器/卸载器，等待返回；不强杀进程，不自动重启。任何非零码均失败并保留日志。卸载前核对本次安装的卸载器摘要；安装后仍保留资源检查前的链接复查。
5. 安装成功后逐项核对清单文件大小和 SHA256、注册项及两个快捷方式目标。卸载后检查主程序、注册项及快捷方式消失。阶段前后比较本次数据根全部文件摘要；保留数据及证据目录，脚本不执行删除命令。

输出证据位于测试账户 `%LOCALAPPDATA%\PaperLens-InstallAcceptance`，每阶段独立 GUID 日志及 JSON。成功 JSON 仅说明该阶段的进程、资源/快捷方式或卸载检查和合成文件保留；GUI 永远标记 `NOT_RUN`。失败保留现场；不自动清理、重试或宣告回滚成功。部分安装失败后的恢复须先检查日志与现场。

拒绝码包括 `INSTALL_INPUT_INVALID`、`INSTALL_HASH_MISMATCH`、`INSTALL_MANIFEST_INVALID`、`INSTALL_TEST_USER_REQUIRED`、`INSTALL_LINK_REFUSED`、`INSTALL_APP_RUNNING`、`INSTALL_EXISTING_STATE_REFUSED`、`INSTALL_OWNERSHIP_REQUIRED`、`INSTALL_PREVIOUS_VERSION_MISMATCH`、`INSTALL_UNINSTALLER_MISMATCH`。执行或检查失败包括 `INSTALL_PROCESS_FAILED:<code>`、`INSTALL_DATA_CHANGED`、`INSTALL_RESOURCE_MISMATCH`、`INSTALL_REGISTRATION_MISSING`、`INSTALL_SHORTCUT_INVALID`、`INSTALL_UNINSTALL_INCOMPLETE`，退出 1。系统文件访问/JSON 解析错误也退出 1 并保留原始错误；不改变桌面或业务错误码。

## 审查前可运行的验证

仓库根目录：

```powershell
.\.venv-desktop\Scripts\python.exe -B tools/desktop_verify.py --suite focused --tests backend/tests/test_desktop_package.py
.\.venv-desktop\Scripts\python.exe -B tools/desktop_verify.py --suite backend
powershell.exe -NoProfile -File build/d4b-evidence/test_preflight.ps1
.\.venv-desktop\Scripts\python.exe -B backend/tests/test_desktop_package.py --write-link-probe build/d4b-evidence/test_links.ps1
powershell.exe -NoProfile -File build/d4b-evidence/test_links.ps1
git diff --check
git status --short
```

`test_preflight.ps1` 为本次保留的开发证据，使用新建合成 EXE 文本占位文件，只执行前置检查，绝不运行该文件。覆盖缺省只检查、旧包摘要错误、清单越界、错误测试 SID 以及无旧包升级；不代表安装器执行成功。该脚本、红绿日志和交接单位于 `build/d4b-evidence`，不进入产品或安装包。

D4b 脚本首轮独立审查发现 P2：安装目录检查晚于安装器启动。最小返修已由独立验收复核 PASS，原 P2 已关闭。`test_links.ps1` 从仓库测试文件导出，并解析实际脚本的 `NoLinks` 函数及安装/卸载启动分支；只执行该分支，以计数替身覆盖 `Start-Process`，不运行账户初始化、归属记录或真实安装器。Install/Reinstall/Upgrade 各覆盖目标 junction、父目录 junction、较远祖先 junction、子目录 junction，以及普通已有/缺失目录，共 18 项。红测中 12 项链接场景启动计数为 1；绿测均拒绝并计数为 0，6 项普通目录计数为 1。测试使用真实合成 junction，路径与证据留在 `build/d4b-evidence/links-*`，无删除操作。这证明启动分支的检查时序，不替代完整专用账户安装，也不声称能抵御检查后并发替换目录。

## 独立审查通过且测试环境就绪后

把安装包、对应清单和验收脚本复制到专用测试 Windows 账户，先核对拷贝 SHA256。以下占位值必须替换为实际路径及该测试用户 SID；不在日常账户执行。

```powershell
# 缺省只检查，不能作为安装证据。
powershell.exe -NoProfile -File tools/verify_windows_install.ps1 -InstallerPath '<包路径>' -InstallerSHA256 '<已验收摘要>' -ManifestPath '<清单路径>'
# 专用测试账户执行首次安装。
powershell.exe -NoProfile -File tools/verify_windows_install.ps1 -InstallerPath '<包路径>' -InstallerSHA256 '<已验收摘要>' -ManifestPath '<清单路径>' -Stage Install -Execute -TestUserSid '<测试账户SID>'
# 同一安装包重装；GUI 关闭后再执行。
powershell.exe -NoProfile -File tools/verify_windows_install.ps1 -InstallerPath '<包路径>' -InstallerSHA256 '<已验收摘要>' -ManifestPath '<清单路径>' -Stage Reinstall -Execute -TestUserSid '<测试账户SID>'
# 卸载，保留数据。
powershell.exe -NoProfile -File tools/verify_windows_install.ps1 -InstallerPath '<包路径>' -InstallerSHA256 '<已验收摘要>' -ManifestPath '<清单路径>' -Stage Uninstall -Execute -TestUserSid '<测试账户SID>'
```

卸载后可用 Reinstall 再安装。真正升级须先安装已核验旧版本，再以新包及对应清单执行 `-Stage Upgrade -PreviousInstallerPath '<旧包>' -PreviousInstallerSHA256 '<旧包摘要>'` 并带上述执行参数。目前没有已验收的旧版本安装包，升级 **NOT_RUN**；旧 portable EXE 和同版本重编译包不算旧版本安装包。

## 仍须取得的实际证据（验收均 PENDING）

| 检查 | 当前状态 | 验证要求 |
| --- | --- | --- |
| 首次安装、快捷方式、卸载注册 | NOT_RUN | 专用账户实跑并检查实际可见快捷方式 |
| 安装后资源与合成文件保留 | NOT_RUN | 脚本逐文件检查；合成标记不替代数据库/PDF |
| 原生 PDF、Mock、证据、审计、修订、回退、导出 | NOT_RUN | 安装后的 EXE，合成文本 PDF，限定窗口操作；保存及取消分别留证 |
| 项目重启、升级与卸载后重装保留 | NOT_RUN | 专用测试账户创建的合成项目/状态，关闭重开，记录项目及数据摘要；需不同版本验证升级 |
| 设置及凭据 | NOT_RUN | 仅该隔离账户本次合成凭据；不迁移或读取日常账户凭据 |
| 取消安装与失败路径 | NOT_RUN | 正常可见安装向导取消；运行中拒绝安装/卸载；保留日志与现场，不用全局 SendKeys |
| WebView2 缺失、安装失败与需重启 | NOT_RUN | 测试 VM 快照或受控环境；不卸载宿主机共享运行时制造缺失 |
| 退出及 WebView2 子进程、跨登录会话互斥 | NOT_RUN | 指定 PID/路径枚举，正常关闭后核对；不把旧 EXE 结果继承给新包 |
| 干净 Win10 22H2 / Win11 x64 | NOT_RUN | 无 Python/Node 目标环境逐项验证，D5 保留门槛 |
| 真实 Hy3 / MinerU | NOT_RUN | 不在本次授权内；Mock 不代表真实模型效果 |

`--offline-test` 每次使用新的临时根，可验证安装后 EXE 的离线 UI，但不能用它证明同一项目的跨启动持久化。真实持久化须在专用测试账户/VM 验证，且不允许在日常账户通过正常入口探测现有凭据。
