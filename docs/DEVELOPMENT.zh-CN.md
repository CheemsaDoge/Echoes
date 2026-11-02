# EchoLedger 开发与部署

## 1. 环境与本地启动

建议 Python 3.12；代码要求 3.11+。本次测试环境是 Python 3.13.5，实际依赖版本记录在 `artifacts/test-environment.txt`。pyproject 使用兼容范围，不是全量锁文件。Docker、Android 依赖安装和 CI 在当前环境无法联网执行，发布前应生成并验证目标平台依赖锁。

在项目根目录：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e 'backend[test]'
python scripts/bootstrap_env.py
./scripts/run_backend.sh
```

bootstrap 仅在 .env 不存在时创建随机且不同的 API/MCP 密钥，权限 0600，不覆盖现有配置。服务默认在 127.0.0.1:8000。健康接口为 /healthz，API 文档为 /docs，根路径是开发调试面板。要让模拟器访问宿主机，可使用 10.0.2.2。手机访问局域网主机时，需要显式 HOST=0.0.0.0 并通过防火墙限制访问范围；禁止把明文开发服务直接暴露公网。

默认 ASR_PROVIDER=disabled、VAD_PROVIDER=passthrough，保证没有密钥时仍能接收录音，任务会保留等待配置。改 .env 后重启服务，已 blocked 的批次需要显式 POST /v1/batches/{id}/retry。

## 2. 无云模型演示

停止服务，在 .env 设置 ASR_PROVIDER=mock、VAD_PROVIDER=energy_demo，重新启动。在另一个已激活虚拟环境的终端：

```bash
set -a; . ./.env; set +a
python scripts/demo_seed.py
```

脚本通过真实 HTTP 创建会话、上传合成波形、关闭会话、等待事件，然后给事件加演示标题。所有结果 is_demo=true，mock 不进行真实识别。调试面板填 API_TOKEN 可以查看事件、来源、人物和确认记忆。浏览器只在内存保存令牌。

本次已实际执行演示上传和后端处理。由于当前浏览器策略阻止访问 localhost，页面截图采用实际 API 返回数据的离线渲染；未把这一项标为浏览器端到端点击测试。

## 3. 接入真实 Silero

先审查官方项目许可与模型来源 [4]，再安装 ONNX Runtime：

```bash
pip install -e 'backend[vad]'
python scripts/prepare_silero.py --source /absolute/path/silero_vad.onnx --destination data/models/silero_vad.onnx
```

prepare_silero 仅复制文件并记录 SHA-256，不下载或执行远程模型代码。也可安装经审查的 silero-vad wheel（使用 --no-deps 避免为复制模型安装整套 Torch），然后不传 --source，脚本从安装包定位模型；包结构变化时请显式指定文件。

.env 设置：

```text
VAD_PROVIDER=silero
VAD_MODEL_PATH=./data/models/silero_vad.onnx
```

适配器要求 ONNX 输入 input/state/sr，16 kHz、512 样本帧、64 样本上下文与递归状态。脚本记录模型哈希，但不代表推理兼容性或准确率已通过。先用一段有授权的已知语音执行校准；模型签名不一致时停止并调整适配器，不要强行忽略错误。

默认 Docker 镜像只装核心依赖；使用 Silero 时把 Dockerfile 的 pip install 改为安装 `/app/backend[vad]`，并将 ONNX 模型放入受控 volume、设置容器内路径。当前 Docker 配置尚未实际构建验证。

## 4. 接入 Gemini

服务端 .env 设置：

```text
ASR_PROVIDER=gemini
GEMINI_API_KEY=<你自己的API密钥>
GEMINI_MODEL=gemini-3.5-transcribe
GEMINI_MODE=transcribe
```

模型与 REST 字段参照 2026-11-02 检查的官方文档 [5][6]。密钥只放服务端，不写进 APK。该适配器用 httpx 调用 Interactions，设置 store=false；这关闭交互状态保存，不代表提供方所有日志、审核或其他数据保留都为零。部署前应核查账号、区域、模型权限、额度、价格和相关隐私条款 [8][9]。

实际密钥调用没有在本环境执行。第一次联调先录制自己同意上传的短样本，核对原文、两人标签、词时间戳和用量，再扩大到日常录音。若切换 GEMINI_MODE=structured，需要同时设置支持该模式的适当模型；不能假定所有模型都接受相同输出 schema。

长 WAV 导入：

```bash
set -a; . ./.env; set +a
python scripts/upload_wav.py sample-16k-mono.wav --started-at-ms <录音开始UTC毫秒>
```

脚本仅接受16k/mono/PCM16，自动分块。失败可能留下未关闭会话供排查；脚本本身不实现断点恢复，Android 上传队列才承担自动恢复。

## 5. Android 构建与调试

工程基线：JDK 17、Gradle 8.10.2、Android SDK 35、AGP 8.8.2、Kotlin 2.1.10；minSdk=26、targetSdk=35。版本选择用于稳定构建基线，没有声称这是最新版本或满足未来应用商店要求。

本次不含 Android SDK 和 Gradle Wrapper JAR，无法交付已验证 APK。可打开 android/ 为 Android Studio 项目；设置 sdk.dir 或 ANDROID_HOME，安装 SDK 35 和对应 build-tools，再执行：

```bash
./scripts/build_android.sh
```

脚本运行 core:test、app:assembleDebug、app:lintDebug。成功产物在 android/app/build/outputs/apk/debug/。CI 定义位于 .github/workflows/check.yml，需要将代码放入用户自己的 GitHub 仓库后触发；本次没有替用户创建仓库、推送或执行工作流。

调试版允许 HTTP，发行版只允许 HTTPS。设备设置页填写服务端地址与 API_TOKEN；首次开始录音会请求权限和告知录音影响。开始后检查通知、时间计数、待上传数量、服务端 chunks 与 batches。校准录音停止后可以回听并标注全部语音，再请求评估。

没有实现：蓝牙指定路由、系统通话录音、绕过后台限制、开机静默录音。来电或其他应用占麦时应显示状态并检查实际音频，不能假定平台会持续交付有效样本。

## 6. 部署

Docker Compose 只绑定宿主机回环 8000，持久卷保存数据库和音频，容器使用非 root UID 10001：

```bash
python scripts/bootstrap_env.py
docker compose up --build -d
```

这些命令需要在具备 Docker 和网络的目标主机执行。公开访问使用已有反向代理或 Caddyfile.example，替换域名、配置 DNS、TLS 和防火墙。SSE 要及时刷新，不能被代理缓冲。不得在日志中记录 Authorization、音频正文或完整转写。

默认一个 uvicorn worker 内带一个处理线程。要独立运行处理器，API 进程设置 RUN_WORKER=false，再用同一配置运行 `python -m echoledger.worker`。不建议在当前 SQLite 方案上直接水平扩容；先测量并迁移数据库/任务锁，再考虑多节点。

备份使用 SQLite 在线备份 API或停服后复制数据库、音频及派生目录，不要只复制正在写入的主 .sqlite3 文件而遗漏 WAL。数据库有 schema_version，后续增加显式迁移；Android Room 同样需提供 Migration，不使用破坏性重建代替升级。

监控至少包含队列最老等待时间、失败/blocked、剩余磁盘、原始与拼接时长、ASR 预留秒数、手机上传积压。现阶段无生产级告警和自动保留期任务。

## 7. 增加实现

新增 VAD：实现 FrameScorer.probabilities，返回每 512 样本一项 [0,1] 概率，提供包含版本/权重哈希的 model_id，在 make_scorer 注册。区间处理和 source map 无需变化。

新增 ASR：实现 Transcriber.transcribe，返回 Transcript 和 AsrSegment；时间相对当前拼接音频，不能直接填写真实时钟。错误转换为 ProviderBlocked 或可重试 ProviderError，错误信息不携带密钥/原始响应。新增模型影响缓存指纹，必须更新版本并添加合约测试。

新增自动声纹：实现 SpeakerEmbedder，从来源清晰的原始片段提取向量，再通过注册/匹配接口使用。现有适配器接口尚未连入自动身份流水线，应先完成质量和拒绝阈值测试。

新增记忆抽取：实现 MemoryExtractor，但需要明确接入后处理步骤、版本化输出和去重语义；当前接口仅为契约。高级记忆必须保留证据，用户编辑不能被后台重跑覆盖。

新增端侧 VAD：实现 AudioPreprocessor，输出原始样本区间。传输协议要增加“原始覆盖范围与保留片段映射”，不能把裁剪后的 sample_count 当作原始时间轴。端侧模型与配置版本一并保存，保留可回退原音频的策略。

## 8. 测试

```bash
./scripts/test.sh
```

Python 测试覆盖 API/算法/事务/模拟提供方；kotlinc 存在时执行纯 Kotlin 核心烟雾测试。与 Android 整包测试、模型真实推理和长时间录音分别验收，详见 TESTS.zh-CN.md。

下一次开发建议先执行整包构建与短时真机联调，修复编译/平台问题，再做 8 小时可靠性与独立 VAD 样本验证。不要先堆叠更复杂的记忆功能而缺少底层录音证据。