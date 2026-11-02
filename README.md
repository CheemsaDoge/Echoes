# 听记 EchoLedger · Android 音频记忆 MVP

版本：0.1.0；交付日期：2026-11-02。

交付包括 Kotlin/Compose 安卓工程、可运行的 Python 服务端、API、只读 MCP、测试和中文开发文档。服务端已在当前环境执行测试；Android 纯 Kotlin 核心已编译运行。**当前交付不包含 APK：当前环境没有 Android SDK/Gradle，原生界面和录音服务尚未经过整包编译及真机测试。** `artifacts/backend-console-*.png` 是浏览器调试面板的离线渲染，不能用于证明原生应用可运行。

## 从哪里开始

- [项目架构报告](docs/ARCHITECTURE.zh-CN.md)：分层、数据流、分块与时间映射、扩展点。
- [可行性与 VAD 校准](docs/FEASIBILITY.zh-CN.md)：容量、成本公式、场景实验和验收门槛。
- [API 文档](docs/API.zh-CN.md) 与 [OpenAPI JSON](docs/openapi.json)。
- [开发与部署](docs/DEVELOPMENT.zh-CN.md)：本地运行、接模型、构建 Android、加入新模块。
- [测试和已知限制](docs/TESTS.zh-CN.md)：实际执行结果及尚未验证的内容。
- [官方资料](docs/SOURCES.md)：外部平台约束与模型接口来源。

## 实现范围

| 功能 | 本版本实现 | 验证情况 |
|---|---|---|
| 长时间录音与实时分块 | AudioRecord、麦克风前台服务、30 秒 WAV、采样时钟、文件恢复 | 纯 Kotlin 文件与时间逻辑已执行；真机未测 |
| 离线上传 | Room 队列、WorkManager 重试、服务端幂等和 SHA-256 校验 | 服务端已测；Android 未整包编译 |
| VAD | 真实 Silero ONNX 适配器；安全透传和演示能量检测 | 区间算法已测；未获取模型，实际推理未测 |
| VAD 校准 | 场景选择、20 秒录音、回听、区间标注、27 组参数试算 | API 已测；原生页面未真机测 |
| 转写 | Gemini REST 适配器、时间戳解析、重试、缓存、时长预算 | 模拟 HTTP 合约已测；未使用真实密钥调用 |
| 发言人 | 批次内分人标签、人物库、手动绑定、外部声纹注册/匹配 | 服务端已测；自动声纹提取尚未接入 |
| 事件 | 时间线、事件类型库、关键词分类、人工修订 API | 服务端已测；跨批次语义聚合未实现 |
| 记忆 | 带来源摘录、候选/确认状态、中文子串搜索 | 已测；向量检索和长期事实归纳未实现 |
| MCP | 4 个只读工具、独立令牌、HTTP JSON-RPC、stdio 桥接 | 握手与工具测试通过；不是完整 SDK 一致性认证 |
| 实时更新 | 服务端 changes/SSE；Android Room Flow/Compose | changes 已测；原生 SSE 链路需真机验收 |

`ASR_PROVIDER=disabled` 和 `VAD_PROVIDER=passthrough` 为安全默认值：保留全部音频，不调用付费转写。`mock` 的输出明确标记为 `[DEMO]`，不能评价识别质量。`energy_demo` 只用于验证程序流程。

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

## Android 构建

打开 `android/` 为 Android Studio 工程。构建基线为 JDK 17、Gradle 8.10.2、SDK 35；依赖需要网络下载。交付包不含 Gradle Wrapper 二进制。安装全局 Gradle 后：

```bash
./scripts/build_android.sh
```

成功时 debug APK 位于 `android/app/build/outputs/apk/debug/`。也提供 `.github/workflows/check.yml` 供仓库 CI 执行构建及上传产物；**本次未在 GitHub 上运行该工作流**。Release 构建要求 HTTPS；模拟器调试可填 `http://10.0.2.2:8000`。

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
