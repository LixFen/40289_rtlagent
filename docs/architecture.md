# 代码结构与阅读顺序

运行时只依赖 Python 标准库。题目通过 HTTP 接收，由后台 worker 调用 Agent；
生产模型通过本机推理服务接入，开发云桥位于 `tools/serve_development.py`。

## 按职责找代码

```text
agent/
  main.py                 单题 worker 入口
  cli.py                  Linux worker 的公开输入隔离入口
  autonomous.py           solve：编排一次解题
  paths.py                安装资源根目录
  config.py               从manifest/环境读取模型连接与运行参数，供Python与Shell共用
  processes.py            共享进程组登记、终止与回收
  trace.py                原始事件与 SSE 的归并、导出
  llm/
    client.py             模型配置、请求及响应适配
    protocol.py           引用统一工具 schema、解析参数与配对 tool 消息
    streaming.py          SSE 增量读取和分片组装
  loop/
    context.py            Conversation：提示词、观察及整轮历史
    lifecycle.py          RunControl：运行状态、异常处理、最终归档
    tools.py              一批工具的顺序执行、错误反馈与重复检测
  tooling/
    runtime.py            Runtime：装配组件、汇总状态、校验权限并派发工具
    verification.py       VerificationState：共享验证状态和记录入口
    candidates.py         CandidateStore：候选版本、证据、排序和交付
    workspace.py          文件访问、原子写入、测试契约与冻结
    checks.py             公开接口的保守静态检查
    testbench.py          模型自建测试片段的受控封装
    eda.py                真实 Vivado 编译、仿真、lint、综合
  vendor/                 固定版本第三方解析器
  agent_tool_registry/
    base.py               Tool 生命周期、ToolContext、ToolResult 和明确停止决策
    registry.py           schema、执行生命周期、控制钩子和原始结果归档
    events.py             同步事件、只读快照和订阅错误处理
    observations.py       模型观察的默认投影与日志压缩
    tools/                工具定义和行为；仅扫描这个安装目录
serve/
  api.py                  HTTP 启动入口
  health.py               模型声明、推理配置与 GPU 遥测核对
  http/
    handler.py            鉴权、路由、JSON 响应
    contract.py           投题字段及期限校验
    server.py             HTTP 服务器和后台队列装配
  tasks/
    queue.py              JobManager：nonce 去重、状态、期限和取消
    worker.py             独立求解进程的启动、监督及结果读取
    archive.py            每题随机目录与归档容量核算
  startup.py              常驻服务的幂等启动
  config.sh               Shell启动器的共享配置加载桥接
  telemetry.py            GPU 遥测采样
  eda_cleanup.py          过期 EDA 目录回收
  prepare_image.py        镜像构建时检查官方 baseline
```

## 从哪开始读

解题流程从 `agent/autonomous.py:solve` 开始：准备运行、构建上下文、请求模型、
解析原生调用、执行工具、保存观察、最终收尾。模型适配、上下文裁剪和工具权限
分别在自己的模块中处理。`solve` 不负责 HTTP 或进程启动。

一次 HTTP 投题依次经过：

```text
Handler → validate_payload → JobManager.submit → 202 + job_id
                                  ↓ 后台线程
                        worker.execute → agent.main → solve
                                  ↓
GET /v1/jobs/{job_id} ← JobManager.get ← 已保存的代码与 trace
```

`worker.execute` 只组织执行步骤：申请执行槽、创建隔离目录、准备公开输入、
启动并等待子进程、读取产物。`run_worker` 负责进程生命周期；`collect_result`
负责读取已经落盘的代码和有效 JSONL。硬超时仍保留之前保存的代码。

## 修改边界

| 想改什么 | 首先看哪里 |
| --- | --- |
| 模型连接、生成参数、SSE | `agent/llm/` |
| 提示词加载、历史裁剪、工具批次执行 | `agent/loop/` |
| 多轮主流程 | `agent/autonomous.py` |
| 模型连接、上下文、输出、输入裁剪和响应大小默认值 | `manifest.json`，读取规则见 [运行配置](configuration.md) |
| 工具定义、注册、生命周期和事件 | `agent/agent_tool_registry/`，见 [工具扩展指南](tool-registry.md) |
| 工具权限、共享服务装配、状态汇总 | `agent/tooling/runtime.py` |
| 文件工具与编辑后的候选更新 | `agent/agent_tool_registry/tools/files.py` |
| 候选排名、反例证据保留 | `agent/tooling/candidates.py` |
| 测试台冻结、路径权限 | `agent/tooling/workspace.py` |
| 测试片段封装 | `agent/tooling/testbench.py` |
| 验证编排、TB 修复 | `agent/agent_tool_registry/tools/verification.py` |
| 测试台准备及反例处理 | `agent/agent_tool_registry/tools/verification.py` 的 Simulate |
| 共享验证状态存储 | `agent/tooling/verification.py` |
| 日志分析、知识片段检索 | `agent/agent_tool_registry/tools/documents.py` |
| Vivado 参数、日志与产物 | `agent/tooling/eda.py` |
| HTTP 字段、状态码、鉴权 | `serve/http/` |
| 排队、去重、截止时间 | `serve/tasks/queue.py` |
| 子进程隔离、超时终止、结果回收 | `serve/tasks/worker.py` |

HTTP 层依赖后台任务，后台任务启动 Agent。Agent 不导入 `serve`。
`Runtime` 装配共享服务，`agent.agent_tool_registry` 自动发现工具并执行生命周期；工具不反向依赖 `Runtime`。
文件编辑先保存源码，再登记候选并更新交付版本；验证组件持有最近仿真结果和待解决反例，
候选仓库负责各版本证据的保留与排序。`loop` 负责执行工具批次及反馈观察。
资源位置统一通过 `agent.paths.ROOT` 获取，避免模块移动影响知识库和提示词路径。
服务启动入口为 `python -m serve.api`；开发云模型服务由 `tools/serve_development.py` 启动。
`agent.main`、`agent.cli` 和 `run.sh` 供服务内部 worker 使用。
主动投题和评分由外部评测方负责，`tests/http_client.py` 仅供协议回归和内部集成夹具使用。

## 验证

```text
python -m unittest discover -s tests -v
python tools/preflight.py
```

单元/集成测试覆盖原生调用配对、流式 trace、候选证据、HTTP 异步协议、
排队期限和进程清理。`test_worker.py` 用真实子进程验证先保存源码再超时，
Agent 与 baseline 两种模式均须返回保存代码，并释放执行槽。
Vivado 验证使用脚本模型驱动真实工具链，覆盖失败、修复、原测试复验及提交，
用于检查执行链路；真实模型能力和 R9700 离线验收另行验证。
