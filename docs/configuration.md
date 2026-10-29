# 运行配置

启动许可检查见 `serve/license_probe.py`：先真实综合验证当前配置，失败再尝试官方地址。
成功缓存位于 `$AGENT_TMP/services/license-probe/`，日志区分 existing/fallback。
每次尝试最多 30 秒；重复启动复用成功记录，日常单题不增加综合调用。

`manifest.json` 保持官方示例字段结构。模型、上下文、输出、思考设置和模型调用上限以它为准；
额外运行默认值、权重文件名/哈希及构建校验信息保存在 `serve/runtime.json`。
显式环境变量优先；只有变量未设置时才读取默认值。

| 设置 | 唯一默认值位置 | 临时环境变量覆盖 | 开发启动器参数 |
| --- | --- | --- | --- |
| 本地模型端点 | `serving.port`，推导 loopback HTTP 地址 | `LLM_BASE_URL` | — |
| 本地模型服务ID | `model.name` | `MODEL_NAME` | — |
| 开发云上游端点 | `serve/runtime.json: development.base_url` | `CLOUD_LLM_BASE_URL` | `--upstream` |
| 开发云模型ID | `serve/runtime.json: development.model_name` | `MODEL_NAME` | `--model` |
| 上下文容量 | `context.max_model_len` | `AGENT_CONTEXT_TOKENS` | `--context-tokens` |
| 单轮输出上限 | `context.max_output_tokens` | `AGENT_MAX_OUTPUT_TOKENS` | `--output-tokens` |
| 输入字符裁剪门槛 | `serve/runtime.json: agent.input_char_limit` | `AGENT_INPUT_CHAR_LIMIT` | — |
| 响应字节上限 | `serve/runtime.json: agent.max_response_bytes` | `AGENT_MAX_RESPONSE_BYTES` | — |
| 模型调用上限 | `agent.max_rounds` | `AGENT_MAX_MODEL_CALLS` | — |
| 权重路径 | `/models/` 加 runtime 的 `model.file` | `MODEL_PATH` | — |
| 思考硬上限 | runtime 的 `serving.reasoning_budget_tokens` | `LLAMA_REASONING_BUDGET` | — |
| 推理历史保留 | runtime 的 `agent.preserve_thinking` | `AGENT_PRESERVE_THINKING=1/0` | — |

工具动作、EDA 次数/时长、内存和产物预算读取 runtime 的 agent 段，原有 AGENT_* 覆盖变量继续有效。
该公共文件不保存令牌，随 serve 目录进入镜像和源码哈希；模型 SHA-256 可用 MODEL_SHA256 覆盖。
基线原始脚本保持不变。官方必填字段没有配置且未提供覆盖值时仍直接报错。

输入字符门槛仅用于裁剪旧历史，响应字节上限用于限制传输，不需要随模型参数改成相同数字。
输出是配置上限，实际请求按本轮上下文中的输入与模板占用缩小，不受此前累计用量影响。

## Token 预算

本地 llama.cpp 通过 `/apply-template` 展开与生成一致的消息、工具和模板参数，再由
`/tokenize` 计数（包含展开后的模板），据此计算本轮上下文余量：

```text
本轮输出上限 = min(配置输出上限, 上下文容量 - 本轮输入tokens)
```

其他兼容后端不提供这些接口时，统一使用 `estimated_chars_v1`：ASCII 每三个字符
估一个 token，非 ASCII 每个字符估一个 token，输入另预留 1024 token 模板开销。
它不是精确计数或严格上界；后端实际上下文仍可能拒绝请求。只估算 messages/tools，
不将 HTTP、采样配置或 JSON 转义字节数当作 token。JSON 传输统一使用 `ensure_ascii=False` 的 UTF-8。

完成后优先使用 `usage.total_tokens`，缺少总量时使用有效的 prompt/completion 之和。
usage 缺失/不完整时，只对缺失部分使用输入计数或生成文本估算，排除 SSE、响应元数据
和 JSON 传输包装。trace 的 `input_token_source`、`token_accounting_source` 标明来源；
summary 的 `actual_tokens` 与 `estimated_tokens` 分别汇总，`total_tokens` 仅统计累计用量。
累计用量没有上限，旧 `AGENT_MAX_TOTAL_TOKENS` 不再生效；trace 的 `max_total_tokens` 为 null。
历史消息每次再次提交仍计入本轮输入。字符门槛或上下文不足时整轮裁剪旧历史；
无历史可裁剪时，字符门槛不再阻止请求，仅在上下文无输出空间时以 `context_budget` 退出。
现有 deadline、模型调用数和工具资源限制仍然生效。

历史保留原生 `tool_calls` 及配对工具结果；开启 thinking 且 runtime 的 preserve_thinking 为 true 时，
还保留推理内容，不回放普通 content，也不生成摘要。原始回复和 SSE 完整归档。
`agent_start.assistant_history` 标识实际策略；最近三轮工具历史及状态/反例记忆仍保留。

Agent本地端点须为无内嵌凭据的loopback HTTP地址，例如 `http://127.0.0.1:9001/v1`。
推理脚本从URL推导绑定主机和端口，从 `model.name` 设置服务别名；无需另改启动脚本。
健康检查和启动探测查询同一端点的 `/models`，核对相同服务ID。正式健康检查还要求有效模型名
与manifest声明一致，因此通过环境变量临时改成未声明模型时，ready仍为false。

开发云地址使用独立profile，须为HTTPS；启动器将其桥接到本机端点，再交给Agent。
云API Key独立通过隐藏输入或环境变量提供，不写进URL或manifest。
修改服务ID不会下载或切换权重；若实际更换模型，仍需设置MODEL_PATH并更新来源、revision和哈希。

查看当前有效配置：

```text
python -m agent.config
```

读取顺序为：开发启动器命令行参数 → 环境变量 → 对应的 manifest/runtime 默认值。
若修改 manifest 后数值未变化，先检查是否仍有旧的环境变量覆盖。
模型连接、窗口或输出配置变更后需要重启相应服务；现有同名模型进程可能被启动器复用。
输入裁剪门槛保留原有的逐轮读取语义。

`agent/config.py` 负责统一读取、类型转换和非秘密配置展示。ModelClient、Conversation、
RunControl 和开发启动器都使用这个入口。三个推理脚本及 start.sh 通过 `serve/config.sh`
调用同一读取器，Shell 不再定义这些数值。配置缺失或格式无效直接报错，避免退回到另一组默认值。

开发云服务也使用上述默认值。云服务实际上支持多大窗口取决于上游服务；如需不同配置，
使用参数显式覆盖，而不是在启动器中维护第二份默认值。

模型来源、版本、量化和历史实测显存仍在 MODEL.md/model/MODEL.md；这些测量记录描述当时的
配置，不是运行时默认值。未来调整连接或生成参数无需同步修改说明文档和启动脚本；窗口变更后仍需实测显存。
