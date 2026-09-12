# PaperLens Windows 桌面实施方案（D0）

日期：2026-09-12。状态：待独立审查，不是已验证的桌面能力。

## 1. 目标、基线与范围

交付 Windows 10/11 x64 可安装应用。终端用户不安装 Python/Node，不开终端；双击快捷方式进入独立窗口。保留 PDF、解读、证据、审计、修订、回退、导出，用户数据重启和升级保留；用户在界面输入自己的 Hy3 Key。基础包只用文本 PDF 解析。无自动更新、账号、付费系统、跨平台、内置 MinerU、共享 Key 或模型研究。

开工 HEAD：`92b1ed6f64b6e88a5cb5cf95d429d143a54d1fa3`，原分支 `codex/postrelease-b-direction-precondition`；工作区干净。已从此基线创建 `codex/windows-desktop`，origin 为 `https://github.com/DakerDack/PaperLens.git`。没有 fetch/push，没有读取 `.env`、既有凭据、数据目录或私有论文。D0 只改三份文档。

本机只读检测：Python 3.13.3 x64；FastAPI 0.141.1、pydantic-settings 2.15.0、Uvicorn 0.52.4、pdfplumber 0.11.10；pywebview/PyInstaller/pythonnet 未装在该解释器。系统 build 22631、DisplayVersion 23H2（Windows 11；注册表 ProductName 仍显示 Windows 10，不据此误判）；WebView2 目录版本 152.0.4191.66。ISCC 不在 PATH，不能据此认定整机未安装。不是干净机器证据。

## 2. 现状核查

| 位置 | 当前行为 | 桌面需处理 |
|---|---|---|
| frontend/src/api.ts | VITE_API_BASE_URL，缺省 127.0.0.1:8000；PDF/导出也统一 fetch | 桌面构建显式空 base，同源动态端口；浏览器开发兼容 |
| frontend/src/components/PdfPane.tsx | worker 通过 Vite `?url`；getDocument 未配置 cMaps/fonts/wasm | worker 与辅助资源随包，不依赖 CDN，实测中文及字体 |
| frontend/src/App.tsx | 项目在 React 内存；Markdown 使用 blob 下载 | 重启重新打开最近项目；验证 WebView2 保存对话框和下载取消 |
| backend/app/main.py | create_app 支持注入服务；模块末尾立即 create_app；仅 Vite 开发 CORS | 桌面显式注入配置、专用安全包装与静态文件，禁用开发 CORS/docs |
| backend/app/settings.py | 模块末尾 Settings()，默认读取 cwd .env；数据相对 cwd | 在任何业务模块导入前实现可靠桌面隔离；禁止只对后续 Settings 传 _env_file=None |
| backend/app/document_service.py | parse 先尝试 MinerU，只有不可用才 pdfplumber | 显式 text_only 入口，绝不探测或启动 MinerU；复用 parse_with_pdfplumber |
| backend/app/project_store.py | 按注入路径保存 SQLite/PDF；唯一 SQL 所有者 | 原六表和布局保留；数据根来自当前用户目录 |
| frontend/playwright.config.ts | Vite 4173，浏览器 Chromium，既有预置接口 | 仅为 UI 回归，不能替代冻结 EXE、真实后端或安装实测 |

## 3. 运行、资源和生命周期

优先路线：pywebview 强制 `edgechromium`，PyInstaller `onedir + windowed`。同一进程主线程运行 GUI，后台线程运行现有 Uvicorn/FastAPI；不加 Electron、第二个 HTTP 框架或后台常驻服务。冻包包含 Python 和现有运行依赖，不包含 Node、Vite server、测试、评测材料或 MinerU。此路线只是设计选择；D1 任一核心兼容项失败即返修本阶段，禁止以浏览器窗口冒充桌面窗口。

启动顺序：解析桌面参数 → 获得当前用户单实例锁 → 定位只读资源/可写数据 → 构造无 env 来源的桌面 Settings → 初始化文本解析和 store → 预绑定 `127.0.0.1:0` socket → 将同一 socket 交 Uvicorn（不先探测空闲端口再重新绑定）→ 等待服务就绪 → 创建/显示窗口。

就绪上限 30 秒；检查线程存活、Uvicorn started 和带桌面 token 的现有 `/api/health`，确认版本/模式匹配后进入工作台。启动失败用中文原生提示展示稳定错误码，不留白窗，不输出异常中的用户路径或请求内容。8000 被占用不影响桌面启动。只有回环监听，无防火墙开放、无 0.0.0.0。

单实例采用 Win32 当前用户作用域命名互斥体（含用户 SID）；重复启动提示“已运行”并退出，不启动第二个数据库写入实例，不用保存 PID 的文件判断存活。跨 Windows 登录会话的同用户实例也需互斥；验证 Global 命名空间权限/ACL，不假定 Local 命名空间覆盖所有会话。崩溃由系统释放锁，重启不删除数据库。

关闭窗口时停止接收新业务请求，有进行中的操作则提示用户等待完成；不承诺强杀后模型调用可撤销。正常关闭设置 should_exit、有限等待服务收尾并释放 socket/互斥体；卡住时展示退出失败并允许重试，不静默遗留服务。强制结束进程后验证 SQLite 已提交版本可读、未提交事务回滚。无独立 Python 服务子进程；WebView2 子进程随窗口释放也须观察。

资源只从模块位置/冻包资源目录解析，禁止依赖 cwd；使用路径规范化与白名单静态目录，资源路径不能指向用户数据目录。静态挂载在 API 之后，未知 `/api/*` 返回既有错误/404，不回落 index.html。基础页使用 `/`，无需新前端路由框架。

PDF.js worker、cMaps、standard_fonts、wasm 从已锁定 pdfjs-dist 收集到 `frontend/dist` 并写入产物清单；PdfPane 显式指定对应同源地址。验证正确 MIME、离线无外域请求、worker 真执行、非空 canvas、中文文本与页码、证据定位和字体降级。不得只检查文件存在。

桌面导出先验证 pywebview `ALLOW_DOWNLOADS` 开启后的现有 blob 保存路径；下载功能默认关闭，必须实际启用并验证。若当前版本 blob 下载不可靠，在 D1e 内改为受限原生保存对话框：仅保存现有导出 API 产生的 Markdown，取消不报成功，不增加任意路径读写桥。超出白名单则先拆卡审查。

## 4. 数据与重新打开

安装目录：`%LOCALAPPDATA%\Programs\PaperLens`；用户数据根：`%LOCALAPPDATA%\PaperLens`（Win32 Known Folder API 获取，不能只信任可污染的环境变量）。数据在 `data\paperlens.db`、`data\projects\<id>\source.pdf`，保持现有相对布局。桌面非敏感状态 `desktop-state.json` 只存最近项目 ID 和显式 mock/live 模式；WebView 存储 `webview`，脱敏诊断 `logs`。路径、图标、静态资源都不写安装目录；测试数据用独立临时根。

不迁移/读取 D:\Hy3 或仓库已有 data；无新数据库字段/表。最近项目 ID 由桌面原生受限桥持久化，启动时经现有 getProject/getProjectPdf 恢复；端口改变不依赖 localStorage 的 origin。无效/丢失 ID 展示恢复失败，不覆盖数据库。提供输入已有项目 ID 的“打开项目”入口，项目 ID 可复制；首版不扩大为项目库/搜索新功能。状态原子替换，损坏状态文件不影响存量 DB。

升级保持 AppId、数据目录、凭据 TargetName 不变；不重新初始化已有业务表或复制空数据库覆盖。卸载只移除安装文件/快捷方式/卸载登记，保留数据、设置、凭据及系统共享 WebView2；卸载说明明确这一点，本任务不提供删除用户数据操作。不实现业务 schema 迁移，出现必要迁移时停止并报告。

## 5. Settings 与凭据

桌面独立配置源只使用显式常量、非敏感桌面状态和本应用专属 Windows Credential Manager Generic Credential，默认 live 且无 Key。界面可明确选用 MOCK；缺 Key 沿用 `HY3_CONFIG_MISSING`，供应商失败保留 `HY3_UNAVAILABLE` 等既有错误，不回退 Mock。base URL/model 固定为现有默认值，首版仅配置 Key 和模式，不开放任意供应商地址。

必须先修模块级 Settings 的加载边界：桌面启动设置入口标志，所有默认 Settings 创建在该模式下禁用 dotenv 和环境来源（包括继承的 HY3_API_KEY、代理/调试影响需显式评估）；桌面实例使用显式输入。原浏览器启动配置语义保留。测试须证明导入 main/document_service/hy3_service 不读取真实 .env、不初始化仓库数据、不启动其他服务。不能用更换 cwd 掩盖问题。

凭据使用标准库 ctypes 调用 CredReadW/CredWriteW/CredDeleteW/CredFree，目标 `PaperLens/Hy3`，只访问该目标，不枚举用户凭据。测试仅使用单独 `PaperLens.Test.<随机值>/Hy3` 合成凭据，禁止探测生产目标是否已有 Key。原生集成测试仅清理本次自行创建的测试条目，不接触用户条目。

界面密码框写入，保存后清空；状态只返回 configured 布尔值，绝不返回 Key/前后缀。不写 `.env`、JSON、SQLite、日志、URL、命令行、截图或包。保存失败保持旧凭据和旧有效配置；更新先持久化成功，然后提示重启生效，避免正在运行的 Hy3Service 与 AuditService 混用新旧配置。下次启动明确显示当前模式。存储不可用不退回明文；不提供“试调用”按钮，保存不触发网络验证。

桌面控制走 pywebview 受限桥，不新增业务 REST 路由。跨模块模型集中在 models.py，TypeScript 对应定义在 types.ts；这是获授权的桌面控制模型，不能修改任何现有业务或模型输出 Schema。

计划冻结的桥方法：`get_desktop_settings()` → `{mode, key_configured}`；`save_desktop_settings({mode, api_key?})` → `{restart_required:true}`（省略 Key 表示保留，空串非法，不隐式删除）；`clear_desktop_key()`（无参数）→ `{key_configured:false, restart_required:true}`；`get_recent_project()` → `{project_id:null|string}`；`set_recent_project({project_id})` → `{saved:true}`。所有结果为 `{ok:true,value:...}` 或 `{ok:false,error:{error_code,message,retryable}}`，禁止序列化异常原文。项目 ID 按既有 API 合法 ID 验证。无任意文件、SQL、eval、shell 或 Key 读取桥。

清除由设置界面独立的“清除 Key”操作显式触发，不通过空密码框或保存操作隐式触发。仅对本应用固定凭据目标执行 CredDeleteW；调用者不能传入目标名。删除成功或返回 ERROR_NOT_FOUND（凭据本就不存在，包括重复清除）均返回上述成功值；其他删除失败返回 `{ok:false,error:{error_code:"DESKTOP_CREDENTIAL_UNAVAILABLE",message:"清除 Key 失败，请重试。",retryable:true}}`，不得显示清除成功或将状态强制设为未配置，不改当前有效配置。传入非约定参数用 `DESKTOP_SETTINGS_INVALID`，不执行删除。状态查询的 `key_configured` 仅表示已持久化凭据是否存在，只返回布尔值，不返回 Key 或片段；删除失败后若状态查询也失败，应显示错误/未知，不伪装成 false。

清除与保存统一在重启后影响服务配置：成功立即删除持久化凭据并显示“已清除保存的 Key，重启后生效；当前运行实例仍可能使用已加载的 Key”，当前 Settings/Hy3Service/AuditService 内存配置及进行中的请求保持不变。即使凭据原本不存在也返回 `restart_required:true`，避免遗漏当前实例已加载的 Key；此标志不代表本次确实删掉了条目。下次启动保持原模式且不加载 Key，live 操作按既有契约返回 `HY3_CONFIG_MISSING`，不自动切换 Mock。清除本身不触发任何网络调用，不删除论文、数据库、模式/项目状态或其他用户凭据。开发和验收仅在本次专用 `PaperLens.Test.<随机值>/Hy3` 合成目标验证所有清除路径，不读取或操作真实凭据。

桌面新增错误码限定控制层：`DESKTOP_RUNTIME_MISSING`、`DESKTOP_RESOURCE_MISSING`、`DESKTOP_START_FAILED`、`DESKTOP_START_TIMEOUT`、`DESKTOP_ALREADY_RUNNING`、`DESKTOP_DATA_UNAVAILABLE`、`DESKTOP_EXIT_TIMEOUT`、`DESKTOP_ACCESS_DENIED`、`DESKTOP_SETTINGS_INVALID`、`DESKTOP_CREDENTIAL_UNAVAILABLE`、`DESKTOP_STATE_INVALID`。启动错误原生展示；访问拒绝 HTTP 403 用原 ErrorResponse 结构；桥错误用上述信封。既有 PDF/业务错误码不变。

## 6. 本机接口保护

威胁范围：阻止其他网页及无授权本地 HTTP 客户端访问论文/触发付费操作；不宣称能抵抗同用户恶意进程读取进程内存或系统凭据。

- 使用 pywebview 每会话 token，经原生注入的 `window.pywebview.token` 获取，API 请求头 `X-PaperLens-Token`；只在内存中。前端等待 pywebviewready 后才发首个请求，后端恒定时间比较。
- 保护所有 `/api/*`，包括 health、PDF、导出及不存在的 API；启动自检持有同一 token。普通 HTTP 下载页面不能获得 token，HTML/JS/URL 中不嵌入它，禁用 docs/openapi/redoc 和访问日志。
- Host 必须精确等于本实例 127.0.0.1:port；有 Origin 时必须同源；拒绝外域/null Origin、恶意 Host 和预检。不以 CORS 代替认证，不接受 URL query token。
- 静态资源不含用户内容，允许无 token 加载，但严禁路径穿越/符号链接逃逸、列目录和访问安装目录其他文件。响应设置禁止 framing 与严格 referrer 策略。
- 窗口仅导航至本实例可信页面；拦截外域导航、新窗口、file://，禁用开发工具/远程调试。不在不可信页面启用原生桥。D1 必须查所锁版本的真实导航拦截能力，若无法可靠限制则返回方案返修，不忽略风险。
- 桥调用也检查当前可信窗口 URL；桥从不提供 token/Key 的读取方法。令牌不能写日志或持久化。测试覆盖无 token、错 token、跨站表单/JSON、读 PDF/导出、DNS rebinding Host、导航限制和重启旧 token。

采用回环 HTTP，不把 pywebview 内置 Bottle 的 ssl 开关误用于外置 FastAPI；以上认证与导航保护是正式应用的前置条件。D1 只用合成 PDF/显式 Mock，D2 完成前禁止装入真实用户资料或 Key。

## 7. 依赖、打包与安装审查

| 项目 | 必要性/使用位置 | 许可和移除路径 |
|---|---|---|
| pywebview | 标准库/React 不能提供所需 Windows WebView 容器；backend/app/desktop.py | BSD-3-Clause；保留声明；移除只能退回浏览器，不能算完成桌面目标 |
| pythonnet 及 pywebview Windows 传递依赖 | WebView2/.NET 桥；由锁文件及构建 spec 收集 | 安装后逐项读取实际发行包许可；未审查项阻断分发，不猜测全树已兼容 |
| PyInstaller | 包含解释器并生成 windowed onedir；desktop.spec/build 脚本 | GPL 带分发例外，产物可采用本项目许可；保留依赖声明；移除将无法交付无 Python 目标，返回打包方案审查 |
| WebView2 Evergreen x64 runtime | Chromium 引擎，系统共享运行时 | Microsoft 再分发条款；官方离线安装器签名核验；不退回 IE/mshtml |
| Inno Setup（构建工具） | Python/PyInstaller 不提供完整 Windows 安装升级卸载；packaging/windows.iss | 官方许可允许商业/非商业使用和分发并要求保留声明；移除时 portable 包只可作诊断，不满足安装目标 |
| 凭据、路径、互斥体 | ctypes 调用 Windows API | 不增加 keyring/pywin32/platformdirs |

版本在隔离 `.venv-desktop` 安装兼容冒烟后锁定至 `requirements-desktop.lock`，不改旧研究环境和 requirements.lock。以现有 Python 3.13 x64 优先验证；若 pythonnet/冻包不兼容，只在独立环境尝试受支持 Python 3.12 x64 并重新验收，不降级现有解释器，不无声改路线。记录直接/传递依赖、哈希、许可、原生 DLL 和 runtime 信息。显式收集 pdfplumber distribution metadata、pydantic_core、pythonnet/CLR loader、WebView2 DLL、前端所有资源，禁止整仓库递归 add-data。

安装用稳定 AppId、每用户模式、x64 onedir、开始菜单与可选桌面快捷方式。WebView2 已有则复用，缺少则运行包内微软签名的 Evergreen 离线安装器；安装/重启要求和失败退出码须处理，失败不得显示安装成功。不要求用户终端安装依赖。检测所需 .NET Framework 版本；Win10/11 基线限定安装器实测支持版本（至少计划 Win10 22H2 与 Win11），不足明确失败，不暗示支持所有历史 build。

升级检查本应用运行状态并要求正常退出，保留数据；不杀所有 Python/WebView2 进程。验证覆盖旧版到新版、取消安装、失败安装、重装、卸载、卸载后重装。包内使用 LICENSE/THIRD_PARTY_NOTICES，生成文件清单和 SHA256；检查无 .env、Key、论文、DB、日志和本机私有路径。现阶段不具备签名证书，签名状态如实报告；不伪造受信任发布者。

## 8. 拆卡（顺序执行，每卡最多四文件）

下列是 D0 待审查范围，不提前建占位文件。每卡仍须在实际修改前声明具体目标、契约和命令；发现额外文件需要先细分，不跨卡顺手修改。新增目录仅 packaging、tools；其他新文件在现有平面目录。忽略的隔离环境/构建产物不提交，但仍登记；所有源码/配置修改算文件数。

| 卡 | 单一可观察结果 | 最多四个允许文件 | focused 验证 |
|---|---|---|---|
| D0 | 方案可供独立审查 | DEV_PLAN、本方案、D0_HANDOFF（均 docs） | 本卡文档检查 |
| D1a | 桌面导入不读 env，离线验证可安全执行 | backend/app/settings.py；backend/tests/test_settings.py；tools/desktop_verify.py；backend/tests/test_desktop_verify.py | F settings + desktop_verify |
| D1b | 文本入口绝不运行 MinerU | backend/app/document_service.py；backend/tests/test_document_service.py | F document_service |
| D1c | 隔离环境可装入经审查的桌面依赖 | pyproject.toml；requirements-desktop.lock；.gitignore；docs/WINDOWS_DESKTOP_PLAN.md | 依赖安装、pip check、版本/许可记录 |
| D1d | 双击原型打开现有工作台并正常退出 | backend/app/desktop.py；backend/tests/test_desktop.py；frontend/src/api.ts；frontend/src/api.test.ts | F desktop + UI API；原生窗口观察 |
| D1e | 冻结原型可离线上传、渲染、导出 | desktop.spec；frontend/src/components/PdfPane.tsx；frontend/src/components/PdfPane.test.tsx；tools/build_desktop.ps1 | F desktop；UI PDF；B；原生 PDF/导出实测 |
| D2a | 数据与资源分离，进程锁和退出可重复验证 | backend/app/desktop.py；backend/tests/test_desktop.py；backend/app/main.py；backend/tests/test_api.py | F desktop + api；并发启动/异常退出 |
| D2b | 未授权本机访问与外域导航被拒绝 | backend/app/desktop.py；backend/tests/test_desktop.py；frontend/src/api.ts；frontend/src/api.test.ts | F desktop；UI API；原生导航攻击检查 |
| D2c | 最近项目状态跨端口/重启保留 | backend/app/desktop.py；backend/tests/test_desktop.py；backend/app/models.py；backend/tests/test_models.py | F desktop + models |
| D2d | UI 重新打开最近/指定项目 | frontend/src/App.tsx；frontend/src/App.test.tsx；frontend/src/api.ts；frontend/src/types.ts | UI；真实桌面重启恢复 |
| D3a | 合成 Key 可安全存取与显式清除，失败无明文回退 | backend/app/desktop_credentials.py；backend/tests/test_desktop_credentials.py；backend/app/models.py；backend/tests/test_models.py | F desktop_credentials + models；本次专用合成目标的清除成功、ERROR_NOT_FOUND/重复清除、受控删除失败及错误信封；不操作其他目标 |
| D3b | 设置/清除桥验证并接入下次启动 Settings | backend/app/desktop.py；backend/tests/test_desktop.py；frontend/src/api.ts；frontend/src/types.ts | F desktop；UI/API 类型构建；合成 Key 清除后持久化状态 false、当前实例配置不变、重启无 Key；删除失败保留状态/配置，查询失败不返回 false；无网络调用 |
| D3c | 用户可保存或显式清除 Key 并看到结果与重启提示 | frontend/src/components/DesktopSettings.tsx；frontend/src/components/DesktopSettings.test.tsx；frontend/src/App.tsx；frontend/src/styles.css | UI；原生设置流程仅用本次专用合成目标，验证清除入口、成功/失败反馈、重复清除、重启后未配置状态及 live 缺 Key 错误；不回显 Key |
| D4a | 可复现安装器生成且资源完整 | packaging/windows.iss；tools/build_desktop.ps1；backend/tests/test_desktop_package.py；docs/THIRD_PARTY_NOTICES.md | F desktop_package；B；ISCC |
| D4b | 实际安装升级卸载保留合成数据 | tools/verify_windows_install.ps1；backend/tests/test_desktop_package.py；packaging/windows.iss；docs/WINDOWS_DESKTOP_ACCEPTANCE.md | I；失败/取消/重装实测 |
| D5a | 产品/安装包/使用文档版本一致 | pyproject.toml；frontend/package.json；frontend/package-lock.json；README.md | 版本核对；R；B；I |
| D5b | 干净机器证据与交付可复核 | docs/WINDOWS_DESKTOP_ACCEPTANCE.md；docs/DEV_PLAN.md；docs/WINDOWS_DESKTOP_PLAN.md；docs/WINDOWS_DESKTOP_D0_HANDOFF.md | 干净 Win10/11 手工验收；R；最终哈希 |

D1e 如需改下载实现、额外 Vite 配置或 native 导航 hook，必须先拆分为独立子卡并交审，不在四文件内塞入无关实现。D2/D3 的模型不改既有 Schema；model-output schema hash 应对基线保持一致。

## 9. 验证命令与证据契约

以下 D1+ 命令是计划接口，D0 尚未创建脚本，不能报告为已执行。运行位置 `D:\PaperLens`。D1a 工具首先建立安全验证环境：禁止读取仓库真实 `.env`、旧数据/凭据、非回环网络、MinerU；允许临时目录内本次测试生成的 synthetic dotenv。环境只保留工具执行必需变量，不把整份环境输出到日志。该工具启动 pytest 前安装保护，保护失效必须非零退出；禁止先导入业务再安装保护。不能通过修改既有业务测试来回避保护错误。

F（示例按上表具体文件替换，交接须记录完整实际命令）：

```powershell
.\.venv313\Scripts\python.exe -B tools/desktop_verify.py --suite focused --tests backend/tests/test_settings.py backend/tests/test_desktop_verify.py
```

D1a 首轮红测在外置临时保护下运行新测试，确认旧代码失败，再实现；以后每张代码卡先 focused 红测，再最小实现，再 focused 绿测。依赖/纯文档卡不造镜像测试。

R（所有代码卡的阶段回归；UI 表示其中 frontend suite）：

```powershell
.\.venv313\Scripts\python.exe -B tools/desktop_verify.py --suite backend
.\.venv313\Scripts\python.exe -B tools/desktop_verify.py --suite frontend
git diff --check
git status --short
```

backend suite 实际执行 `python -B -m pytest backend/tests eval/test_eval.py -q --tb=short -p no:cacheprovider`。frontend suite 在 frontend cwd 依次执行 `npm.cmd run test -- --run`、`npm.cmd run build`、`.\node_modules\.bin\playwright.cmd test`；显式说明最后一步为既有预置接口。保护必须覆盖子进程，不将仅阻断 Python socket 当作浏览器外网隔离；浏览器/API 的离线限制在对应配置/进程启动层落实，必要时拆验证卡。D1c 起另以 `.venv-desktop\Scripts\python.exe` 跑桌面 focused 与全量后端，防止只测旧环境。

依赖卡候选安装命令（D0 PASS 后才能执行，版本来自真实解析/审查；首次不用不存在的锁）：

```powershell
.\.venv313\Scripts\python.exe -m venv .venv-desktop
.\.venv-desktop\Scripts\python.exe -m pip install -e ".[dev,desktop]" pyinstaller
.\.venv-desktop\Scripts\python.exe -m pip check
.\.venv-desktop\Scripts\python.exe -m pip freeze --exclude-editable
```

安装用网络只访问依赖发布源，不读取应用凭据；网络权限按执行环境要求申请。冻结后重建改为 `pip install -r requirements-desktop.lock`，不得随意升级。

B（脚本显式以空 VITE_API_BASE_URL 构建，设置/恢复进程环境，不读 dotenv；发布打包只接受白名单）：

```powershell
powershell.exe -NoProfile -File tools/build_desktop.ps1 -Configuration OfflineTest
.\dist\PaperLens\PaperLens.exe --offline-test
```

`--offline-test` 必须强制隔离临时数据、合成凭据目标、Mock、禁外网/禁 MinerU，不能读取用户配置；正常 EXE 缺省 live。该模式可用来验证真实冻包 UI/后端，但不代表真实模型效果。生成安装包用 `-Configuration Release`，不得嵌入测试数据；构建脚本调用已核验路径的 ISCC，产物统一 `dist\installer`，不与 frontend/dist 冲突。

I（D4b 脚本先完成且独立审查后使用；只在测试 Windows 用户/VM 运行）：

```powershell
powershell.exe -NoProfile -File tools/verify_windows_install.ps1 -InstallerPath <本次安装包绝对路径> -PreviousInstallerPath <上一测试版本绝对路径>
Get-FileHash <本次安装包绝对路径> -Algorithm SHA256
```

脚本只操作本次测试安装、合成数据与自己创建的凭据，不删除用户目录；无旧安装包时只能跑首次安装，升级项标记 NOT_RUN。GUI 是否真的出现、文件选择/保存是否成功、卸载是否真实完成不能由静态脚本代替。

所有卡证据含基线 HEAD、文件清单/diff、准确红绿/回归退出码与摘要、脱敏日志绝对路径、人工操作步骤及截图（仅合成内容）、独立验收结果、提交号。日志保存任务专用临时证据目录，交接前确认仍可读；失败日志保留。未完成项写 NOT_RUN/NOT_VERIFIED，不拿历史计数当当前结果。

## 10. 最终门槛与协作

D0 审查通过只是方案门槛；D1 必须完成原生窗口＋冻结兼容验证后才推进 D2。最终必须在无 Python/Node 的 Windows 10 x64 与 Windows 11 x64 干净目标环境实际安装：快捷方式启动、合成文本 PDF、离线 Mock 解读/证据/审计/修订/回退/导出、重启恢复、合成凭据保存/重启、升级保留、卸载保留/重装、关闭无残留服务。记录 OS build、runtime、安装器版本/哈希、操作证据和失败项；隐藏 PATH、Windows 开发机本地运行和 Playwright 都不能代替。

若本机无可用 Windows VM/Sandbox 或第二目标机器，到 D5 必须请用户提供目标测试环境/结果；不为避免阻塞伪造验收。不擅自启用系统虚拟化功能或下载 OS 镜像。真实 Hy3 验证本任务未授权，最终明确 `LIVE_MODEL_VALIDATION=NOT_RUN`，不能据此说完整真实供应商闭环已验证。

每卡开发完冻结工作区，将卡号/基线/差异/日志/验收步骤交独立验收会话。当前无通信工具，用户已选择手工转发，无第三会话；PASS 后开发提交并记录哈希再推进。FAIL 只返修当前卡。全部通过后推送 `codex/windows-desktop` 并创建指向 main 的 PR，交安装包绝对路径和 SHA256；不 merge、不公开 Release。安装包外部分发位置若尚未有可用授权存储，在最终阶段与用户确认；本地交付路径必须真实存在。

## 11. 官方依据（2026-09-12 查阅）

- [pywebview 安装](https://pywebview.flowrl.com/guide/installation.html) 和 [引擎选择](https://pywebview.flowrl.com/guide/web_engine.html)：Windows 使用 pythonnet/.NET 与 WebView2，需强制 EdgeChromium；实际最低 .NET 以所锁版本验证。
- [pywebview 冻结](https://pywebview.flowrl.com/guide/freezing.html)：支持 PyInstaller 与前端构建目录，避免无关 GUI 依赖进入包。
- [pywebview API](https://pywebview.flowrl.com/api/) 和 [安全说明](https://pywebview.flowrl.com/guide/security.html)：GUI 主线程、下载开关、原生桥及 session token；本项目仍须自行落实认证与导航限制。
- [PyInstaller 工作方式](https://pyinstaller.org/en/stable/operating-mode.html)：打包解释器，需对应 OS/架构构建；onedir 便于资源检查。
- [WebView2 分发](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)：Evergreen 在线/离线分发与检测，不假定所有目标系统已安装。
- [CredWriteW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew)、[CredReadW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credreadw)：当前登录用户目标凭据读写与返回内存释放。
- [pywebview 许可](https://github.com/r0x0r/pywebview/blob/master/LICENSE)、[PyInstaller 许可](https://pyinstaller.org/en/stable/license.html)、[Inno Setup](https://jrsoftware.org/isinfo.php) 及 [许可](https://jrsoftware.org/files/is/license.txt)：实际安装版本与传递依赖还须逐项归档。
