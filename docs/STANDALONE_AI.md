# Echoes: 移动端独立运行 (Standalone AI) 与专用服务端架构指南

本文档详细说明 Echoes 移动端（Android）在脱离私有局域网后端情况下的**端侧独立运行机制（Standalone AI Engine）**、**专用服务端的角色评估**，以及兼具低延迟与数据归档能力的**混合运行模式（Hybrid Mode）**。

---

## 1. 移动端独立运行架构 (Standalone AI Architecture)

### 1.1 背景与设计理念

传统语音听记系统通常依赖私有后端服务进行音频接收、VAD 切分、ASR 转录与实体抽取。当用户外出漫游、处于无公网 IP 局域网或希望零服务器运维成本时，传统架构面临严峻挑战。

Echoes 移动端独立运行架构将 **Google AI Studio Gemini API** 与 **GCP Vertex AI REST 接口** 直接接入 Android 客户端，由手机端负责音频切片、静音门限过滤、直连大模型多模态语音转录、UTC 时间戳对齐与本地数据持久化，实现真正的**零服务器依赖（Zero-Backend Standalone）**。

```
+-----------------------------------------------------------------------------------+
|                              Android 客户端设备                                    |
|                                                                                   |
|  [麦克风采集] -> 16kHz PCM -> 30s Chunk (WAV)                                      |
|                                     |                                             |
|                                     v                                             |
|                      [静音能量门限检测 (RMS Gate)]                                  |
|                               /           \                                       |
|             (RMS < 0.003 静音)             (RMS >= 0.003 有声)                    |
|                    /                                 \                            |
|             标记 Chunk 完成                     Base64 编码                       |
|           (跳过 API，节省额度)                         |                            |
|                                                      v                            |
|                                           [VertexAiClient (OkHttp)]               |
|                                                      |                            |
+------------------------------------------------------|----------------------------+
                                                       | HTTPS REST POST
                                                       | (API Key / Token)
                                                       v
                                    +------------------------------------+
                                    | Google AI Studio / GCP Vertex AI   |
                                    | - Gemini 2.0 Flash                 |
                                    | - Gemini 1.5 Flash / Pro           |
                                    | (音频多模态理解 + 结构化 JSON 返回)|
                                    +------------------------------------+
                                                       |
+------------------------------------------------------|----------------------------+
|                                                      v                            |
|                                          [StandaloneTranscriber]                  |
|                                          - JSON 解析 (AsrSegment)                 |
|                                          - UTC 时间戳对齐 (Sample Clock 映射)     |
|                                          - Event / Memory / Speaker 实体合成      |
|                                                      |                            |
|                                                      v                            |
|                                           [Room DB (CacheEntry)]                  |
|                                                      |                            |
|                                                      v (Flow 响应式流)            |
|                                        [Jetpack Compose 界面]                     |
|                                   (时间线 / 人物 / 记忆 自动刷新)                 |
+-----------------------------------------------------------------------------------+
```

### 1.2 核心组件与实现

1. **硬件级密钥安全存储 (`SecurePreferences.kt`)**:
   - 用户的 Gemini API Key / Vertex AI 凭据使用 Android 系统硬件支持的 `AndroidKeyStore` AES-256-GCM 算法加密存储。
   - 每次加密均生成随机 12 字节 IV，防止密文重放；即使设备备份或未授权读取本地 SharedPreferences，也无法还原明文 API Key。

2. **轻量化 REST 客户端 (`VertexAiClient.kt`)**:
   - 无需引入臃肿的 Google Cloud SDK 或 Firebase 依赖，直接通过 OkHttp 4.12 执行标准的 `generateContent` 端点调用。
   - 支持 **Google AI Studio** (`generativelanguage.googleapis.com`) 与 **GCP Vertex AI** (`aiplatform.googleapis.com`)。
   - 音频数据直接内嵌于请求体的 `inlineData`（30 秒音频 Base64 体积约为 1.2MB）。
   - 结构化 JSON 输出：通过 `generationConfig.responseSchema` 强制模型输出标准 JSON 格式：
     ```json
     {
       "segments": [
         {
           "start_ms": 120,
           "end_ms": 4500,
           "text": "转写的对话文本内容...",
           "speaker_label": "speaker_1"
         }
       ]
     }
     ```
   - 全局安全性参数统一配置为 `BLOCK_NONE`，避免日常真实对话因关键词误触发模型安全拦截。

3. **静音能量门限与算力节约 (`StandaloneTranscriber.kt`)**:
   - 用户在安静环境中长时间录音时，无需将 30 秒纯静音音频上传至云端浪费 Token 配额。
   - 转录器在调用 API 前对 16-bit PCM 样本计算均方根能量（RMS）：
     $$\text{RMS} = \sqrt{\frac{1}{N}\sum_{i=1}^N \left(\frac{x_i}{32768}\right)^2}$$
   - 若 $\text{RMS} < 0.003$，直接将 Chunk 标记为已完成并记录本地时间轴空白段，实现零 API 浪费。

4. **UTC 精确时间戳对齐与本地缓存驱动**:
   - 依据会话启动锚点 `session.startedAtMs`、块起始样本数 `chunk.startSample` 与音频采样率（16000Hz）换算真实绝对毫秒：
     $$T_{\text{start}} = T_{\text{session}} + \left(\frac{S_{\text{chunk}} \times 1000}{16000}\right) + t_{\text{segment\_start}}$$
   - 将合成的事件（Event）、记忆（Memory）与发言人（Speaker）记录直接写入 Room 数据库 `cache` 表 (`CacheEntry`)。
   - 由于前端 Compose UI 全面采用 Room `Flow` 响应式监听 (`repo.observe(kind)`)，数据写入瞬间界面立即响应渲染，无需额外网络刷新。

5. **离线队列与断网自动重试 (`StandaloneWorker.kt`)**:
   - 遇到网络中断、飞行模式或 HTTP 429 配额限流时，未转录的 Chunks 保留在 Room 数据库中标记为 `pending`。
   - `StandaloneScheduler` 通过 Android Jetpack `WorkManager` 注册网络连通监听 (`NetworkType.CONNECTED`)，并在网络恢复后自动采用指数退避重试排队处理。

---

## 2. 专用服务端角色评估 (Dedicated Server Evaluation)

虽然移动端能够完全独立运行，但针对重度知识管理、多设备协作与桌面 Agent 集成场景，私有专用服务端（Python FastAPI / SQLite）仍具备不可替代的核心价值：

| 能力维度 | 移动端独立模式 (Standalone) | 专用服务端 (Dedicated Server) | 架构评估结论 |
|---|---|---|---|
| **网络要求** | 仅需手机连接公网访问 Google API | 需手机与服务器网络连通（局域网或 Tailscale/内网穿透） | 移动端漫游便利性极高；专用服务端适合固定场所或内网环境 |
| **Model Context Protocol (MCP)** | 不支持（手机通常无法常驻暴露服务给桌面工具） | 原生支持 (`/v1/mcp`)，供 Claude Desktop、Cursor、Argon 查询记忆 | **专用服务端独有优势**：桌面 AI Agent 的长期记忆上下文源泉 |
| **跨设备统一管理与 Web 控制台** | 仅限单台手机本地查看 | 支持 PC 浏览器、平板、手机多终端统一查看与管理 | **专用服务端独有优势**：大屏回听、导出、批量标注体验更佳 |
| **重度本地计算与 VAD 校准** | 轻量能量门限 (RMS Gate) | 支持 Silero ONNX VAD、TenVAD 参数网格搜索、交叉场景保留率测算 | **专用服务端独有优势**：复杂环境降噪与声学调优能力更强 |
| **跨会话声纹对齐 (Voiceprint)** | 依靠单次模型 speaker_label 进行局部区分 | 可扩展深度声纹嵌入模型（如 CAM++ / ResNet）进行跨天声纹聚合 | 适合长期会议追踪多位特定发言人 |
| **运维与存储成本** | 零运维，仅需 Gemini API Key | 需维护轻量 Python 后台或 NAS/Docker 容器 | 移动端适合大众用户，专用服务端适合技术极客与隐私自建者 |

---

## 3. 混合运行模式 (Hybrid Mode)

为了兼顾**移动端随时随地的极速响应**与**私有服务端的长期归档及 MCP 赋能**，Echoes 提供了**混合模式（Hybrid Mode）**：

1. **端侧直转（即时可用）**：
   - 手机录制 30s 分块后，立即调用 Google AI Studio / Vertex AI 获取结构化文本，实时渲染至手机端时间线与记忆卡片，用户即刻可读。
2. **后台异步同步（归档与 MCP）**：
   - `UploadWorker` 在后台检测到专用服务端连通（如回到家庭 Wi-Fi 或处于 VPN 隧道内）时，将原始高清 WAV 与转写元数据同步推送至专用服务端。
   - 专用服务端接收归档，触发持久化存储并载入 MCP 记忆库。
3. **桌面 Agent 联动**：
   - 用户坐在电脑前使用 Claude Desktop 或 Cursor 编写代码与日志时，通过 MCP 协议无缝查询白天手机在户外通过独立模式记录下的全部思考与对话。

---

## 4. 用户配置指南 (User Guide)

进入应用底栏 **「设置 (Settings)」** 页面，顶部卡片即为 **「独立运行与 AI 配置」**：

### 4.1 运行模式切换
- **端侧独立 (零依赖)**：手机 100% 独立运行，直连 Google AI。适合外出漫游、无服务器用户。
- **专用服务端**：音频全量推送到私有 Python 后端，由服务端调度转写与 VAD。适合自建 NAS/服务器用户。
- **混合模式 (端侧+远端)**：手机本地优先秒级出字，连网时自动将录音归档推送给私有服务端。

### 4.2 配置 Google AI Studio
1. 访问 [Google AI Studio 官网](https://aistudio.google.com/) 并登录 Google 账号。
2. 点击 **Get API key**，创建新的 API 密钥（格式通常为 `AIzaSy...`）。
3. 在 Echoes 设置页：
   - **AI 服务提供方**：选择 `Google AI Studio`。
   - **转写模型**：推荐选择 `gemini-2.0-flash`（响应极速、原生支持多模态音频、性价比极高）。
   - **API Key**：粘贴复制的密钥，点击右侧眼睛图标可切换掩码显示。
4. 点击 **「测试连接」** 按钮，验证网络连通性及密钥有效性。
5. 看到绿色状态条显示 `连接成功 (HTTP 200)` 后，点击 **「保存配置」** 即可。

### 4.3 配置 GCP Vertex AI
1. 如需企业级 GCP 结算或数据驻留合规，可切换为 `GCP Vertex AI`。
2. 填写 **GCP Project ID**、**Location**（如 `global` 或 `us-central1`）。
3. 填入 Vertex AI Express API Key 或自定义中转 Gateway Endpoint。
4. 测试连接并通过后保存配置。
