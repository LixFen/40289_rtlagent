"""用于模型测试端的 Chat Completions 函数调用响应。"""
import json


def completion(action, call_id='call_test', usage=None):
    if isinstance(action, str):
        message = {'role':'assistant', 'content':action}
        finish = 'stop'
    else:
        actions = action if isinstance(action, list) else [action]
        message = {'role':'assistant', 'content':None, 'tool_calls':[
            {'id':call_id if len(actions)==1 else f'{call_id}_{i}', 'type':'function', 'function':{
                'name':item['tool'], 'arguments':json.dumps(item.get('args', {}))}} for i,item in enumerate(actions)]}
        finish = 'tool_calls'
    return {'choices':[{'index':0, 'message':message, 'finish_reason':finish}],
            'usage':usage or {'total_tokens':50}}


def tool_delta(action, call_id='call_test'):
    calls = completion(action, call_id)['choices'][0]['message']['tool_calls']
    return {'tool_calls':[dict(call, index=i) for i,call in enumerate(calls)]}
