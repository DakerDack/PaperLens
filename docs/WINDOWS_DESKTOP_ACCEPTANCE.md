# Windows 桌面安装验收

## 当前状态

- D4a 已独立 PASS，提交 `64f2c41466fa48a15d1f2b690e54a8634123c43e`。
- D4b 当前仅准备验收脚本，**安装脚本独立审查 PENDING，实际安装验收 NOT_RUN**。
- 用户确认尚无隔离测试环境，先完成脚本审查。依据 WINDOWS_DESKTOP_PLAN.md 第 9 节，脚本独立审查通过后才能执行安装操作。
- 未安装、升级、卸载 PaperLens；未执行 WebView2 安装器。开发 Windows 环境不能替代没有 Python/Node 的干净机器。

## 已冻结的 D4a 输入

- 安装包：`D:\PaperLens\dist\installer\71079916bc464a17abea8d944e4eb947\PaperLens-0.1.0-windows-x64-setup.exe`
- 安装包 SHA256：`E46F2E79DE87F31029690C4316EA9FC4A0C544AC1C28D4276A1EE67266C2573D`
- 清单：`D:\PaperLens\dist\Release-59dc8aced9e24ac493bcd08c5a60e9bb\PaperLens-manifest.json`，762 项。
- 安装器版本 0.1.0；修改验收脚本不改变该包内容。后续若修改产品或安装器，须重新构建、核验资源并更新本节，不能继续引用旧包作为新实现证据。

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

D4b 脚本首轮独立审查发现 P2：安装目录检查晚于安装器启动。当前已做最小返修，等待复核。`test_links.ps1` 从仓库测试文件导出，并解析实际脚本的 `NoLinks` 函数及安装/卸载启动分支；只执行该分支，以计数替身覆盖 `Start-Process`，不运行账户初始化、归属记录或真实安装器。Install/Reinstall/Upgrade 各覆盖目标 junction、父目录 junction、较远祖先 junction、子目录 junction，以及普通已有/缺失目录，共 18 项。红测中 12 项链接场景启动计数为 1；绿测均拒绝并计数为 0，6 项普通目录计数为 1。测试使用真实合成 junction，路径与证据留在 `build/d4b-evidence/links-*`，无删除操作。这证明启动分支的检查时序，不替代完整专用账户安装，也不声称能抵御检查后并发替换目录。

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

## 仍须取得的实际证据

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
