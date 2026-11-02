# 官方资料与核查范围

核查日期：2026-11-02。以下链接用于平台约束、架构实践和模型协议，不代表本项目已通过对应平台认证。项目自定参数、算法策略、成本算式和实际测试结果分别在架构、可行性与测试文档说明。

1. Android foreground service types：麦克风服务类型、权限、while-in-use 限制。
   https://developer.android.com/develop/background-work/services/fgs/service-types
2. Android background foreground-service start restrictions：后台启动限制。
   https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start
3. Android offline-first architecture 与 long-running WorkManager：本地存储、同步、任务调度约束。
   https://developer.android.com/topic/architecture/data-layer/offline-first
   https://developer.android.com/develop/background-work/background-tasks/persistent/how-to/long-running
4. Silero VAD 官方仓库与 ONNX 参考实现：模型用法、采样率、输入/状态结构、参数。
   https://github.com/snakers4/silero-vad
   https://github.com/snakers4/silero-vad/blob/master/src/silero_vad/utils_vad.py
5. Gemini Audio transcription：gemini-3.5-transcribe、Interactions、分人标签、词时间戳、实时接口说明。
   https://ai.google.dev/gemini-api/docs/transcribe
6. Gemini Audio understanding：音频内联输入、结构化输出示例。
   https://ai.google.dev/gemini-api/docs/audio
7. MCP transports：stdio、Streamable HTTP、JSON-RPC、Origin、认证及返回方式。
   https://modelcontextprotocol.io/specification/2025-03-26/basic/transports
8. Gemini Developer API pricing：按模型核查实际计费，不使用一个固定音频 token 换算覆盖全部模型。
   https://ai.google.dev/gemini-api/docs/pricing
9. Gemini Interactions overview：状态存储、store=false、数据保留说明。
   https://ai.google.dev/gemini-api/docs/interactions-overview

实现说明：第三方模型、SDK 和平台规则可能变化。这里保留查询日期和适配器版本；实际调用仍需凭据联调。Silero 权重不随项目分发，运营者须单独审查来源、许可证和版本。Android/Gradle 依赖为指定构建基线，不声称最新。完整服务端依赖锁与供应链审计尚未完成。