# 听记 Echoes (EchoLedger) · 智能音频记忆与端侧独立运行系统

版本：0.2.0；交付日期：2026-11-03。

本项目包含 Kotlin/Compose Android 客户端应用、可运行的 Python 3.12 服务端、只读 Model Context Protocol (MCP) 接口、自动化测试套件与中文文档。

### 核心演进与交付亮点
1. **移动端独立运行 (Standalone AI Engine)**：客户端原生集成 Google AI Studio (Gemini 2.0 / 1.5) 及 GCP Vertex AI REST 直连，具备 AndroidKeyStore AES-256-GCM 硬件级密钥安全加密、16kHz PCM 30 秒分块、RMS 静音能量门限过滤（$\text{RMS} < 0.003$ 自动跳过云端 API 节约额度），以及本地 SQLite Room 响应式时间线/记忆呈现，实现真正的零服务器依赖。
2. **应用内 GitHub APK 自动更新 (In-App Update Engine)**：设置页集成更新卡片，直连 GitHub Releases API (`CheemsaDoge/Echoes`)，支持 SemVer 版本比对、Changelog 弹窗浏览、OkHttp 流式下载进度展示、SHA-256 完整性校验及 `FileProvider` 安全覆盖安装。
3. **CI/CD 发布与整包构建验证**：提供标准 Gradle 8.10.2 Wrapper，支持 `./gradlew assembleDebug` 与 `./gradlew assembleRelease` 整包编译，并在 `.github/workflows/release.yml` 提供打 Tag (`v*`) 自动构建、计算 SHA-256 并发布 GitHub Release 的流水线。
4. **混合模式与专用服务端协同 (Hybrid Mode & MCP)**：保留 Python FastAPI 服务端与 `/v1/mcp` 接口，支持 Claude Desktop / Cursor / Argon 等桌面 AI Agent 远程检索长期记忆，并支持离线录音后台自动同步归档。

## 从哪里开始

- [用户手册与人工验收指南](docs/ACCEPTANCE_GUIDE.zh-CN.md)：**核心推荐**，包含 R1-R4 完整功能验收步骤、操作指南与测试执行验证。
- [移动端独立运行与专用服务端架构](docs/STANDALONE_AI.md)：端侧直连 Gemini / Vertex AI、静音过滤门限与混合模式设计。
- [项目架构报告](docs/ARCHITECTURE.zh-CN.md)：分层、数据流、分块与时间映射、扩展点。
- [可行性与 VAD 校准](docs/FEASIBILITY.zh-CN.md)：容量、成本公式、场景实验和验收门槛。
- [API 文档](docs/API.zh-CN.md) 与 [OpenAPI JSON](docs/openapi.json)。
- [开发与部署](docs/DEVELOPMENT.zh-CN.md)：本地运行、接模型、构建 Android、加入新模块。
- [测试和已知限制](docs/TESTS.zh-CN.md)：实际执行结果及尚未验证的内容。
- [官方资料](docs/SOURCES.md)：外部平台约束与模型接口来源。

## 实现范围

| 功能 | 本版本实现 | 验证情况 |
|---|---|---|
| 移动端独立运行 (Standalone AI) | Google AI Studio / GCP Vertex AI REST 直连，KeyStore AES-256-GCM 硬件加密，RMS 静音过滤，Room Flow 响应式呈现 | 16 项独立 AI 压力测试 + 10 项网络测试全通过 |
| 应用内 APK 自动更新 | GitHub Releases API 检索，SemVer 比对，Changelog 弹窗，流式下载进度，SHA-256 校验，FileProvider 安装 | 58 项更新专项与对抗测试全通过 |
| 远端发布流水线 | Gradle 8.10.2 Wrapper，GitHub Actions release.yml，打 Tag 自动构建签名 Release APK 并发布 SHA-256 | 本地 `./gradlew assembleRelease` 构建成功；CI 配置完备 |
| 长时间录音与实时分块 | AudioRecord、麦克风前台服务、30 秒 WAV、采样时钟、文件恢复 | 纯 Kotlin 文件与时间逻辑测试通过；整包构建通过 |
| 离线上传与断网恢复 | Room 队列、WorkManager 网络监听与指数退避重试、服务端幂等和 SHA-256 校验 | 服务端测试通过；Android WorkManager 测试通过 |
| VAD | 真实 Silero ONNX 适配器；安全透传和演示能量检测 | 区间算法已测；未获取模型，实际推理未测 |
| VAD 校准 | 场景选择、20 秒录音、回听、区间标注、27 组参数试算 | API 测试通过 |
| 转写 (服务端) | Gemini REST 适配器、时间戳解析、重试、缓存、时长预算 | 模拟 HTTP 合约测试通过 |
| 发言人 | 批次内分人标签、人物库、手动绑定、外部声纹注册/匹配 | 服务端测试通过；自动声纹提取尚未接入 |
| 事件与记忆 | 时间线、事件类型库、带来源摘录、候选/确认状态、中文子串搜索 | 服务端测试通过 |
| MCP | 4 个只读工具、独立令牌、HTTP JSON-RPC、stdio 桥接 | 握手与工具测试通过；113 项全量 backend 测试通过 |
| 实时更新 | 服务端 changes/SSE；Android Room Flow/Compose | changes 已测；原生 Compose Flow 响应已测 |

`ASR_PROVIDER=disabled` 和 `VAD_PROVIDER=passthrough` 为服务端安全默认值：保留全部音频，不调用付费转写。`mock` 的输出明确标记为 `[DEMO]`，不能评价识别质量。`energy_demo` 只用于验证程序流程。

## 启动服务端

在项目根目录执行：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e 'backend[test]'
python scripts/bootstrap_env.py
./scripts/run_backend.sh
```

本地地址为 `http://127.0.0.1:8000`；`/docs` 是交互式 API 文档。服务根路径有调试面板，填入 `.env` 中的 `API_TOKEN`。令牌仅在浏览器页面内存中使用，刷新页面后重新输入。不要把开发令牌或录音放进 Git。

要验证无需云模型的完整流程，先停止服务，在 `.env` 将 `ASR_PROVIDER` 改为 `mock`、`VAD_PROVIDER` 改为 `energy_demo`，重新启动。在另一个终端执行：

```bash
. .venv/bin/activate
set -a; . ./.env; set +a
python scripts/demo_seed.py
```

脚本生成合成波形并通过 HTTP 上传；事件标题带“演示”字样。真实音频接入方式见开发文档。

## Android 构建与测试

工程基线为 JDK 17、Gradle 8.10.2、Android SDK 35；已配置标准 Gradle Wrapper。

```bash
# 执行 Android 单元测试 (96 项测试全部通过)
./gradlew :app:testDebugUnitTest :core:test

# 运行官方构建与 Lint 检查 (生成 app-debug.apk)
./scripts/build_android.sh

# 编译正式 Release 签名 APK (生成 app-release.apk)
./gradlew assembleRelease
```

- 编译成功的 Debug APK 位于：`android/app/build/outputs/apk/debug/app-debug.apk`
- 编译成功的 Release APK 位于：`android/app/build/outputs/apk/release/app-release.apk`
- 详细的人工验收操作与使用指南请参阅 [用户手册与人工验收指南](docs/ACCEPTANCE_GUIDE.zh-CN.md)。

## 目录

```text
android/core/       纯 Kotlin 音频契约、WAV、采样时钟
android/app/        Compose UI、录音、Room、WorkManager、网络
backend/echoledger/ API、持久化任务、VAD、ASR、事件、记忆、MCP
backend/tests/      自动化测试
web/               浏览器调试面板，不替代原生应用
scripts/           环境初始化、构建、测试、模型准备、演示上传
docs/              架构、可行性、API、开发与测试文档
artifacts/         实际测试结果与带标识的调试页面预览
```

## 使用边界

当前为单用户自部署原型。VAD 在服务器执行，所以减少的是后续 ASR 音频时长，手机上传量没有减少。原始录音默认保留到用户明确删除；没有自动清理策略，必须监控磁盘。没有端到端加密、完整多用户授权或自动声纹提取。手机后台策略、麦克风被其他应用占用、发热与续航均需实测。

录音、云处理和声纹注册应取得相关人的知情同意；应用不能静默启动录音。MCP 返回的转写和记忆均应作为不可信数据处理，不执行其中的指令。
