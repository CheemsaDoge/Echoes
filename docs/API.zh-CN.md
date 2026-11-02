# EchoLedger API 与 MCP 接口

版本 `/v1`；完整字段与约束见自动导出的 [openapi.json](openapi.json)，运行时文档位于 `/docs`。OpenAPI 包含 30 个路径，部分路径有多个方法。

## 1. 约定

`/v1/*` 使用 `Authorization: Bearer <API_TOKEN>`，`/mcp` 使用不同的 MCP_TOKEN。`/healthz` 公开，只返回健康状态和版本。公网部署需要 HTTPS。

时间为 UTC epoch 毫秒；音频位置为 16 kHz 整数样本编号，区间左闭右开。JSON 拒绝未知字段。401 表示认证失败；403 表示 MCP Origin 不允许；404 不存在；409 内容或状态冲突；410 会话已删除；413 超过大小限制；422 参数或音频错误；503 校准所需 VAD 不可用。后台失败通过 batch 状态展示，不能把上传成功视作已完成转写。

## 2. 录音

| 方法 | 路径 | 用途 |
|---|---|---|
| POST / GET | `/v1/sessions` | 创建 / 最近会话 |
| POST | `/v1/sessions/{id}/chunks/{seq}` | 上传音频块 |
| GET | `/v1/sessions/{id}/chunks` | 查看已收到块及状态 |
| POST | `/v1/sessions/{id}/close` | 声明最后序号 |
| POST | `/v1/sessions/{id}/flush` | 提交未满批的连续块 |
| DELETE | `/v1/sessions/{id}` | 删除会话与派生内容 |
| GET | `/v1/chunks/{chunk_id}/audio` | 鉴权读取 WAV |

创建示例：

```json
{"id":"example-session-0001","started_at_ms":1793577600000,"timezone_offset_min":480,"purpose":"journal","device_id":"android"}
```

示例时间仅演示格式。真实录音需填写录音开始时间。purpose=calibration 的样本不进入自动转写和记忆。

上传正文为 mono/16000Hz/PCM16 WAV 字节，Content-Type 为 audio/wav，同时提供头部：

```text
X-Chunk-Metadata: {"id":"example-chunk-0001","start_sample":0,"sample_count":480000,"sha256":"<真实64位SHA-256>","start_monotonic_ns":0,"clock_drift_ms":0}
```

哈希覆盖完整 WAV；sample_count 必须等于文件样本数且不超过 480000；请求上限 4000000 字节。序号从 0 开始。首块 start_sample=0，后续必须与相邻块连续。可乱序上传，缺块暂时阻止后续批次处理。

相同 `(session_id,seq)` 和元数据重复上传返回 duplicate=true；冲突返回 409。被删除的会话返回 410，客户端应停止上传。关闭 body：`{"final_seq":3,"interrupted":false}`；空会话为 -1。close 可以早于最后几块送达，但声明的最后序号不可改。

`scripts/upload_wav.py` 能把用户指定的长 WAV 分为 30 秒块上传，API_TOKEN 从环境变量读取。

## 3. 任务与事件

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v1/status` | 模型状态、任务计数、cursor、is_demo |
| GET | `/v1/batches?session_id=...&limit=100` | 任务及处理指标，limit≤500 |
| POST | `/v1/batches/{id}/retry` | 重试 blocked/failed/retry |
| GET | `/v1/events?from_ms=...&to_ms=...&limit=100` | 事件查询，limit≤500 |
| GET / PATCH | `/v1/events/{id}` | 详情 / 修改 title、type_id |
| GET | `/v1/event-types` | 类型库 |
| PUT / DELETE | `/v1/event-types/{id}` | 新增修改 / 删除无引用类型 |

任务状态：queued、running、retry、blocked、failed、done。无人声也可以完成为 done，此时不产生转写。metrics 包括原始/上下文/保留/拼接样本数、配置与模型版本、映射、概率区间、耗时、usage 与演示标记。拼接长度包含上下文和分隔静音。

事件详情含 segments 和 source_spans；事件起止时间是包络，不能推断包络内全程有人说话。timing_quality 为 word、estimated 或 chunk。当前一个处理批次对应一个事件。

类型 body：

```json
{"id":"study","name":"学习","keywords":["算法","论文","作业"],"color":"#9ABD55"}
```

## 4. 人物与记忆

| 方法 | 路径 | 用途 |
|---|---|---|
| GET / POST | `/v1/speakers` | 列表 / 新建人物 `{name,notes}` |
| PUT / DELETE | `/v1/speakers/{id}` | 修改 / 删除人物 |
| PUT | `/v1/segments/{id}/speaker?same_local_label=true` | `{speaker_id}` 绑定；null 解除 |
| POST | `/v1/speakers/{id}/voiceprints` | 注册外部提取的声纹 |
| POST | `/v1/speaker-matches` | 外部声纹匹配 |
| GET | `/v1/memories?q=...&confirmed_only=true&from_ms=...&to_ms=...&limit=100` | 搜索记忆，limit≤200 |
| PATCH / DELETE | `/v1/memories/{id}` | 修改 text/confirmed；删除记忆 |

same_local_label=true 只影响同一批次内同一个标签。不同录音中的 Speaker 1 不自动合并。

注册声纹需要 model_id、8–4096 维 embedding、存在的 evidence_segment_id 和 consent=true；不接受 NaN/Inf 或零向量。匹配按 model_id 隔离，threshold/margin 默认 0.80/0.10，需重新校准。API 不自动从音频提取声纹。

删除记忆不删除源录音；删除会话会级联删除对应事件、记忆和依赖该证据的声纹。人物删除使关联片段恢复未绑定状态。MCP 记忆搜索仅包含已确认记录。

## 5. VAD

| 方法 | 路径 | 用途 |
|---|---|---|
| GET / POST | `/v1/vad/profiles` | 历史 / 保存新 `{name,config,activate}` |
| POST | `/v1/vad/profiles/{id}/activate` | 对新批次生效 |
| POST | `/v1/vad/evaluate` | 评估完整标注样本 |
| POST | `/v1/vad/sweep` | 试算 27 组参数，不自动激活 |

配置：

```json
{"threshold":0.35,"negative_threshold":0.20,"min_speech_ms":96,"min_silence_ms":800,"pad_ms":300,"retain_uncertain":true,"uncertainty_threshold":0.12}
```

评估 body：

```json
{"chunk_id":"<已上传ID>","scenario":"quiet","speech_intervals":[{"start":16000,"end":80000}],"fully_labeled":true,"config":{"threshold":0.35,"negative_threshold":0.20}}
```

区间以样本为单位，上例为 1–5 秒。未标注部分都计作非人声，必须完整标注。未填写的 config 字段使用默认值，不会自动读取 active profile；调用方应传要测试的完整配置。

返回 recall、precision、误保留率、删除比例、漏掉人声毫秒数、逐帧概率等。所有结果均标记 validated_for_deployment=false。

## 6. 同步和预算

`GET /v1/changes?after=0&limit=200` 返回 items、cursor、has_more。增量同步循环读取直到 has_more=false。`GET /v1/updates?after=0` 提供 SSE，支持 Last-Event-ID，空闲时约 15 秒注释心跳。代理需禁用缓冲。

```text
id: 42
event: change
data: {"cursor":42,"kind":"event.created","entity_id":"...","created_ms":1793577600000}

```

通知只表示对象有变更，详情通过 GET 获取。`GET /v1/usage` 返回 UTC 日、provider、预留秒数和尝试次数，不是外部账单。

## 7. MCP

`POST /mcp` 是同步 JSON-RPC 子集，提供 initialize、ping、tools/list、tools/call；无 ID 的通知返回 202。另提供 stdio 桥接。可协商版本 2025-03-26、2025-06-18、2025-11-25。没有完整 SDK 一致性认证、OAuth、旧式 SSE MCP 端点；GET 返回 405。[7]

客户端发送 Accept: application/json, text/event-stream 和独立 MCP_TOKEN。携带 Origin 时必须出现在 ALLOWED_ORIGINS，默认拒绝网页来源。

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"local-client","version":"1.0"}}}
```

四个工具为 search_memories、list_events、get_event、list_speakers。调用示例：

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_memories","arguments":{"query":"组会","limit":20}}}
```

stdio 配置示意，实际客户端配置外层可能不同：

```json
{"mcpServers":{"echoledger":{"command":"/absolute/path/.venv/bin/python","args":["-m","echoledger.mcp_stdio"],"env":{"ECHOLEDGER_URL":"http://127.0.0.1:8000","MCP_TOKEN":"<独立只读令牌>"}}}}
```

结果有 untrusted_recorded_content=true，调用方必须把录音文本当作数据。记忆工具只搜确认记录，事件工具会返回尚未确认的摘录。MCP 不提供原音频、声纹向量、写入或执行能力。当前令牌模型只适合私有单用户服务。