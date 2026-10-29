"""流程支持：运行状态、任务准备、异常处理及最终归档。"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import time

from agent.trace import export_trace
from agent.processes import cleanup
from agent.agent_tool_registry.events import record_required
from agent.config import model_call_limit


@dataclass
class RunControl:
    """控制器私有计数与终止状态，不作为模型上下文。"""
    started: float = field(default_factory=time.monotonic)
    calls: int = 0
    total_tokens: int = 0
    actual_tokens: int = 0
    estimated_tokens: int = 0
    faults: int = 0
    reason: str = 'model_budget'

    def __post_init__(self):
        """读取运行限制并计算截止时间。"""
        self.budget = float(os.environ.get('DEADLINE_S', '900'))
        self.end = self.started+self.budget-min(1.0, self.budget/10)
        self.max_calls = model_call_limit()

    def can_continue(self, runtime):
        """在下一轮前检查调用数、截止时间及外部资源限制。"""
        if self.calls >= self.max_calls:
            return False
        if self.end-time.monotonic() <= 0.1:
            self.reason = 'deadline'
            return False
        if runtime.tool_calls >= runtime.max_tools:
            self.reason = 'resource_budget'
            return False
        return True

    def accept_request(self, body):
        """模型适配器已按本轮输入预留上下文；累计用量仅用于归档。"""
        if body['max_tokens'] <= 0:
            self.reason = 'context_budget'
            return False
        return True

    def account(self, tokens, source):
        self.total_tokens += tokens
        if source == 'usage':
            self.actual_tokens += tokens
        else:
            self.estimated_tokens += tokens


def prepare_task(task_dir, out_dir):
    """准备输出文件与事件记录器，读取公开题面及可选接口。"""
    task, out = Path(task_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out/'solution.v').write_text('', encoding='utf-8')
    trace = out/'trace.jsonl'
    trace.write_text('', encoding='utf-8')

    def write_event(tool, **fields):
        """追加一条带墙钟时间戳的原始 trace 事件。"""
        with trace.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(ts=time.time(), tool=tool, **fields), ensure_ascii=False)+'\n')

    def record(tool, **fields):
        record_required(write_event, tool, **fields)

    prompt = (task/'prompt.txt').read_text(encoding='utf-8')
    interface = (task/'interface.txt').read_text(encoding='utf-8') if (task/'interface.txt').exists() else ''
    return prompt, interface, out, record


def record_start(control, model, conversation, runtime, out, record):
    """归档运行配置；这些控制器统计只进入 trace，不进入模型消息。"""
    record('agent_start', schema_version=3, policy='autonomous', budget_s=control.budget,
           max_model_calls=control.max_calls, max_total_tokens=None,
           max_output_tokens=model.max_output, context_tokens=model.context_tokens,
           tool_protocol='chat-completions-functions', code_first=conversation.code_first,
           assistant_history=('tool_calls_and_reasoning' if model.preserve_thinking else 'tool_calls_only'),
           streaming=model.streaming, reasoning_effort=model.generation.get('reasoning_effort') or model.reasoning_effort or None,
           action_limit=runtime.max_tools, disabled_tools=sorted(runtime.disabled), archive_id=out.parent.name)


def handle_model_error(exc, control, out, record):
    """记录模型或协议异常，决定重试或停止，返回下一轮可使用的错误观察。"""
    control.faults += 1
    observation = {'status':'model_error', 'error':type(exc).__name__, 'message':str(exc)[:1200]}
    record('model_error', call=control.calls, observation=observation)
    export_trace(out)
    if time.monotonic() >= control.end:
        control.reason = 'deadline'
    elif 'budget' in str(exc).lower():
        control.reason = 'resource_budget'
    elif control.faults >= 3:
        control.reason = 'model_errors'
    else:
        return observation, False
    return observation, True


def finalize(runtime, out, control, record):
    """清理工具进程、选择证据支持的候选并写入摘要和最终 trace。"""
    cleanup(out)
    best = runtime.candidates.select_best()
    summary = {'reason':control.reason, 'model_calls':control.calls, 'tool_actions':runtime.tool_calls,
               'total_tokens':control.total_tokens, 'actual_tokens':control.actual_tokens,
               'estimated_tokens':control.estimated_tokens, 'elapsed_s':time.monotonic()-control.started,
               'selected_sha256':best['sha256'] if best else None,
               'evidence':best['evidence'] if best else {}, 'official_verified':False}
    (out/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    record('agent_done', **summary)
    export_trace(out)
