"""Chat Completions 原生函数定义、动作解析与配对的工具结果消息。"""
import json
from agent.agent_tool_registry import catalog


def tool_definitions(disabled=()):
    """根据工具可用性生成函数参数 schema，执行权限仍由 Runtime 校验。"""
    return catalog().definitions(disabled)


def parse_tool_actions(choice):
    """执行前解析整轮函数调用，校验所有调用 ID 唯一且参数为对象。"""
    if choice.get('finish_reason') != 'tool_calls':
        raise ValueError('Native mode requires a completed function call')
    calls = choice['message'].get('tool_calls')
    if not isinstance(calls, list) or not calls:
        raise ValueError('Response requires function calls')
    parsed, ids = [], set()
    for call in calls:
        if call.get('type') != 'function' or not isinstance(call.get('id'), str) or not call['id']:
            raise ValueError('Function call requires type and nonempty id')
        if call['id'] in ids:
            raise ValueError('Function call ids must be unique within a turn')
        ids.add(call['id'])
        function = call['function']
        args = json.loads(function['arguments'])
        if not isinstance(args, dict) or not isinstance(function.get('name'), str):
            raise ValueError('Function requires a name and object arguments')
        parsed.append(({'tool':function['name'], 'args':args}, call))
    return parsed


def history_turn(calls, observations, assistant_message=None):
    """回放配对工具消息；启用思考保留时同时回传原始推理。"""
    assistant = {'role':'assistant','content':None,'tool_calls':calls}
    if assistant_message:
        reasoning = assistant_message.get('reasoning_content') or assistant_message.get('reasoning')
        if isinstance(reasoning, str) and reasoning:
            assistant['reasoning_content'] = reasoning
    return [assistant] + [
        {'role':'tool','tool_call_id':call['id'],'content':json.dumps(observation, ensure_ascii=False)}
        for call, observation in zip(calls, observations)]
