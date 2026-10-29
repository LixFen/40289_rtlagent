# 开发 HTTP 服务与镜像选择

2026-10-16 的闭环研发变更、通用 TB 片段接口、观察记忆与实验上下文开关见
[研发迭代记录](development-loop.md)。下方保留各次历史实测，不把旧结果当作新版验收。

2026-10-14。本次按仓库中的赛事公开要求核对；官方基础镜像名称仍为占位符，
尚无 digest/SBOM，因此下表不是官方镜像的逐包差异。

## 两种运行方式

- 测试：本机 Agent + 本机 Vivado，云端 AMD OpenAI-compatible API 提供模型。
  `tools/serve_development.py` 只绑定本机 loopback，为 Agent 和原样 baseline 统一附加云端鉴权。
  不改提示词、采样参数、模型回复或 baseline。模型列表只保留上游实际公布的指定模型，
  防止 baseline 自动选择另一个模型。此桥接器不是 MCP，也不提供解题工具。
- 生产：R9700 单卡 32GB、模型和 Vivado 同机，完全离线。继续使用现有启动脚本。
  云 API 的权重、量化、上下文和 GPU 无法据短生成核实；云端结果仅作开发验证，
  不能代替选定 GGUF 的正式配对评测或生产显存验收。

## 本机盘点

| 组件/要求 | 本机状态 | 缺项/边界 |
| --- | --- | --- |
| Vivado 2026.1 | Windows 安装在 `D:/AMDDesignTools/2026.1/Vivado` | 初始 PATH 缺失；测试入口按进程设置，不修改系统 PATH |
| xvlog / xelab / XSim | 实际验收通过 | 自建公开题目，包含先失败再修复的仿真 |
| lint / OOC synth / ZU3EG | 实际验收通过 | 默认 `xczu3eg-sbva484-1-e`；不是官方题集得分 |
| Vitis 2026.1 | `vitis-run --version` 通过，Build 6497934 | 有 v++、vitis-run、vitis 等入口；HLS 真任务未验证，RTL 当前不依赖 |
| Python | 3.13.7，标准库预检通过 | 非最终 Linux ABI 环境 |
| bash / Linux 运行环境 | 有 Windows WSL 入口 | 仅发现 `docker-desktop` WSL2 发行版，已停止；未发现用户 Linux 发行版 |
| Docker | 客户端 29.8.0 | desktop-linux 引擎不可连接，尚不能构建/运行镜像 |
| Linux Vivado/Vitis | 本次未发现独立 Linux 工具环境 | Windows 安装不能直接运行于 Linux 容器 |
| 镜像变量与目录 | 当前 shell 缺少赛事统一设置 | 本地入口设置 Vivado/PATH/档案目录；Linux 镜像沿用基础镜像变量 |
| eda-workdir / 临时目录清理 | 未验证官方助手 | 当前 Agent 自建逐次工具目录；不在 Windows 模拟后台清理 |
| 云端模型 | Qwen3.8-27B 模型列表及真实短生成通过 | 密钥隐藏输入，仅保留进程内；需要网络，非生产依赖 |
| 本机 ROCm / GPU 推理 | 本次未验收 | 云模型测试无需补装；生产必须在 R9700 实测 |
| GPU 显存遥测 | 本机未提供云 GPU 遥测 | 生产 `/v1/health` 仍可能 ready=false，不伪造显存或放宽生产条件 |
| baseline / Ross 资产 | baseline 两文件与官方一致，九项资产哈希通过 | Ross MCP/完整 RAG 未接入，也不是当前解题依赖 |
| 磁盘 | 盘点时 C 约 35.7 GiB、D 约 32.0 GiB 可用 | 打包 Linux 工具链/完整模型前须按选定镜像体积扩容或换盘 |
| 官方镜像 digest / 清单 | 未取得 | 暂不能确认逐包版本、补丁和完整性一致 |
| 离线授权 / Linux 隔离 / 长稳 | 未完成 | 当前 Windows 在线验收不能覆盖这些项目 |

本次本地真实工具验收：`out/local-toolchain-audit-20261014-approved/report.json`，
88.423 秒，12 次脚本化决策、先仿真失败后修复通过、lint 和综合通过。
脚本化决策不计作真实模型能力。沙箱内初次 HTTP 被 WinError 10013 阻止，
在获准的沙箱外执行后通过。云端真实模型的独立验证资料在 `out/local-cloud-audit-20261014/`。

真实云端 Agent 单题请求 **未通过**：公开 AND 门题，墙钟 298.859 秒，最终无 RTL。
第 1 次模型调用耗时 79.919 秒，动作 JSON 多出结束括号；第 2 次耗时 28.214 秒，
成功建立测试契约；第 3 次在剩余预算内未完成并超时，退出原因 deadline。
本次未进入 EDA 步骤，不能声称云模型与本地工具的完整闭环已通过，也不能据此计算
官方准确率/增益。API 连通、独立工具验收与限时解题能力是三个不同结论。
临时验证服务已关闭；再次测试按下方命令启动。

扩大输出额度后重测同题：累计预算 262144，单轮配置上限 131072，扣除输入后
前两轮实际请求额度分别为 124012 / 124004。墙钟 298.756 秒，仍无 RTL；
3 次模型调用中 2 次完成回复，第 1 次 JSON 格式错误，第 2 次建立契约，
第 3 次超时。已收到回复的累计用量 4676 tokens（不包含上游未返回的超时调用消耗），
退出原因仍为 deadline，未调用 EDA。证据为
`out/local-cloud-retest-256k-20261014/trace.jsonl` 和 `summary.json`。
这次重测说明扩大输出额度未解决本次限时失败；未测量更长墙钟或其他推理配置。

## 启动 Windows HTTP 服务

在项目目录运行，不需要本地 GPU、Docker 或 bash：

```powershell
cd D:/AttendingCompete/rtl-agent
python tools/serve_development.py --probe
python tools/serve_development.py
```

脚本先隐藏读取云 API Key，再核对上游 `/models`。`--probe` 实际发送一次短生成后退出。
常规启动会再隐藏读取本地 Agent bearer token，随后提供 `127.0.0.1:7860/v1/solve`。
调用遵循赛事契约，`mode=agent` 或 `mode=baseline`；两者都用同一个云端模型桥接入口。
默认云上游和模型ID读取manifest.development；可用 `--upstream` / `--model` 临时覆盖，
环境覆盖为CLOUD_LLM_BASE_URL / MODEL_NAME。Vivado安装位置可用 `--vivado` 指定，桥接端口默认18000。
累计 token 用量仅记录，不限制任务，也不再提供 `--total-tokens` 参数。
测试 Agent 的上下文、单轮输出和传输默认值读取 manifest，与本地启动器共用配置入口；
单轮输出受本轮上下文余量限制。输入优先按本地模板/tokenizer 计数，不支持时使用明确标记的字符估算。
可用 `--context-tokens` / `--output-tokens` 临时覆盖，优先级见 [运行配置](configuration.md)。
输入字符门槛和响应字节上限取自 manifest.agent，响应上限包含SSE封装开销。
依据 [AMD 模型文档](https://amd-aim.github.io/radeon-cloud-docs/models/qwen3-8-27b/)，
该云 API 上下文为 131072 tokens（输入和输出合计），思考与正文共用输出额度。
Agent配置窗口必须不超过上游实际窗口；实际输出额度受配置输出上限和本轮输入预留限制。
开发结果不能代替本地GGUF与目标GPU的验收。
HTTP 墙钟仍由请求的 `deadline_s` 决定；上次退出原因是 deadline，增大 token 预算
本身不会延长 300 秒请求，也不代表云端模型支持 256K 上下文。
Ctrl+C 关闭。开发结果默认写入 `out/local-cloud-test`，不进入镜像。

自动运行可通过进程环境提供 `CLOUD_LLM_API_KEY` 和 `FPGACHINA_TOKEN`；
不要将真实值写进命令历史、源码、Dockerfile、构建参数或报告。
桥接器从环境取出云密钥后立即移除该环境变量，EDA 环境本身也采用无密钥白名单。
生产健康检查继续要求本地 GPU 遥测，测试连通性由模型检查和真实 solve 分别确认。

## 镜像打包入口

Dockerfile 已提供两个 target，默认最终阶段是 production：

```bash
docker build --network=none --build-arg BASE_IMAGE='<verified-base>@sha256:<digest>' --target production -t rtl-agent:production .
docker build --network=none --build-arg BASE_IMAGE='<verified-base>@sha256:<digest>' --target test -t rtl-agent:test .
```

两者共用 Agent、技能、baseline 和 Linux EDA 基础层，test 额外携带云端鉴权桥接入口。
production 通过 `serve/entrypoint.sh` 启动；`serve/start.sh` 负责幂等启动模型与辅助服务并在就绪后返回。
test 不启动本地模型/显存采样器，需要 HTTPS 出站和 `XILINX_VIVADO`；交互方式可在
Linux Docker 主机以 `docker run --rm -it --network host rtl-agent:test` 隐藏输入凭据。
生产运行禁止外联并预置完整模型/运行库，不能使用同一测试网络设置。

**这两个镜像尚未构建或启动验证。** 先恢复 Docker 引擎、取得可用的 Linux EDA
基础镜像/安装和足够磁盘；当前 Windows 原生接入已经可以独立使用。
不增加跨容器远程执行 Windows EDA 的服务，避免为临时测试引入第二套工具协议。

## JSON mode 与优先保存代码实验

2026-10-14 后续调整：已移除 set_contract 和 amend_test 的 source_quote 必填及逐字匹配
要求。检查项仍由模型根据公开题面描述，需唯一 id 和非空 statement；这项结构校验不证明
模型测试 oracle 正确。simulate 可直接传 `checks`，首次调用时注册不可变契约，
无需额外一次 set_contract；后续 simulate 中的 checks 不能替换已有契约。
模型指导改为保存 RTL 后优先写自检 TB 并运行 simulate，不要求先做计划/引用动作。
simulate 还可携带 `testbench_content`，同一次动作创建 TB、注册 checks 并执行真实工具；
已有测试冻结后不允许通过此参数覆盖，必须走受限 amend_test。
保存/修补主 RTL 后的观察包含代码正文（最多 12000 字符，另标总长及截断状态）和
立即测试的下一步指导，避免后续无历史调用只看到哈希而反复重新生成 RTL。
测试冻结、已知 DUT 反例保留、只有工具确认 TB_ERROR 才允许说明原因修订 TB 的规则继续生效。
以下记录为调整之前的历史实验，引用错误不再是当前流程的要求。

```powershell
python tools/serve_development.py --code-first
```

`--code-first` 要求先调用 write_file 保存完整 RTL，再由模型选择验证和修复工具。
模型请求使用 Chat Completions functions，调用与工具结果按 ID 配对；源码保存不代表验证通过。
以下历史实验的配置以各自档案为准，单次结果不能分离组合改动的贡献。

2026-10-14 同题实测：

| 配置 | 首次保存 RTL | 请求墙钟 | 结果 |
| --- | --- | --- | --- |
| 原样 baseline | 约 24 秒 | 24.445 秒 | 一次生成，独立编译/展开/四组合仿真/综合通过 |
| 原 Agent，扩大 token 额度 | 无 | 298.756 秒 | JSON 格式错误、契约动作、模型超时，无 RTL |
| JSON mode + code-first（修正版） | 15.296 秒 | 298.856 秒 | 4 次模型调用，无 JSON 解析错误；返回已有 RTL，Agent 内无 EDA 证据 |

初版实验重复保留“首轮先写代码”的提示，造成模型反复写同一候选，于 170.592 秒人工
停止；证据在 `out/local-cloud-json-code-first-20261014/`，summary 标注 stopped_by_operator，
不能作为完整运行。修正版只在 state.current 为空时使用首轮指导，已有候选后恢复
完整自主指导；对应回归测试验证后续请求不再包含首轮优先写代码指令。

修正版第 2 轮契约 source_quote 不是题面逐字引用，被控制器拒绝；第 3 轮自行修正。
第 4 轮等待回复超时，退出原因 deadline。已收到回复累计 6408 tokens；
未返回调用的消耗未知。证据在 `out/local-cloud-json-code-first-v2-20261014/`，
原始逐次档案位于初版目录的 `runs/request-fhz29v07/`（复用同一个桥接服务）。

最终返回的 RTL 经独立可信侧、同 baseline 测试台验证，编译、展开、四组合仿真、
ZU3EG OOC 综合全部通过，结果为
`../trusted-evaluation/local-cloud-json-code-first-v2-20261014/result.json`。
这项外部验证在 HTTP 请求结束后运行，不计入 298.856 秒，不能声称 Agent 在期限内
自行完成工具闭环。实验改进了早期产物与格式可靠性，但还未解决后续决策迟缓。
20 项相关回归通过，baseline 两文件及九项 Ross 资产哈希通过；非正式增益实验。

### 移除引用与直接仿真后的验证

完整回归 45 项通过（81.246 秒），日志 `out/direct-test-v2-regression-20261014.log`；
随后清理不再使用的题面引用存储字段，14 项工作区/工具能力回归通过。
独立可信脚本实跑新 simulate API：一次动作传入 TB 正文和无引用检查项，真实
xvlog/xelab/XSim 通过四个 AND 门组合，测试冻结生效。
证据 `../trusted-evaluation/direct-simulate-acceptance-20261014/result.json`。
这是接口验收，测试台由可信脚本产生，不能记作云模型自建测试台成功。

真实模型两次尝试均使用同题和 300 秒期限：

- 仅移除引用/合并检查项：11.152 秒保存 RTL；第 2 轮 273.301 秒后仍重写 RTL，
  第 3 轮超时，HTTP 总墙钟 298.771 秒。证据 `out/local-cloud-direct-test-20261014/`。
- 再加入保存代码正文的观察及一次动作创建/执行 TB：14.599 秒保存 RTL；
  第 2 轮超时，HTTP 总墙钟 298.793 秒，无 EDA 调用。证据
  `out/local-cloud-direct-test-v2-20261014/`。

最终返回候选 SHA-256 与此前独立通过的 AND 门代码一致。
引用约束和额外契约轮次已移除，但云模型在现有推理配置/期限下仍未完成自主自测；
不能以可信脚本验收代替该结论。未调整推理强度或墙钟期限。

## 流式 trace

本地测试入口默认启用流式模型请求，可用 `--no-stream` 关闭。其他入口可设置
`AGENT_STREAM=1`；生产默认和官方 baseline 保持原配置，baseline 仍为原样非流式。
使用 `stream=true`，请求 `stream_options.include_usage=true`；云平台可能过滤该可选参数，
只记录实际收到的 usage，不推定请求生效或捏造超时消耗。

- `llm_request` 在联网前落盘，未收到首字节也能看到本次完整模型请求。
- `llm_stream_event` 即时记录服务端事件原对象，保留 content、reasoning、reasoning_content、
  usage 和未知字段。SSE 注释、非 data 字段和未结束的半行另由原始文件保存。
- 每轮 `llm-NNNN.sse` 保存并逐片 flush 原始响应字节，截止前已收到的部分仍保留。
- `llm_stream_end` 标记 completed、接收字节、正文/推理字符和已收到的用量。
- 若监督器硬截止杀死工作进程，可能没有 stream_end；以已有 request/start、缺少结束标记
  和监督器 timeout 识别不完整调用，原始 SSE 及已完成事件仍保留。
- 完整回复仍组装为既有 `llm` 记录后解析动作；缺失 finish_reason / [DONE]、断流或
  字节超限不执行半条工具动作。完整事件之前的残片只在原始 SSE 中，不能冒充合法事件。

本机鉴权桥接器逐行转发并 flush，不等待完整上游回复；下游断开时关闭上游连接。
这不保证云服务取消计算，也不能读取服务端尚未发送的内部信息。此处流式是模型调用，
赛事 `/v1/solve` 返回 HTTP 202 与 job_id；通过 `/v1/jobs/<job_id>` 轮询，done 响应包含 trace。
运行中可在本机读取档案文件，结束后的响应 trace
包含已完成的事件记录。原始 SSE 文件位于每题档案 out 中，不作为模型可写报告。
参考 [AMD 流式接口文档](https://amd-aim.github.io/radeon-cloud-docs/zh-cn/api/chat-completions/)。

真实云端同题实测保存在 `out/local-cloud-streaming-20261014/`：HTTP 299.535 秒，
首轮完成并保存 RTL；共捕获 3453 个事件。第二轮捕获 3346 个事件、13406 个
推理字符、0 个正文字符，原始 SSE 879311 字节，随后被监督器截止。
第二轮无最终 usage / finish / DONE，summary 标为 incomplete_calls=[2]，
不能声称拿到了完整服务端内部信息或实际总 token 消耗。
该实测运行的首轮有服务端 usage；实测后修正无 usage 时的估算，排除 SSE 封装字节，
使用组装后回复字节保守记账。完整回归 51 项通过（119.000 秒），
预算修正后 16 项相关回归通过，包含新增封装开销不计 token 的测试。
日志为 `out/streaming-regression-20261014.log`。官方 baseline 未修改。

### 拼接后的正式 trace

每轮正常结束或读取异常后自动导出 `trace.merged.jsonl` 和 `trace.md`；监督器停止工作
进程后再从原始 SSE 重建，覆盖没有 finally / stream_end 的硬截止情况。
`/v1/solve` 的 Agent `trace` 字段现在返回拼接后的 JSONL：每轮一条 llm，包含完整请求、
连续推理/正文、completed 状态和已收到的 usage，保留动作与工具记录，去掉 SSE 分片流水。
原始内部 `trace.jsonl` 和 `llm-NNNN.sse` 继续作为底层证据，不覆盖或伪造未收到的内容。
不同事件中的 reasoning / reasoning_content 按接收顺序合并；同一事件的重复别名不重复拼接。
拼接只是展示与追踪，不把不完整模型回复当作可执行动作。

上次真实流式实验已直接转换，无需重调模型：
`out/local-cloud-streaming-20261014/trace.md`（阅读版）和
`out/local-cloud-streaming-20261014/trace.merged.jsonl`（机器版）。
第一轮推理 176 字符、正文 228 字符；第二轮连续推理 13406 字符、正文为空，标记 interrupted。

## Token 执行契约与推理档位修复

技能现在明确说明：simulate 自动生成随机 token，并使用 XSim
`-testplusarg RTL_TOKEN_<random>` 注入；模型只需按给定 `$value$plusargs` 格式读取并回显，
不需要从状态获取 token、不自行生成、不设默认值。缺少 token 仍失败，未放宽真实自检判定。

本地测试入口默认 `--reasoning-effort low`，可选 low / medium / xhigh；
`--reasoning-effort default` 省略参数以复现提供方默认。Agent 的其他入口默认不发送
该参数，可显式设置 `AGENT_REASONING_EFFORT`。官方 baseline 原文件和参数保持不变。
因此当前 low 实验不与默认 xhigh 的 baseline 直接组成正式公平增益结果。

推荐复现：

```powershell
python tools/serve_development.py --code-first --reasoning-effort low
```

同一道 AND 门题、同 300 秒期限实测成功：HTTP 墙钟 **83.580 秒**，三轮模型调用
依次为 write_file、simulate、submit；模型自行编写 TB 并在 simulate 中传入四个检查项。
真实 xvlog / xelab / XSim 全部返回 0，覆盖 and_00 / and_01 / and_10 / and_11，
随机 token 回显匹配，4 次比较通过。模型调用分别 15.513 / 46.586 / 11.969 秒，
累计服务端 usage 9262 tokens。没有调用 lint / synth，不声称本次完成综合或官方 L2 评分。

证据 `out/local-cloud-token-fix-20261014/trace.md`、`summary.json`，原始档案
`runs/request-v1t0klxe/out/` 包含模型生成 TB 和工具日志。此前长期思考及超时记录保留，
本次是说明修复与 low 推理的组合实验，不单独归因其中一个变量。

完整回归首跑 56 项，55 项通过，1 项发现此前 trace 拼接遗漏 incomplete_trace_record
标记的兼容问题。已恢复该标记并复验原失败用例及相关 16 项，全部通过；未重复全量
运行。原始完整日志保留在 `out/token-fix-regression-20261014.log`，不将失败首跑记为全通过。
baseline 和九项 Ross 资产完整性预检通过。
# 工具调用协议

Agent 使用 Chat Completions functions 和配对的 assistant/tool 消息。
每轮请求携带 tools、tool_choice=required、parallel_tool_calls=true，允许多个函数调用。
SSE 的 tool_calls 按 index 拼接 arguments，只有完整 finish_reason 和 [DONE]
才允许执行；整轮参数及唯一 ID 校验后，按返回顺序逐项执行并回传结果。
原始流和拼接 trace 都保留调用参数；历史裁剪以一轮调用及其全部结果为单位。
函数定义由 agent/llm/protocol.py 提供，执行权限和参数约束由 Runtime 校验。

