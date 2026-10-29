"""工具处理：重复轮次检测、顺序执行和逐项结果归档。"""
from dataclasses import dataclass
import json
import time
import uuid
from agent.agent_tool_registry import Directive, ToolResult
from agent.agent_tool_registry.observations import model_observation
from agent.agent_tool_registry.events import ArchiveError, CriticalSubscriberError, record_required


@dataclass
class ToolBatchResult:
    """一轮工具的原始观察，以及可能的流程终止原因。"""
    observations: list
    stop_reason: str | None = None
    feedback: dict | None = None


def is_repeated_batch(runtime, parsed_calls):
    """检测连续重复的动作批次；不依赖会随响应变化的调用 ID。"""
    signature = json.dumps([action for action,_ in parsed_calls], sort_keys=True)
    runtime.repeats = runtime.repeats+1 if signature == runtime.last_action else 0
    runtime.last_action = signature
    return runtime.repeats >= 3


def execute_tools(runtime, parsed_calls, end, record, call):
    """依次执行同轮工具，逐项记录结果；终止后的其余调用记录为未执行。"""
    observations, reason, feedback = [], None, None
    for action, native_call in parsed_calls:
        started = time.monotonic()
        invocation_id = uuid.uuid4().hex
        execution_error = None
        invoked = False
        if reason is None and time.monotonic() >= end:
            reason = 'deadline'
        if reason is None and runtime.tool_calls >= runtime.max_tools:
            reason = 'resource_budget'
        if reason is not None:
            result = ToolResult({'status':'not_executed', 'message':'Execution stopped before this call.'})
        elif feedback is not None:
            result = ToolResult({'status':'not_executed', 'message':'Paused for automatic RTL diagnostics. Repair the source before retrying this action.'})
        else:
            try:
                result = runtime.invoke(action, invocation_id=invocation_id)
                invoked = True
            except ArchiveError:
                raise
            except CriticalSubscriberError as exc:
                result = exc.result or ToolResult({'status':'tool_error', 'error':type(exc).__name__, 'message':str(exc)[:1500]})
                execution_error = {'error':type(exc).__name__, 'message':str(exc)[:1500],
                                   'subscriber_id':exc.subscriber_id, 'subscriber':exc.subscriber}
                reason = 'critical_subscriber_error'
            except Exception as exc:
                result = ToolResult({'status':'tool_error', 'error':type(exc).__name__, 'message':str(exc)[:1500]})
            if result.directive == Directive.SUBMIT:
                reason = 'submitted'
            elif result.directive == Directive.STOP_RESOURCE:
                reason = 'resource_budget'
        record_required(record, 'action', call=call, tool_call_id=native_call['id'], invocation_id=invocation_id, decision=action,
               decision_format='native_tool', observation=result.data, directive=result.directive.value,
               execution_error=execution_error, sec=time.monotonic()-started)
        try:
            if invoked and result.status not in ('cancelled','not_executed','tool_error'):
                observation = runtime.registry.observe(action['tool'], result)
            else:
                observation = model_observation(result.data)
        except ArchiveError:
            raise
        except Exception as exc:
            if isinstance(exc, CriticalSubscriberError) and reason is None:
                reason = 'critical_subscriber_error'
            record_required(record, 'tool_observation_error', invocation_id=invocation_id,
                            name=action.get('tool'), error=type(exc).__name__, message=str(exc)[:1500])
            observation = model_observation({'status':'tool_error', 'message':'Could not format tool observation.'})
        observations.append(observation)
        if result.directive == Directive.RETURN_TO_MODEL:
            feedback = observation
    return ToolBatchResult(observations, reason, feedback)
