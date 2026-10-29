"""运行参数入口：环境变量覆盖官方 manifest 和公共 runtime 配置。"""
from dataclasses import dataclass, fields
import json
import os
from pathlib import Path
import shlex
import sys
import urllib.parse

from agent.paths import ROOT


@dataclass(frozen=True)
class Settings:
    """模型连接、窗口、输出及配套传输限制；凭据须独立配置。"""
    base_url: str
    model_name: str
    context_tokens: int
    max_output_tokens: int
    input_char_limit: int
    max_response_bytes: int


# 官方字段从 manifest 读取；运行扩展字段从 serve/runtime.json 读取。
PARAMETERS = {
    'base_url': ('LLM_BASE_URL', 'serving', 'base_url'),
    'model_name': ('MODEL_NAME', 'model', 'name'),
    'context_tokens': ('AGENT_CONTEXT_TOKENS', 'context', 'max_model_len'),
    'max_output_tokens': ('AGENT_MAX_OUTPUT_TOKENS', 'context', 'max_output_tokens'),
    'input_char_limit': ('AGENT_INPUT_CHAR_LIMIT', 'agent', 'input_char_limit'),
    'max_response_bytes': ('AGENT_MAX_RESPONSE_BYTES', 'agent', 'max_response_bytes'),
}


def load_manifest(root=None):
    return json.loads((Path(root or ROOT)/'manifest.json').read_text(encoding='utf-8'))


def load_runtime(root=None):
    """Public defaults and pinned artifacts, separate from the organizer manifest."""
    path = Path(root or ROOT)/'serve/runtime.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def model_call_limit(environ=None):
    env = os.environ if environ is None else environ
    return int(env['AGENT_MAX_MODEL_CALLS'] if 'AGENT_MAX_MODEL_CALLS' in env
               else load_manifest()['agent']['max_rounds'])


def runtime_value(section, key, variable, environ=None):
    env = os.environ if environ is None else environ
    return env[variable] if variable in env else load_runtime()[section][key]


def load_settings(manifest_path=None, environ=None, profile=None):
    """读取配置快照，缺失/无效字段直接报错，避免悄悄采用另一组默认值。"""
    manifest = json.loads(Path(manifest_path or ROOT/'manifest.json').read_text(encoding='utf-8'))
    environ = os.environ if environ is None else environ
    if profile not in (None, 'development'):
        raise ValueError('Unknown settings profile')
    runtime = load_runtime(Path(manifest_path).parent if manifest_path else None)
    overrides = manifest.get('development', runtime.get('development', {})) if profile == 'development' else {}
    values = {}
    for name, (variable, section, key) in PARAMETERS.items():
        if profile == 'development' and name == 'base_url':
            variable = 'CLOUD_LLM_BASE_URL'
        if variable in environ:
            value = environ[variable]
        elif name in overrides:
            value = overrides[name]
        elif key in manifest[section]:
            value = manifest[section][key]
        elif name == 'base_url':
            value = 'http://127.0.0.1:'+str(manifest['serving']['port'])+'/v1'
        else:
            value = runtime[section][key]
        if Settings.__annotations__[name] is str:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(variable+' must be a nonempty string')
            values[name] = value.rstrip('/') if name == 'base_url' else value
        else:
            if isinstance(value, bool):
                raise ValueError(variable+' must be an integer')
            values[name] = int(value)
    return Settings(**values)


def local_address(base_url):
    """从本机无凭据 HTTP 地址解析绑定端点；错误消息不包含 URL 内容。"""
    url = urllib.parse.urlparse(base_url)
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1')
            or url.username or url.password or url.query or url.fragment):
        raise ValueError('LLM_BASE_URL must be credential-free loopback HTTP')
    return url.hostname, url.port or 80


def main():
    """为 Shell 输出安全引用的 export 语句，或打印不含凭据的有效配置。"""
    settings = load_settings()
    host, port = local_address(settings.base_url)
    if sys.argv[1:] == ['--shell']:
        for field in fields(settings):
            variable = PARAMETERS[field.name][0]
            print('export '+variable+'='+shlex.quote(str(getattr(settings, field.name))))
        print('export LLM_BIND_HOST='+shlex.quote(host))
        print('export LLM_BIND_PORT='+str(port))
        generation = load_manifest().get('generation', {})
        kwargs = dict(generation.get('chat_template_kwargs', {}))
        runtime = load_runtime()
        kwargs.setdefault('preserve_thinking', runtime.get('agent', {}).get('preserve_thinking', False))
        if generation.get('reasoning_effort'):
            kwargs['reasoning_effort'] = generation['reasoning_effort']
        print('export LLAMA_CHAT_TEMPLATE_KWARGS='+shlex.quote(json.dumps(kwargs)))
        budget = runtime_value('serving', 'reasoning_budget_tokens', 'LLAMA_REASONING_BUDGET')
        print('export LLAMA_REASONING_BUDGET='+shlex.quote(str(int(budget))))
    elif not sys.argv[1:]:
        print(json.dumps({field.name:getattr(settings, field.name) for field in fields(settings)}, indent=2))
    else:
        raise SystemExit('Usage: python -m agent.config [--shell]')


if __name__ == '__main__':
    main()
