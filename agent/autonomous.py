"""Agent 流程编排：连接模型、组织上下文、执行工具，并统一处理异常和归档。"""
import os
import time

from agent.loop.context import Conversation
from agent.loop.lifecycle import RunControl, prepare_task, record_start, handle_model_error, finalize
from agent.llm.client import ModelClient
from agent.tooling.runtime import Runtime
from agent.loop.tools import execute_tools, is_repeated_batch
from agent.trace import export_trace
from agent.agent_tool_registry.events import ArchiveError


def solve(task_dir, out_dir):
    """编排一次解题；模型、上下文、工具执行和退出处理分别由独立模块负责。"""
    control = RunControl()
    prompt, interface, out, record = prepare_task(task_dir, out_dir)
    runtime = Runtime(prompt, interface, out, control.end, record)
    record = runtime.events.record
    model = ModelClient()
    if not model.is_local():
        record('agent_done', reason='nonlocal_model_endpoint', official_verified=False)
        return
    conversation = Conversation(prompt, interface, os.environ.get('AGENT_CODE_FIRST') == '1')
    record_start(control, model, conversation, runtime, out, record)
    try:
        while control.can_continue(runtime):
            body = conversation.build_request(model, runtime)
            if not control.accept_request(body):
                break
            control.calls += 1
            try:
                result, tokens = model.complete(body, out, record, control.calls,
                                                control.end, control.end-time.monotonic())
                control.account(tokens, model.accounting_source)
                export_trace(out)
                parsed_calls = model.parse_actions(result)
            except ArchiveError:
                raise
            except Exception as exc:
                conversation.observation, stopped = handle_model_error(exc, control, out, record)
                if stopped:
                    break
                continue
            control.faults = 0
            if is_repeated_batch(runtime, parsed_calls):
                control.reason = 'stagnation'
                break
            batch = execute_tools(runtime, parsed_calls, control.end, record, control.calls)
            kwargs = model.generation.get('chat_template_kwargs', {})
            preserve = kwargs.get('enable_thinking') is True and model.preserve_thinking
            conversation.remember(parsed_calls, batch.observations, batch.feedback,
                                  result['choices'][0]['message'] if preserve else None)
            if batch.stop_reason is not None:
                control.reason = batch.stop_reason
                break
    except ArchiveError:
        control.reason = 'archive_error'
        raise
    finally:
        finalize(runtime, out, control, record)
