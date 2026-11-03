# Echoes (听记) 用户使用手册与人工验收指南

本文档为 Echoes 项目（Android 移动端与 Dedicated 后端）在完成 **M1 远端发布**、**M2 移动端独立运行 (Standalone AI)**、**M3 GitHub APK 更新机制** 及 **M4 综合交付** 后的完整用户使用手册与人工验收指南。提供端到端的功能特性说明、详细操作步骤、验证方法及自动化测试执行指令。

---

## 1. 交付概览与核心特性

Echoes 是一个结合本地持续录音、多模态大模型理解与长期记忆构建的智能听记系统。在最新版本中，系统实现了以下核心演进：

1. **零服务器依赖的端侧独立运行 (Standalone AI Engine)**：
   - 移动端直接连接 **Google AI Studio (Gemini 2.0 / 1.5)** 或 **GCP Vertex AI REST API**。
   - 采用 AndroidKeyStore 硬件级 AES-256-GCM 加密存储密钥。
   - 具备 16kHz PCM 30 秒分块、RMS 静音能量门限检测（$\text{RMS} < 0.003$ 自动跳过 API）、UTC 时间戳精确对齐，以及本地 SQLite Room 数据库响应式数据流。
2. **应用内 GitHub APK 自动检查与安全更新 (In-App Update Engine)**：
   - 在设置页集成「关于与应用更新」卡片，直连 GitHub Releases API (`CheemsaDoge/Echoes`)。
   - 具备 SemVer 语义化版本比对、更新日志预览弹窗。
   - 支持 OkHttp 流式下载与动态进度条、SHA-256 哈希校验，以及 Android 8.0+ `FileProvider` 安全安装。
3. **云端与专用服务端灵活协同 (Hybrid Mode & Dedicated Server)**：
   - 保留专用的 Python FastAPI 后端，提供 Model Context Protocol (MCP) 服务 (`/v1/mcp`) 供桌面 AI Agent（Claude Desktop / Cursor / Argon）调用，并支持大屏 Web 控制台管理。
4. **工业级 CI/CD 发布体系**：
   - 规范的 Gradle 8.10.2 Wrapper。
   - GitHub Actions 自动化发布流水线 (`release.yml`)，支持打 Tag (`v*`) 自动构建 Release APK、计算 SHA-256 校验和并发布 GitHub Release。

---

## 2. 远端仓库与发布流水线验收 (R1)

### 2.1 Git 远端配置验证
验证本地仓库正确关联 GitHub 官方仓库：

```bash
# 检查远端仓库配置
git remote -v
```

**预期输出**：
```text
origin  https://github.com/CheemsaDoge/Echoes (fetch)
origin  https://github.com/CheemsaDoge/Echoes (push)
```

### 2.2 GitHub Actions 自动化发布流水线 (`.github/workflows/release.yml`)
在 GitHub 仓库中，发布流水线配置如下关键环节：
1. **触发条件**：
   - 推送版本标签：`git tag v0.2.0 && git push origin v0.2.0`（匹配 `v*` 通配符）。
   - 手动触发：支持在 GitHub Actions 界面通过 `workflow_dispatch` 手动输入 `tag_name` 运行。
2. **构建与签名机制**：
   - 自动拉取代码并配置 JDK 17 与 Android SDK 35 (`build-tools;35.0.0`)。
   - 解码仓库 Secret `RELEASE_KEYSTORE_BASE64` 生成临时密钥库；若未配置生产密钥库，则自动回退至标准构建调试密钥库签名，确保产物随时可装。
   - 执行 `./gradlew :core:test` 验证音频核心契约。
   - 执行 `./gradlew assembleRelease` 生成正式签名 APK。
3. **校验和与制品发布**：
   - 自动生成 `app-release.apk.sha256` 与 `checksums.txt`。
   - 调用 `softprops/action-gh-release@v2` 将 APK 及校验和文件自动发布为 GitHub Release 制品。

---

## 3. 移动端独立运行与 AI 连接验收 (R2)

### 3.1 运行模式与架构选择
打开应用，切换至底部导航栏最右侧 **「设置 (Settings)」** 标签页，页面顶部即为 **「独立运行与 AI 配置」** 核心卡片。

系统提供三种运行模式（通过 FilterChip 单选切换）：
- **端侧独立 (零依赖) (`standalone`)**：手机脱离私有服务器 100% 独立运行，直接调用 Google 云端大模型转写并存入本地 Room，适合外出漫游、无自建服务器的用户。
- **专用服务端 (`server`)**：全量音频推送至内网/公网的 Python FastAPI 后端，由服务端集中执行 VAD、ASR 与数据归档。
- **混合模式 (端侧+远端) (`hybrid`)**：端侧直连大模型优先出字，本地即刻呈现时间线；连网时后台通过 WorkManager 异步推送录音至私有服务端归档。

### 3.2 凭据输入与硬件级安全存储
在选中「端侧独立」或「混合模式」后，展开 AI 配置选项：
1. **AI 服务提供方**：
   - `Google AI Studio` (`gemini_studio`)：个人开发者或标准 API 用户首选。
   - `GCP Vertex AI` (`vertex_ai`)：企业级 Google Cloud Platform 结算及指定地理区域用户。
2. **转写模型**：
   - 快捷选项：`gemini-2.0-flash`（强烈推荐，速度最快、音视频理解能力强、成本最低）、`gemini-1.5-flash`、`gemini-1.5-pro`。也可手动输入自定义模型名称。
3. **API Key**：
   - 填入 Google AI Studio 或 GCP 获取的 API 密钥。输入框支持眼睛图标切换密文/明文展示。
   - **安全机制**：点击「保存配置」后，API Key 经由 `SecurePreferences.kt`，由 AndroidKeyStore 硬件主密钥（AES-256-GCM 算法，每次加密使用独立随机 12-byte IV）加密后写入本地，杜绝明文泄露风险。
4. **Vertex AI 专有配置（仅选择 GCP Vertex AI 时显示）**：
   - **GCP Project ID**：填写 Google Cloud 项目 ID。
   - **Location**：默认为 `global`，可根据数据驻留合规要求填写 `us-central1` 等区域。
   - **自定义 Endpoint**：支持配置企业内网反向代理或网关地址。

### 3.3 一键连通性测试 (Connectivity Test)
在保存配置前，必须通过连通性测试：
1. 点击配置卡片下方的 **「测试连接」** 按钮。
2. 系统将显示旋转进度指示器，并由 `VertexAiClient` 向 Google 端点发送一个极小的轻量探测载荷（包含标准 WAV 头部与基础提示词）。
3. **验收观察点**：
   - 若密钥与网络正常：状态栏变绿并提示 `连接成功 (HTTP 200)`。
   - 若密钥错误：状态栏变红并提示 `连接失败 (HTTP 400/403): API key not valid...`。
   - 若网络不可达：提示网络超时或解析失败信息。
4. 测试成功后，点击 **「保存配置」** 按钮持久化设置。

### 3.4 录音、静音过滤与本地时间线呈现端到端验收
1. **启动录音**：
   - 切换至 **「时间线 (Timeline)」** 标签页，点击底部麦克风录音按钮。
   - 顶部状态栏显示录音时长以及闪烁红点提示，麦克风前台服务启动，状态显示 `16kHz PCM`。
2. **30 秒音频分块与 RMS 静音过滤**：
   - 录音服务持续采集 16-bit 16000Hz 单声道音频，每满 30 秒自动打包为一个标准 WAV 切片。
   - **静音门限保护**：`StandaloneTranscriber` 自动计算切片样本的 RMS 能量：
     - 若处于完全安静环境（$\text{RMS} < 0.003$），应用自动跳过云端 API 请求，直接完成切片索引，避免浪费用户 Token 额度。
     - 若检测到人声语音（$\text{RMS} \ge 0.003$），自动进行 Base64 编码并调用 Gemini 模型进行语音多模态识别与结构化 JSON 输出。
3. **本地时间线与记忆卡片即时刷新**：
   - 模型返回识别文本后，`StandaloneTranscriber` 解析出分段、发言人标签（如 `speaker_1`）与时间偏移，通过会话启动时间戳和采样时钟精确映射为 UTC 绝对毫秒。
   - 合成的事件（Events）、记忆（Memories）与发言人数据直接写入 SQLite Room 数据库 `CacheEntry` 表中。
   - 界面基于 Jetpack Compose 的 `Flow` 响应式机制无缝自动刷新，时间线立即展示对话内容，无需人工刷新或依赖任何后端接口。
4. **离线断网与后台自动续跑**：
   - 在无网络状态下（如飞行模式），录制的切片保留在本地 Room 中标记为 `pending`。
   - 当网络恢复后，`StandaloneWorker`（由 Jetpack WorkManager 监听 `NetworkType.CONNECTED` 触发）自动在后台唤醒，采用指数退避算法将积压的音频切片逐一转录完毕。

---

## 4. 应用内 APK 自动更新机制验收 (R3)

### 4.1 UI 更新卡片与版本状态呈现
在「设置」页下方，找到 **「关于与应用更新」** 卡片：
- **版本标识**：右上角高亮展示当前客户端版本号（例如 `v0.1.0` 或构建版本）。
- **版本详情**：展示应用名称 `EchoLedger (Echoes)`、构建编号 `Build X` 以及更新发布通道 `GitHub: CheemsaDoge/Echoes`。

### 4.2 检查更新与 SemVer 对比逻辑
1. 点击 **「检查更新」** 按钮：
   - 按钮右侧显示上次检查时间（如 `上次检查: 17:15`）。
   - 触发时进入加载状态，显示圆形进度环与提示文案 `正在检查最新版本...`。
2. 客户端异步请求 GitHub API `https://api.github.com/repos/CheemsaDoge/Echoes/releases/latest`：
   - **若是最新版本**：状态切换为绿色勾选图标，显示 `已是最新版本`，并提供「重新检查」按钮。
   - **若检测到新版本**（远程 SemVer 严谨大于本地当前版本）：
     - 界面展开新版本信息，展示版本标签（如 `v0.2.0`）与发布标题。
     - 提供两个操作按钮：**「立即更新」** 与 **「查看详情」**。

### 4.3 更新日志（Changelog）弹窗
点击 **「查看详情」** 按钮：
- 弹出居中更新日志对话框，完整渲染 GitHub Release 的 Markdown 变更说明文本。
- 支持纵向滚动浏览详细更新内容。
- 弹窗底部提供「关闭」与「开始下载」按钮。

### 4.4 流式下载与动态进度条
点击 **「立即更新」**（或详情弹窗中的「开始下载」）：
1. 客户端通过 `UpdateDownloader.kt` 建立针对 GitHub Release APK 资源的 OkHttp GET 流式下载。
2. 状态切换为 `Downloading`：
   - 实时计算并显示下载数据量及百分比，格式如：`4.2 MB / 11.7 MB (36%)`。
   - 下方呈现平滑动画的 `LinearProgressIndicator` 进度条。
   - 右侧提供 **「取消」** 按钮，点击即可中断当前下载协程并清理未完成的临时文件。
3. 下载文件存储于应用沙箱缓存目录：`context.cacheDir/updates/update_{version}.apk`。

### 4.5 SHA-256 完整性哈希校验
1. 下载完成后，后台 IO 线程自动读取下载的完整 APK 文件并计算 SHA-256 摘要。
2. 与 GitHub Release 提供的校验和（来自 release body 中的 SHA-256 哈希或 `checksums.txt`）进行安全比对。
3. 若哈希一致，状态流转为 **`ReadyToInstall`**（就绪可安装）；若文件损坏或哈希不匹配，则拦截安装并提示 `更新包完整性校验失败，请重新下载`。

### 4.6 FileProvider 安全安装与未知应用权限引导
1. 在更新卡片提示 `新版本 (vX.Y.Z) 已下载完成` 时，点击 **「立即安装」** 按钮。
2. **未知来源权限校验**：
   - `UpdateInstaller.kt` 检查系统权限 `packageManager.canRequestPackageInstalls()`。
   - 若首次安装且未授予该权限，应用会自动唤起系统设置页 `Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES`，引导用户允许 Echoes 安装应用，并在返回后继续安装。
3. **FileProvider 安装意图分发**：
   - 应用使用 AndroidX `FileProvider` 解析出安全内容 URI：
     `content://dev.echoledger.app.fileprovider/updates/update_{version}.apk`。
   - 构造 `Intent(Intent.ACTION_VIEW)`，附加 `FLAG_GRANT_READ_URI_PERMISSION` 与 `FLAG_ACTIVITY_NEW_TASK`。
   - 调起 Android 原生系统软件包安装程序界面，用户点击「更新」即可覆盖安装，数据无缝保留。

---

## 5. 自动化测试套件与构建验证指南

在开发机或 CI 环境中，可以通过以下命令对项目进行全量验证。

### 5.1 后端测试套件 (Pytest)
验证 Python FastAPI 后端的所有 API、VAD 算法、转录契约、数据库事务与 MCP 协议：

```bash
# 确保处于虚拟环境中
.venv/bin/pytest backend/tests
```

**验收标准**：
- **测试数量**：`113 passed`。
- **覆盖领域**：
  - `test_api_pipeline.py`：会话生命周期、分块上传、幂等校验、断点恢复。
  - `test_audio_vad.py`：WAV 头校验、能量 VAD、Silero ONNX 适配器逻辑。
  - `test_speaker_models.py`：发言人绑定、人物库管理、声纹契约。
  - `test_speakers_providers_mcp.py`：MCP JSON-RPC 工具调用、Token 鉴权、Origin 限制。
  - `test_vad_models.py`：场景化 VAD 标注与参数试算。

### 5.2 Android 单元测试套件 (Gradle / JUnit)
验证 Android 端核心音频算法、独立运行 AI 客户端、更新检查、流式下载与 ViewState 状态机：

```bash
# 执行 Android 单元测试
./gradlew :app:testDebugUnitTest :core:test --rerun-tasks
```

**验收标准**：
- **测试数量**：`96 passed`（0 failures, 0 errors, 0 skipped）。
- **模块分布**：
  - `:core:test` (3 项)：纯 Kotlin 音频契约、14 小时时钟算术、WAV 头部修复。
  - `SecurePreferencesTest` (4 项)：KeyStore 加密存取、密钥隔离。
  - `TimestampMappingTest` (6 项)：采样点与绝对 UTC 毫秒换算。
  - `VertexAiClientTest` (10 项)：Google AI Studio / Vertex AI REST 报文构造、JSON Schema 约束、网络容错。
  - `StandaloneAiStressTest` (16 项)：RMS 静音能量门限、批次数据注入 Room、离线恢复。
  - `SemVerTest` (7 项)：语义化版本大小比对边界测试。
  - `UpdateCheckerTest` (12 项)：GitHub API 响应解析、Asset 筛选、RateLimit 降级。
  - `UpdateDownloaderTest` (10 项)：OkHttp 流式写入、中断取消、SHA-256 校验。
  - `AppViewModelUpdateTest` (10 项)：检查/下载/就绪状态流转。
  - `AppViewModelChallenger2Test` (9 项)：并发更新、权限被拒与状态重置。
  - `M3EmpiricalChallengerTest` (19 项)：网络异常、破损 APK、下载断流等严苛异常场景测试。

### 5.3 Android 整包构建与 APK 产物验证
验证客户端在 Debug 与 Release 两种模式下的整包编译能力：

```bash
# 1. 运行官方验证脚本（包含 core 测试、assembleDebug 与 lintDebug）
./scripts/build_android.sh

# 2. 编译生产 Release 签名 APK
./gradlew assembleRelease
```

**产物检查**：
- Debug APK 位于：`android/app/build/outputs/apk/debug/app-debug.apk`（约 17.8 MB）。
- Release APK 位于：`android/app/build/outputs/apk/release/app-release.apk`（约 11.7 MB）。

---

## 6. 人工验收自检对照表 (Acceptance Checklist)

| 验收项目 | 验收指标与步骤 | 结果 |
|---|---|:---:|
| **R1. 远端与流水线** | `git remote -v` 指向 `https://github.com/CheemsaDoge/Echoes`；`.github/workflows/release.yml` 配置完整，支持 Tag `v*` 构建发布。 | [x] 合格 |
| **R2.1 运行模式切换** | 设置页支持切换「端侧独立」、「专用服务端」、「混合模式」，切换平滑且即时生效。 | [x] 合格 |
| **R2.2 凭据安全存储** | 输入 Gemini API Key，切换明暗显示，保存后经 AndroidKeyStore AES-256-GCM 加密落盘。 | [x] 合格 |
| **R2.3 连通性测试** | 点击「测试连接」按钮，能准确反馈 HTTP 200 成功或具体错误原因。 | [x] 合格 |
| **R2.4 录音与静音门限** | 开启录音生成 16kHz PCM；安静环境下 RMS < 0.003 自动跳过 API；说话时正常调用转录。 | [x] 合格 |
| **R2.5 本地时间线响应** | 独立模式下转写完成后，本地时间线、人物与记忆卡片基于 Room Flow 即时显示。 | [x] 合格 |
| **R2.6 离线与恢复** | 断网时分块进入本地队列，连网后 WorkManager 自动后台恢复转录。 | [x] 合格 |
| **R3.1 更新卡片与版本** | 设置页显示「关于与应用更新」，准确呈现当前版本与 GitHub 官方通道。 | [x] 合格 |
| **R3.2 检查更新与弹窗** | 点击「检查更新」，准确比对 SemVer；有新版本时可点击「查看详情」阅读 Changelog。 | [x] 合格 |
| **R3.3 流式下载与取消** | 点击「立即更新」启动 OkHttp 流式下载，实时更新 MB 与百分比进度，支持随时取消。 | [x] 合格 |
| **R3.4 哈希校验与安装** | 下载完成后自动进行 SHA-256 校验；引导未知来源权限并通过 FileProvider 调起系统安装器。 | [x] 合格 |
| **回归测试 (Backend)** | 执行 `.venv/bin/pytest backend/tests`，113 项测试全部通过。 | [x] 合格 |
| **回归测试 (Android)** | 执行 `./gradlew :app:testDebugUnitTest :core:test`，96 项测试全部通过。 | [x] 合格 |
| **构建产物** | 执行 `./scripts/build_android.sh` 与 `./gradlew assembleRelease`，成功生成 Debug/Release APK。 | [x] 合格 |
