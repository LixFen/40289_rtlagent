"""模型适配：连接配置、原生请求、响应读取及调用解析。"""
import json
import os
import time
import urllib.request

from agent.llm.protocol import parse_tool_actions
from agent.llm.streaming import read_stream
from agent.llm.tokens import TokenCounter, account_usage, estimate_input
from baseline import load_generation
from agent.config import load_settings, load_runtime, local_address


def output_limit(messages, limit, context_tokens=0, input_tokens=None):
    """Reserve this request's input tokens within the context window."""
    if context_tokens:
        reserved = estimate_input(messages) if input_tokens is None else input_tokens
        return max(0, min(limit, context_tokens-reserved))
    return limit


class ModelClient:
    """封装单个本地模型端点；云端开发请求由本地桥接转发。"""

    def __init__(self):
        """读取模型端点、生成参数和响应限制，不将控制器资源状态写入提示词。"""
        settings = load_settings()
        self.base = settings.base_url
        self.name = settings.model_name
        self.streaming = os.environ.get('AGENT_STREAM') == '1'
        self.reasoning_effort = os.environ.get('AGENT_REASONING_EFFORT', '')
        self.generation = load_generation()
        preserve = os.environ.get('AGENT_PRESERVE_THINKING')
        self.preserve_thinking = (preserve == '1' if preserve is not None else
                                 load_runtime().get('agent', {}).get('preserve_thinking', False))
        self.max_output = settings.max_output_tokens
        self.context_tokens = settings.context_tokens
        self.response_bytes = settings.max_response_bytes
        self.counter = TokenCounter(self.base) if self.is_local() else None
        self.input_tokens, self.input_token_source = None, None
        self.accounting_source = 'usage'

    def is_local(self):
        """仅允许无内嵌凭据的 loopback HTTP 端点。"""
        try:
            local_address(self.base)
            return True
        except ValueError:
            return False

    def create_request(self, messages, tools):
        """生成 Chat Completions 原生多工具请求，按当前消息计算输出上限。"""
        body = {'model':self.name, 'messages':messages, 'temperature':1.0, 'top_p':0.95,
                'top_k':20, 'min_p':0.0, 'presence_penalty':0.0, 'repeat_penalty':1.0,
                'max_tokens':self.max_output,
                'tools':tools, 'tool_choice':'required', 'parallel_tool_calls':True}
        if self.streaming:
            body.update(stream=True, stream_options={'include_usage':True})
        if self.reasoning_effort:
            body['reasoning_effort'] = self.reasoning_effort
        body.update(self.generation)
        if self.counter:
            self.input_tokens, self.input_token_source = self.counter.count_input(body)
        else:
            self.input_tokens, self.input_token_source = estimate_input(body), 'estimated_chars_v1'
        body['max_tokens'] = output_limit(body, self.max_output, self.context_tokens, self.input_tokens)
        return body

    def complete(self, body, out, record, call, end, remaining):
        """记录请求并读取响应，保留原始 SSE，返回完整响应和记账 token 数。"""
        started = time.monotonic()
        record('llm_request', call=call, request=body, budget_input_tokens=self.input_tokens,
               input_token_source=self.input_token_source)
        request = urllib.request.Request(self.base+'/chat/completions', data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                                         headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request, timeout=max(0.01, remaining)) as response:
            if self.streaming:
                result, _ = read_stream(response, out/f'llm-{call:04d}.sse',
                                                     record, call, end, self.response_bytes)
            else:
                raw = response.read(self.response_bytes+1)
                if len(raw) > self.response_bytes:
                    raise ValueError('Model response exceeds byte budget')
                result = json.loads(raw)
        tokens, self.accounting_source = account_usage(result,
            self.input_tokens if self.input_tokens is not None else estimate_input(body))
        usage = result.get('usage') or {}
        record('llm', call=call, request=body, response=result,
               tokens_in=usage.get('prompt_tokens'),
               tokens_out=usage.get('completion_tokens'),
               accounted_tokens=tokens, token_accounting_source=self.accounting_source,
               budget_input_tokens=self.input_tokens, input_token_source=self.input_token_source,
               sec=time.monotonic()-started)
        return result, tokens

    def parse_actions(self, result):
        """拒绝截断响应，并在执行前校验整轮原生工具调用。"""
        choice = result['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise ValueError('Function call response is incomplete; return complete function arguments')
        return parse_tool_actions(choice)
