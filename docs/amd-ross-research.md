# AMD Ross 调研 — 2026-10-12

后续澄清：最终环境为 R9700 32GB、同机完整 Vivado、运行期离线。
下文的跨机 SSH 限制仅描述开发环境；当前流程见 [自主 Agent](autonomous-agent.md)。
后续已固定上游提交并接入真实解析器与诊断指南，见 [自主 Agent 实现](autonomous-agent.md)；
本文“未安装/未冻结”等表述记录调研当时状态。

本次阅读 AMD 产品页、下载页及官方 Xilinx 仓库中的文档和技能源码。没有安装 MCP、下载大型运行包、连接远端实例或修改 Agent。网页内容来自当日可访问的 `main`；未取得提交 SHA，因此以下是检索快照，不是已冻结的供应链清单。

## 身份、入口和可用性

准确产品名称是 **AMD Ross™ Agentic AI Assistant**。它将 AMD 工具访问、文档检索、专家工作流程和设计示例组合起来，可使用用户自己的客户端及 LLM，并不是必须替换当前本地模型的推理引擎。官方产品页介绍四部分：MCP 服务器、AMD 知识库、Agent Skills、设计示例。[AMD 中文产品页](https://www.amd.com/zh-cn/products/software/ross-agentic-ai.html)

产品页当前指向的正式源码仓库为 **Xilinx/ross-ai-assistant**。仓库中的 `amd-ross-agentic-ai-assistant` 是插件名称；不要根据搜索摘要把它当成当前仓库路径。插件只包含技能，Vivado MCP 必须另装；插件与单独复制技能二选一，避免重复加载。[官方仓库 README](https://github.com/Xilinx/ross-ai-assistant)

仓库 LICENSE 为 MIT。许可允许复用、修改和分发，但须保留版权及许可声明。这不证明下载站的二进制、知识库语料或第三方模型均为 MIT；实际交付还要检查所下载包的条款和 NOTICE。[仓库 LICENSE](https://github.com/Xilinx/ross-ai-assistant/blob/main/LICENSE)

当前下载页列出的版本为 2026.9.1，更新时间 2026-09-29：

| 包 | 页面标示大小 | 页面 MD5 |
| --- | ---: | --- |
| Windows Vivado MCP | 19.25 MB | 28906784c3af1c3a379ace4213ab68b7 |
| Linux Vivado MCP | 22.89 MB | d62b050db4316889a051da4c38661bd3 |
| VS Code 扩展 VSIX | 28.02 MB | 4328c23e5a8c494a7de73e0ab9f4eed6 |
| 本地知识库 + Qwen3-Embedding-0.6B | 4.14 GB | 93053c8c3e5875b8feb42442110e1220 |
| 本地知识库 + EmbeddingGemma-300M | 2.94 GB | 2db7cdc8a3de09790590e697434bcd73 |

下载链接进入 AMD 账户域；本次未完成登录、实际下载或检查包内许可证。发布说明记录 2026.9.1 于 2026-09-30 首次同步发布，两日期分别来自下载页和 changelog。[下载页](https://www.amd.com/zh-cn/support/downloads/ross-agentic-ai.html)、[发布说明](https://github.com/Xilinx/ross-ai-assistant/blob/main/CHANGELOG.md)

## 架构和执行方式

Vivado 路径为客户端 → MCP stdio → 本地服务器 → Tcl → Vivado。官方文档以 Vivado/Vitis 2026.1 为已测试版本；“架构上兼容所有 Vivado”不能当作所有技能在每个版本都已验证。HLS 技能主要直接运行 `v++` / `vitis-run`。[Getting Started](https://github.com/Xilinx/ross-ai-assistant/blob/main/docs/getting-started/README.md)

MCP 提供会话启停与重连、Tcl 执行、状态、日志、历史和任务进度等工具。单个 Vivado 会话的 Tcl 执行须串行，不同会话可以并行；长操作通过状态工具监控。文档搜索 `vivado_doc_search` 属于另一个 MCP 服务器，不是 Vivado 服务器内置工具。其混合检索支持调节语义/关键词权重。Windows 不支持 `vivado_ssh`；Linux 的该工具要求两端相同路径的共享文件系统，因此不能直接替代当前“Windows Vivado + 远端 AMD GPU”的文件传输方式。[MCP 工具参考](https://github.com/Xilinx/ross-ai-assistant/blob/main/docs/reference/vivado-mcp-tools.md)

离线知识库是检索层：Weaviate 1.38.2、llama.cpp embedding 服务、doc-search MCP 0.9.0 三个常驻容器，加一次性快照导入任务。embedding 默认 CPU 运行；查询采用关键词与向量混合检索。包已携带容器镜像、嵌入模型和预计算文档快照，仍需目标环境有 Docker。Qwen 嵌入模型为 Apache 2.0，Gemma 使用 Gemma 条款。[本地知识库文档](https://github.com/Xilinx/ross-ai-assistant/blob/main/docs/local-kb/README.md)

回答 LLM 在知识库外部，可复用已有 OpenAI-compatible 本地服务。AMD 的本地部署指南明确允许跳过已有模型的重新安装，Lemonade 也不是必需依赖；它强调完整运行库和权重的离线转移及 SHA-256 清单。指南的参考平台是 Strix Halo，不能将其 GFX override、上下文和调参直接套给 W7900D。[本地 LLM 指南](https://github.com/Xilinx/ross-ai-assistant/blob/main/docs/local-kb/local-llm.md)

## 实际读过的工作流程

| 技能 | 核心方法 | 对本项目的意义 |
| --- | --- | --- |
| vivado-rtl-lint | `synth_design -lint`；以真实报告为准，计数核对，按规则/文件优先级修复；2026.1 原生 CSV，旧版本有解析器 | 能发现 xvlog 编译之外的锁存器、未驱动等问题；须实测墙钟成本 |
| vivado-rtl-elaboration-analysis | 解析 `[Synth 8-*]`，按严重度和可修复性分级，关联真实文件/行号，区分根因和级联错误 | 可用于可信评测侧的综合失败归因，或只向解题侧返回允许的诊断 |
| vivado-simulate-rtl | 明确测试契约、有限时运行、显式 PASS，记录最早因果错误、种子及波形；区分 design/testbench/environment/timeout/tool_defect | 适合强化本地可信 XSim 评测的证据；编译或退出码 0 不能替代功能正确 |
| hls-optimize | 先测基线；一次一个假设和改动；检查工具报告及原因；昂贵 cosim/实现按阶段启用 | 方法可借鉴，但当前 RTL track 不应引入 HLS 流程 |

源码来源：[RTL lint](https://github.com/Xilinx/ross-ai-assistant/blob/main/skills/vivado-rtl-lint/SKILL.md)、[Elaboration analysis](https://github.com/Xilinx/ross-ai-assistant/blob/main/skills/vivado-rtl-elaboration-analysis/SKILL.md)、[RTL simulation](https://github.com/Xilinx/ross-ai-assistant/blob/main/skills/vivado-simulate-rtl/SKILL.md)、[HLS optimize](https://github.com/Xilinx/ross-ai-assistant/blob/main/skills/hls-optimize/SKILL.md)。仿真技能当前标为 beta / Early Access。

另有 timing methodology、IP 配置、版本控制、ILA/VIO 硬件调试、HLS 架构及 Vitis AI 的编译/量化/自定义算子工作流。它们说明 Ross 是覆盖工程开发生命周期的套件；没有找到它已在本赛事数据集上提高 RTL 解题正确率的证据。

## 建议：先在开发与可信评测侧试点

以下是结合本项目约束的判断，不是 AMD 的现成竞赛集成方案：

1. **优先复用诊断方法**：在独立开发分支验证 `synth_design -lint` CSV 和 `[Synth 8-*]` 结构化解析。保留 xvlog 作为较便宜的首级，只有剩余预算足够时再启用更重检查。必须用真实 Vivado 测量，不能凭文档声称速度或质量提升。
2. **把 XSim 证据规范放在可信评测器**：明确功能 PASS、timeout、环境失败、工具失败，保存版本、源码哈希和首个因果错误。官方测试台及参考答案仍留在模型工作区之外；不得为了使用 Ross 示例而破坏该边界。
3. **先离线准备知识，再讨论在线工具调用**：泛化的错误解释和编码规则可按诊断 ID 加载，避免每题塞入整套技能。完整 RAG 可作为研发辅助或后续可选检索服务；需先取得包、检查语料与规则、冻结版本，再测检索收益。当前自写控制器不会因为复制 `SKILL.md` 就自动获得 MCP 能力，需明确客户端实现和工具 schema。
4. **本机 MCP 小试点**：Windows Vivado 2026.1 与官方已测试版本吻合，独立 MCP 二进制较小，可先验证一份公开 RTL 的 lint/session 生命周期。不要把全部远程执行或硬件编程能力直接放入限时解题循环。
5. **按相同模型做消融评测**：现有 Agent、加诊断规则、再加 lint、最后加 RAG 分别成组；冻结同一模型/生成配置，比较功能正确率、延迟、模型调用次数和工具开销。既不能把 lint clean 当正确，也不能把自生成测试台 PASS 当官方判分 PASS。

中国大陆服务器下载大型包时，优先可达镜像或本机获取再上传。未发现可确认的官方国内 Ross 镜像；不能编造镜像地址。账户限定下载应正常使用获授权账户，不转用来历不明的二进制；用官方 MD5 做下载一致性检查，再建立完整包的本地 SHA-256 清单。后续镜像须同时记录来源、版本及校验值。

## 未验证事项

- 尚未下载或运行 Ross MCP / 本地知识库，没有性能或兼容性实测。
- 未取得 AMD 账户下载包的条款、包内 NOTICE 或更强官方哈希。
- 未证明当前 Qwen3.8-27B-GSQ-RCO 量化模型的 MCP tool-calling 稳定性。
- 未验证离线数据库包的真实展开大小、容器权限及目标平台 Docker 可用性。
- 未冻结上游提交；报告不能代替正式供应链清单或竞赛交付许可审查。
