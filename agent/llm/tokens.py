"""Token counts for budgeting; transport bytes never represent consumed tokens."""
import json
import math
import urllib.request

from agent.config import local_address


def estimate_text(text):
    """Fallback estimate: three ASCII characters or one Unicode character per token.

    This is an estimate, not a tokenizer or a guaranteed context upper bound.
    """
    ascii_chars = sum(ord(char) < 128 for char in text)
    return math.ceil(ascii_chars/3) + len(text)-ascii_chars


def estimate_input(body):
    # Only model-visible data, excluding sampling and HTTP/stream configuration.
    content = {key:body[key] for key in ('messages', 'tools') if key in body} if isinstance(body, dict) else body
    return estimate_text(json.dumps(content, ensure_ascii=False)) + 1024


class TokenCounter:
    """Use the local llama.cpp template/tokenizer when available; otherwise estimate."""

    def __init__(self, base):
        local_address(base)
        self.base = base.removesuffix('/v1')
        self.native = None
        # Counting uses a separate loopback-only transport, without proxy settings.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, body=None):
        data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
        request = urllib.request.Request(self.base+path, data=data,
                                         headers={'Content-Type':'application/json'})
        with self.opener.open(request, timeout=2) as response:
            raw = response.read(8*1024*1024+1)
        if len(raw) > 8*1024*1024:
            raise ValueError('Tokenizer response too large')
        return json.loads(raw)

    def count_input(self, body):
        try:
            if self.native is None:
                props = self.request('/props')
                self.native = isinstance(props, dict) and isinstance(props.get('chat_template'), str)
            if self.native:
                prompt = self.request('/apply-template', body)['prompt']
                if not isinstance(prompt, str):
                    raise ValueError('Invalid rendered prompt')
                tokens = self.request('/tokenize', dict(content=prompt, add_special=False,
                                                        parse_special=True))['tokens']
                if not isinstance(tokens, list) or not all(type(token) is int for token in tokens):
                    raise ValueError('Invalid tokenizer result')
                return len(tokens), 'llamacpp_template_tokenizer'
        except (OSError, ValueError, KeyError, TypeError):
            self.native = False
        return estimate_input(body), 'estimated_chars_v1'


def account_usage(result, input_tokens):
    """Prefer provider usage; clearly label all missing/partial-usage fallbacks."""
    usage = result.get('usage') or {}
    valid = lambda value: type(value) is int and value >= 0
    if valid(usage.get('total_tokens')):
        return usage['total_tokens'], 'usage'
    if valid(usage.get('prompt_tokens')) and valid(usage.get('completion_tokens')):
        return usage['prompt_tokens']+usage['completion_tokens'], 'usage'
    # Exclude response IDs, timings, JSON wrappers and SSE framing from estimates.
    message = result['choices'][0]['message']
    text = (message.get('content') or '') + (message.get('reasoning') or message.get('reasoning_content') or '')
    for call in message.get('tool_calls') or []:
        function = call.get('function') or {}
        text += (function.get('name') or '') + (function.get('arguments') or '')
    prompt = usage['prompt_tokens'] if valid(usage.get('prompt_tokens')) else input_tokens
    completion = usage['completion_tokens'] if valid(usage.get('completion_tokens')) else estimate_text(text)
    return prompt+completion, 'estimated_missing_usage'
