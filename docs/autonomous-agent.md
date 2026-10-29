# 自主 RTL Agent 实现与验证 — 2026-10-12

2026-10-17 研发更新：原生函数调用、成对的近期对话历史、通用自检片段框架、诊断压缩及补丁式 TB 修复；
当前正负实验、默认与实验配置、未完成项见 [研发迭代记录](development-loop.md)。

API与模块化流程全量回归 115 项通过；脚本模型经异步 HTTP
驱动 Vivado，在 6 轮、每轮 2 个调用中完成反例检测、补丁修复、原测试复验、lint、
综合和提交。该验证覆盖执行链路，真实模型能力另行评测。

唯一 Agent 入口为自主决策循环。
模型每轮通过 Chat Completions 原生函数调用选择一项或多项工具，控制器执行工具，把真实观察和工作区/候选
摘要交回模型。它可选择继续分析、读文件、创建 RTL/TB、修改、验证、修复或提交。
工具结果使用 role=tool 和对应 tool_call_id 逐项回传；历史以一轮 assistant 及其全部结果为单位保留。
同轮调用按返回顺序执行；依赖前次结果才能确定的动作由模型在后续轮选择。

## 已实现的纵向闭环

`agent/main.py` 转交 `autonomous.solve`，后者只编排解题流程。

| 模块 | 职责 |
| --- | --- |
| agent/llm/ | 模型连接配置、请求生成、SSE/普通响应读取、原生调用协议及 token 记账 |
| agent/loop/context.py | 系统提示、任务与证据状态、观察压缩及整轮历史裁剪 |
| agent/loop/tools.py | 批次重复检测、顺序执行多调用、逐项错误反馈和终止后的未执行结果 |
| agent/loop/lifecycle.py | 私有运行状态、任务准备、请求限制、模型异常重试、进程清理及最终归档 |
| agent/tooling/ | 工具调度、独立候选仓库、文件能力和真实 Vivado 操作 |
| serve/http/ / serve/tasks/ | HTTP 协议、后台队列、隔离执行和产物归档 |

各包的依赖方向与修改入口见 [代码结构](architecture.md)。

| 工具 | 实现 |
| --- | --- |
| list/read/write/patch_file | 相对路径、原子写入、唯一匹配补丁；仅 src/tests/notes，reports 只读 |
| set_contract / amend_test | 不要求题面逐字引用；simulate 可直接携带检查项建立不可变契约，无需单独一轮 set_contract；首轮仿真后可在任何结果下用 amend_test 说明原因修订 TB，撤销旧仿真证据和待处理反例，再复测 |
| check_interface | 既有保守接口检查，暴露实际覆盖端口数，不冒充完整 HDL parser |
| compile | 实际 xvlog + xelab，top 必须符合公开规格 |
| lint | 实际 synth_design -lint，规范七列 CSV，核对工具计数 |
| simulate | 实际 xvlog/xelab/XSim；有限 100 us、进程墙钟限制、随机结束标记、契约 check ID 和非零检查数 |
| synth | 实际 OOC synth_design，DUT 单独综合，默认 ZU3EG；不综合自检 TB |
| analyze_log | 原始工具诊断 ID 与首批因果信息，完整原始日志另存 |
| search_docs | 冻结 Markdown 知识的按需本地检索，不访问文档链接或外网 |
| submit | 返回证据排序最好的版本，不强制返回最后一次修改 |

测试契约及 TB 内容来自运行时模型动作，生产代码不包含题目答案或模板测试答案。
测试文件中的饱和计数器仅是公开规格验收夹具，不进入提交镜像。
模型请求携带 tools、tool_choice=required、parallel_tool_calls=true；
响应通过 assistant.tool_calls 提供函数名称及参数，控制器解析后交由受控执行器执行。
温度0、top_p 1；上下文与输出上限读取 `manifest.json.context`，模型名称和权重未更换。

## Ross：真正接入与借鉴

已接入固定上游提交 `2cdc9eef1b6b5b17fa45f5e4d468e5483a1c92de` 的：

- **真实 Ross ASCII lint 解析器**：`agent/vendor/ross_parse_lint_report.py`，在 EDA 适配器中实际调用。
- 六份诊断指南：ASSIGN-1/2、INFER-1/2、RESET-3、CLOCK-1，加 resolution 总览，供 `search_docs` 实际检索。
- 原始 MIT 许可证/版权、来源 URL 和 SHA-256 清单；preflight 核验全部九个资产。

本机 Vivado 2026.1 Build 6511674 实测仍输出 ASCII 表格，即使输出文件后缀是 `.csv`。
因此按实际格式选择 Ross 解析器或原生 CSV，不只依据版本号。解析失败/计数不一致不填写通过。
固定版解析器另有零诊断时把 waiver 页脚当数据的问题；适配层去掉已知非表格页脚，
保持原报告与上游脚本不变，并加入回归测试。
参考：[上游 lint 技能](https://github.com/Xilinx/ross-ai-assistant/blob/2cdc9eef1b6b5b17fa45f5e4d468e5483a1c92de/skills/vivado-rtl-lint/SKILL.md)。

借鉴的是诊断优先级、根因归类、测试契约、有限仿真、明确 PASS 与环境故障分离。
执行器是本项目受控 CLI 适配，不声称完整执行 Ross 的交互式技能。
**未接入 Ross MCP 二进制、Weaviate、向量 embedding 或完整 AMD RAG 数据库**。
目前不需要 GUI、人工审批、SSH、在线检索或自动下载。

## 隔离、证据和故障恢复

HTTP 为每题生成随机目录，忽略 task_id/nonce 的路径含义，只写入公开 prompt/interface。
模型只能操作四个可见子目录，禁止绝对路径、路径穿越、符号链接/junction 和报告写入；
没有 shell 或任意 Tcl 工具。禁用 HDL 外部文件任务、DPI/PLI/VPI、include 和宏隐蔽调用。
安装库与必要授权属于可信工具配置；工具 HOME/TMP 指向本题内部，环境白名单不带 API 令牌。

源代码版本以 SHA-256 存入 `candidates/`，每版有独立证据表。源修改自然产生新版本，
不会继承旧版通过结果；测试台修订会使所有旧仿真证据失效并保留修订依据。
同一候选的已知反例不能被后一次无反例结果悄悄抹去，不能在 DUT_FAIL 后放宽测试预期。
证据排序先避开接口错误/已知反例，再考虑自检、综合、编译和 lint；UNKNOWN 不等于 PASS。

每次模型请求/完整回复、决策/观察、每个外部工具的启动/结束均写入 trace。
工具源快照、TB、Tcl、原始日志、结果 JSON 和最终 summary 保存在请求档案。
最佳 solution.v 原子更新；模型异常、预算耗尽或工具失败仍保留已保存版本。
工具进程组登记在控制器私有目录，内外两层清理；HTTP 硬截止可回收工具并读回候选。
被中断的最后一条 trace 原始字节留在档案中，HTTP 返回有效记录并标明中断。

文件能力限制不是内核级多租户沙箱。正式提交仍须只挂载本题可写目录、只读运行库/授权，
不挂载官方测试台/参考答案；在目标 Linux 镜像验证容器网络/挂载权限与进程清理。
自检检查次数和结束标记能识别无结果/误判成功，但不能证明模型生成的 oracle 完整或正确。

## 启动与预算

先启动已核验的本地 27B llama.cpp 模型，再启动 Agent；正式运行所需文件均须预置。
时间、额度及资源计数由控制器管理，模型上下文只提供任务、代码状态、工具结果及验证证据。

```bash
export LLM_BASE_URL=http://127.0.0.1:8000/v1
export MODEL_NAME=ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF
export XILINX_VIVADO=/tools/Xilinx/2026.1/Vivado
export FPGACHINA_TOKEN='<competition-token>'
export RUN_ARCHIVE_DIR=/var/tmp/rtl-agent/runs
bash serve/entrypoint.sh
# 容器退路的单题 CLI 只复制公开题面，由评测方控制进程组期限
bash run.sh /path/to/task /tmp/result
```

| 配置 | 默认值 |
| --- | --- |
| DEADLINE_S | CLI 900 秒；HTTP 用请求 deadline_s，含排队时间，并预留终止与回收时间 |
| AGENT_MAX_MODEL_CALLS | 24 次决策，作为资源上限，不是固定修复轮数 |
| 累计 token 用量 | 无上限，旧 AGENT_MAX_TOTAL_TOKENS 不再生效；优先按 usage 记账，缺少 usage 时估算输入/生成文本 token，并在 trace 标明来源；trace 的 max_total_tokens 为 null |
| AGENT_MAX_ACTIONS / AGENT_MAX_TOOL_RUNS | 64 项动作 / 24 个外部进程 |
| AGENT_TOOL_SECONDS / AGENT_TOOL_TIMEOUT_S | 累积 180 秒 / 单进程 45 秒，均受剩余 deadline 限制 |
| AGENT_TOOL_MEMORY_MB | Linux 每进程地址空间 8192 MiB，继承 CPU/文件大小限制；Windows 尚无 Job Object 内存限制 |
| AGENT_ARTIFACT_MB / RUN_ARCHIVE_MB | 每题产物监测 64 MiB / 公共启动环境的档案 admission 默认 8192 MiB（直接运行 Python 而不加载环境时为 1024 MiB）；不自动删除证据 |
| AGENT_CONTEXT_TOKENS | 默认值见 manifest.context.max_model_len，与推理启动器共享；优先按本地模板/tokenizer 计算输入，不支持时使用统一字符估算 |
| AGENT_MAX_OUTPUT_TOKENS | 默认值见 manifest.context.max_output_tokens；实际请求还受本轮上下文余量限制，与此前累计用量无关 |
| AGENT_MAX_RESPONSE_BYTES | 默认值见 manifest.agent.max_response_bytes，包含SSE分片封装开销 |
| AGENT_INPUT_CHAR_LIMIT | 默认值见 manifest.agent.input_char_limit；仅用于整轮裁剪旧历史，不作为请求退出门槛；本轮输入占满上下文时也裁剪历史，无历史可裁剪且无输出空间才以 context_budget 退出 |

Vivado/xelab 默认两线程。磁盘使用是轮询监测，正式环境还应配额；全档案满时拒绝新任务，
由开发侧换新目录或归档。模型仅接受本机 HTTP 地址，跨机研发应通过 loopback 隧道。
档案目录由单个服务独占：首次使用核对总大小，之后按已完成请求增量记账，避免每题
重扫历史文件导致连续请求变慢。不要由其他进程向该目录添加产物。
Windows 验证不代表 Linux rlimit、断网许可或正式 W7900 显存已验证。

## 实测与复现

当前用户确认没有可用模型实例。因此实际工具验收使用明确标识的 **脚本化模型决策测试端**，
经真实 HTTP 驱动生产控制器、真实 Vivado 2026.1，不把它称为真实 LLM 自主能力测量。

```powershell
python -m unittest discover -s tests -v
python tools/preflight.py
```

验收新题为 4 位饱和计数器，测试契约包含同步复位优先级、使能保持和饱和边界。
实际轨迹包括建契约/计划、写 RTL/TB、编译、仿真、读反例、补丁、重测、lint、综合和提交。
初始 DUT 在第 18 次比较（176 ns）期望 15、实际 0；仅修 DUT 的饱和条件，不修改 TB。
修复后 22 次实际比较通过，并覆盖三个契约 check ID。详细最终测量以
`out/autonomous-vivado-20261012-06/report.json` 为准，失败的前期启动/格式诊断也保留。
最终完整验收耗时 **83.469 秒 / 300 秒**，12 次脚本化模型决策、10 个真实工具进程，
保存两版候选；最终自检 PASS、lint 0 条、OOC 综合通过。档案约 14.44 MiB。
此前版本完整回归 **42 项通过，82.219 秒**，历史日志为 `out/autonomous-tests-final2-20261012.txt`。
移除固定轮次控制器后，服务测试已全部迁移到自主动作协议，200 次连续请求交替覆盖
自主 Agent 和原样 baseline；仍使用合成模型回复，不代表真实模型稳定性评测。
基线文件与官方逐字节一致，全部九项 Ross 资产 SHA-256 核验通过。
移除旧控制器后的完整回归 **36 项通过，94.915 秒**，包括 200 次自主 Agent/baseline
交替请求；日志为 `out/autonomous-only-tests-final-20261012.txt`。

## 配对与消融

配对样本、官方 reference 检查和评分由外部评测器负责。
每组固定同一模型/服务参数/源码清单，服务端分别启动以下配置：

| 组 | 服务配置 |
| --- | --- |
| 自主 Agent 全工具 | 清空禁用列表 |
| 去 lint | AGENT_DISABLED_TOOLS=lint |
| 去自检 | AGENT_DISABLED_TOOLS=amend_test,set_contract,simulate |
| 去文档检索 | AGENT_DISABLED_TOOLS=search_docs |

每组同时跑原样 baseline；比较官方 L0–L3、pass@1/pass@5、墙钟、工具/模型调用和资源。
尚无本版真实模型配对结果或准确率增益。下一步是恢复模型实例/目标平台后跑这些实验，
验证 W7900 容器构建、32GB 上下文峰值、离线启动/授权和数百请求稳定性，再决定 MCP/RAG 扩展。
