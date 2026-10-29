# 工具注册表、生命周期和事件

`agent/agent_tool_registry/` 是工具定义与执行入口。模型 schema 和 Runtime 派发都从同一份
定义生成。现有 14 个工具已迁移到 `tools/files.py`、`tools/verification.py`、
`tools/documents.py` 和 `tools/submit.py`。新增工具可以单独建一个模块。

## 新增或修改工具

在 `agent/agent_tool_registry/tools/` 添加模块，导出 `TOOLS = (YourTool,)`，其中每项是无参
构造的 `Tool` 子类。启动只扫描这个安装包目录，不扫描题目、工作区或任意外部插件目录。
每题重新创建工具实例、上下文、钩子和事件总线；工具模块按名称排序，schema 按工具名排序。
重复工具名、缺少实现、未知 schema 关键字会直接失败。

```python
from agent.agent_tool_registry import Tool, ToolResult
from agent.agent_tool_registry.schema import TEXT, parameters

class ReadNote(Tool):
    name = 'read_note'
    description = 'Read a note from the task workspace.'
    parameters = parameters({'path': TEXT}, ('path',))

    def execute(self, ctx, args):
        # 路径、读取窗口等限制仍由统一工作区服务执行。
        return ToolResult({'path': args['path'], 'content': ctx.workspace.read_text(args['path'])})

TOOLS = (ReadNote,)
```

新增模块后更新机器可读交付清单，无需手写另一份工具表：

```bash
python -m agent.agent_tool_registry                  # 查看自动发现的工具
python -m agent.agent_tool_registry --write-manifest # 生成 manifest.agent.tools
python tools/preflight.py                     # 检查声明和注册表一致
```

schema 支持当前需要的 object/array/string/integer/number/boolean、required、enum、
additionalProperties=false、数值和长度边界。它不是完整 JSON Schema 实现；未支持的
关键字会在注册时拒绝。参数类型、必填项、额外参数会在业务代码运行前校验。

## 固定生命周期

```text
名称与 schema 校验 → 强制权限/调用预算 → 工具 validate
  → tool.started → before_prepare 钩子
  → prepare → execute → apply_result → after_result 钩子
  → cleanup（finally）→ 原始 tool_result 归档 → tool.completed
  → action 原始归档 → observation → 模型历史
```

简单工具只需 execute；其他方法继承默认实现：

| 方法 | 用途 |
| --- | --- |
| configure(ctx, add_hook) | 每题装配一次，注册事件订阅和控制钩子 |
| validate(ctx, args) | 业务条件校验，不能替代强制 schema/权限检查 |
| prepare(ctx, args) | 准备执行输入，默认直接返回 args |
| execute(ctx, prepared) | 执行动作并返回 ToolResult |
| apply_result(ctx, prepared, result) | 更新候选、证据与反例等共享状态 |
| cleanup(ctx, prepared) | 一旦进入 prepare 就保证调用，准备失败时 prepared 为 None |
| observation(ctx, result) | 生成模型可见结果；收到副本，不能覆盖原始证据 |

ToolContext 只提供 workspace、candidates、eda、verification 和 events，不暴露
RunControl、HTTP 队列或完整 Runtime。工具及扩展是预置的可信 Python 代码，不是
针对恶意插件的安全沙箱。文件工具必须通过 workspace，外部进程通过统一 EDA 服务运行；
路径限制、冻结、测试契约和资源回收继续由这些服务执行。

工具行为放在注册包：ReadFile 自己校验 offset/limit、切分读取窗口并构造返回值；
WriteFile/PatchFile 处理文件产物响应、候选更新和下一步提示。Workspace 只提供完整文本
`read_text`、受保护的原子 `write_text`、目录枚举和不可变契约，不再返回工具结果。
本次保留 offset/limit 的字符语义；以后改为行窗口只需修改 ReadFile 及对应测试。
Simulate 负责测试台准备、诊断反馈和反例状态转换；VerificationState 仅保存共享状态和
受保护的记录入口。测试台在首次仿真后通过 amend_test 说明原因修订，不限制上一轮结果；修订撤销旧仿真证据和待处理反例，必须重新验证。

apply_result 失败不会回滚已经完成的文件/EDA 操作；trace 会保留已有原始结果并记录失败。
cleanup 失败同样使调用失败；同时存在执行与清理错误时保留原始异常，另记清理错误。
原始证据归档失败以 ArchiveError 中止运行，不能降级成普通可重试工具观察。
候选仓库、EDA、验证状态、模型请求和批次动作共用受保护的记录入口；其中任一必需事件
写入失败都不能继续执行后续工具或重试模型，求解汇总标记为 archive_error。
observation 失败保留原始证据并记录 tool_observation_error，模型收到简短错误。

## 明确的流程控制

ToolResult.data 保留原有工具结果结构，status 缺省为 ok，artifacts 缺省为空列表。
控制流程只读取 Directive：CONTINUE、SUBMIT、STOP_RESOURCE。普通结果中的 done=true
不再隐式结束执行；submit 工具显式返回 SUBMIT。批次停止后的剩余调用保留原 ID，
统一反馈 not_executed，保持 assistant/tool 消息配对。

before_prepare 可取消本次调用或请求停止；after_result 只能请求停止，不能撤销已经
执行的动作。钩子按注册顺序同步执行，第一个取消/停止决策生效，后续钩子不再运行。
每次派发先固定钩子快照；钩子运行时新增的同阶段钩子，从下一次派发起才参与。
参数、权限、预算和业务校验始终发生在控制钩子之前。取消调用也消耗一次动作额度。

工具可在自己的模块中安装扩展，不必修改 Runtime：

```python
from agent.agent_tool_registry import Directive, HookDecision

def configure(self, ctx, add_hook):
    def before_prepare(ctx, call):
        if call['tool'] != self.name:
            return None
        # 在这里检查本工具特有的先决条件。
        if not ctx.workspace.listing():
            return HookDecision(cancel=True, reason='No input files available.')
        return None

    add_hook('before_prepare', before_prepare)
```

钩子接收只读调用快照，通过 HookDecision 返回取消/停止意图；不能伪造 SUBMIT 或
替换原始结果。业务钩子异常使本次调用失败。主循环、工具和事件不允许递归调用。
此版本不提供异步钩子、任意优先级或从模型输入加载扩展。

## 事件通知

```python
def configure(self, ctx, add_hook):
    def on_counterexample(event):
        # event.payload 是递归只读快照；在此做统计或界面通知。
        pass

    ctx.events.subscribe('verification.counterexample_found', on_counterexample)
```

事件包括 tool.started、tool.completed、tool.failed、candidate.updated、
verification.counterexample_found、verification.counterexample_resolved。
工具事件的 payload.name 是工具名，tool.failed 的 payload.phase 标明失败阶段。
候选和反例先完成状态更新，再发送对应通知。订阅者按注册顺序同步执行，不获得控制器
上下文，不应返回控制决策。普通订阅失败记录 subscriber_error；critical=True 的订阅
失败以 CriticalSubscriberError 结束本次调用并停止当前批次：若工具已经产生 SUBMIT 或
STOP_RESOURCE，保留原始结果及终止指令；否则以 critical_subscriber_error 停止。
后续工具反馈 not_executed，不会因异常被转成 CONTINUE 而继续运行。
通知失败不会回滚已提交状态，事件递归发布被拒绝。每次派发先固定订阅者快照，
派发期间新增的订阅者只会参与下一次事件，不能无限延长当前事件。

每次调用生成独立的 invocation_id，关联 tool.started/completed/failed、tool_hook、
tool_result、candidate/EDA/验证组件事件、subscriber_error 和 action。模型原始
tool_call_id 及轮次 call 仍保留在 action 中；即使跨轮复用同一个模型调用 ID，
内部 invocation_id 仍不同。直接调用 registry.invoke 也会自动生成 ID。
订阅者通过 Event.invocation_id 获取关联 ID；subscriber_error 同时记录稳定的
subscriber_id、回调的模块限定名称 subscriber 和 critical 标记。

原始 lifecycle、hook 决策和工具结果均进入 trace；模型只接收工具的 observation。
现有 EDA 日志压缩位于 observations.py，工具可以覆盖 observation，无需修改 context.py。

冻结后的 RTL 写入反馈提供 `next_action`，直接复用原 TB；`simulate` 若仍携带片段或完整
内容，会在写入前拒绝，并给出复测参数；需要修改测试台时，可在任何仿真结果下使用 `amend_test` 并说明原因。
`read_file` 读取 reports 和 `analyze_log` 均返回 `report`：所属候选/测试台哈希、阶段、
结果、当前候选哈希、是否匹配当前源码及当前仿真状态。未知来源显式标记，不从文件名猜测。
同一源码下重复读取同一日志窗口会提示未产生新证据；读取其他窗口不会误报。
片段框架新增 `check_equal(actual, expected, id, inputs)`（值宽不超过 1024 位），失败时
打印期望值、实际值与模型提供的输入说明。旧的 `check(condition,id)` 保持兼容；没有
记录值时反馈明确说明缺失，不推断或伪造输入/输出。

## 验证与交付

保存或 patch `src/solution.sv` 后自动调用 slang `--lint-only`（3 秒上限，并受任务剩余期限约束）。
错误以 `precheck` 返回完整的源码行/列指示诊断（模型反馈限 16000 字符，明确标注截断，完整日志另存）。
编译错误、超时或运行异常产生 `RETURN_TO_MODEL`：暂停同批后续动作，将它们逐项归档为
not_executed，保留所有工具消息配对，并把检查结果作为下一轮 last_observation。
这不是任务终止，模型修复后可以继续；warning 不阻断。slang 通过不代表功能正确，Vivado
编译、仿真、综合和原有检查仍照常进行。缺少 slang 时明确返回 unavailable、checked=false，
不声称通过，也不妨碍使用 Vivado；部署前须安装并检查该依赖。

保存、修订完整 TB 及每次 simulate 前，联合 `src/solution.sv` 和 TB 执行 slang 编译检查，
使用默认 timescale 1ns/1ps，不用 `--lint-only`，以检查端口和任务参数。失败时先返回诊断，
不启动 Vivado；联合检查失败不写入 DUT 的验证成绩。日志绑定 DUT/TB 两份哈希。

固定版本及官方归档 SHA-256 见 `serve/slang-release.json`。先在联网准备环境下载相应归档，
再用 `python tools/install_slang.py <archive> --prefix <directory>` 校验并安装到其 bin 下。
将 bin 加入 PATH，或设置 `SLANG_BIN` 为可执行文件完整路径。镜像需预置该二进制；
运行时不联网、不安装依赖。该检查仍使用 Python 标准库调用独立的 slang CLI。
Dockerfile 会在 slang-build 阶段调用该安装器：固定归档校验、版本和最小 lint 均成功后，
才把安装目录复制到 runtime；无需先手工修改官方基础镜像。

新增工具应测试 schema/实际派发、错误和清理、观察与原始证据分离、权限与预算边界。
有控制扩展时，还要测试 hook 顺序、显式停止和同批工具 ID 配对。
包位于 agent/agent_tool_registry，随现有 agent 的 Docker COPY/白名单、冻结与源码快照一起包含；镜像准备阶段
也会检查 manifest 工具清单一致性。此重构不自动修改技能提示或候选排序，不代表真实
模型会主动选择正确工具，相关能力仍需独立实测。
