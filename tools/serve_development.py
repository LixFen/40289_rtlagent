"""开发 HTTP 接题服务与云模型桥接启动器（仅标准库）。"""
import argparse
import getpass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.config import load_settings
from baseline import load_generation


def bridge_handler(upstream, key, model, generation=None):
    response_bytes = load_settings(profile='development').max_response_bytes
    class Bridge(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_error(self, *args, **kwargs):
            try:
                super().send_error(*args, **kwargs)
            except OSError:
                pass  # The deadline supervisor may already have closed the client.

        def forward(self):
            stream_started = False
            if (self.command, self.path) not in (
                ('GET', '/v1/models'), ('POST', '/v1/chat/completions')
            ):
                self.send_error(404)
                return
            try:
                data = None
                if self.command == 'POST':
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 262144:
                        self.send_error(400)
                        return
                    self.connection.settimeout(5)
                    data = self.rfile.read(length)
                    payload = json.loads(data)
                    if not isinstance(payload, dict) or payload.get('model') != model:
                        self.send_error(400)
                        return
                    payload.update(generation or {})
                    data = json.dumps(payload).encode()
                request = urllib.request.Request(
                    upstream + self.path.removeprefix('/v1'), data=data,
                    headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=300) as response:
                    if self.command == 'POST' and payload.get('stream') is True:
                        self.send_response(200)
                        self.send_header('Content-Type', response.headers.get('Content-Type', 'application/octet-stream'))
                        self.send_header('Cache-Control', 'no-cache')
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        self.close_connection = True
                        stream_started = True
                        size = 0
                        while True:
                            chunk = response.readline(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > response_bytes:
                                raise ValueError('Upstream response too large')
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        return
                    body = response.read(response_bytes + 1)
                    if len(body) > response_bytes:
                        raise ValueError('Upstream response too large')
                # Baseline selects the first advertised model. Expose only the requested
                # model when the upstream actually advertises it; never invent a model.
                if self.command == 'GET':
                    models = json.loads(body)
                    models['data'] = [item for item in models['data'] if item['id'] == model]
                    body = json.dumps(models).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except urllib.error.HTTPError as exc:
                if not stream_started:
                    self.send_error(502, 'Upstream HTTP ' + str(exc.code))
            except (OSError, ValueError, KeyError, TypeError):
                if not stream_started:
                    self.send_error(502, 'Upstream request failed')

        do_GET = forward
        do_POST = forward
    return Bridge


def parse_args(argv=None):
    """命令行覆盖环境变量和 manifest 默认值；解析不启动服务或读取密钥。"""
    settings = load_settings(profile='development')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', default=settings.base_url)
    parser.add_argument('--model', default=settings.model_name)
    parser.add_argument('--vivado', default=os.environ.get('XILINX_VIVADO', 'D:/AMDDesignTools/2026.1/Vivado'))
    parser.add_argument('--bridge-port', type=int, default=18000)
    parser.add_argument('--port', type=int, default=7860)
    parser.add_argument('--output-tokens', type=int, default=settings.max_output_tokens,
                        help='Agent output ceiling (default: manifest/environment)')
    parser.add_argument('--context-tokens', type=int, default=settings.context_tokens,
                        help='Agent context allowance; must fit the upstream model window')
    parser.add_argument('--code-first', action='store_true', help='Ask Agent to save RTL before planning/contract turns')
    parser.add_argument('--stream', action=argparse.BooleanOptionalAction, default=True,
                        help='Stream and capture model SSE events (default: enabled)')
    parser.add_argument('--reasoning-effort', choices=('low', 'medium', 'xhigh', 'default'),
                        default=load_generation().get('reasoning_effort') or 'default',
                        help='Agent reasoning effort (default: manifest); default omits the provider parameter')
    parser.add_argument('--probe', action='store_true', help='Check models and actual generation, then exit')
    args = parser.parse_args(argv)
    if args.output_tokens <= 0 or args.context_tokens <= 0:
        parser.error('--output-tokens and --context-tokens must be positive')
    upstream = urllib.parse.urlparse(args.upstream)
    if upstream.scheme != 'https' or not upstream.hostname or upstream.username or upstream.password or upstream.query or upstream.fragment:
        parser.error('Upstream must be an HTTPS base URL without credentials/query/fragment')
    return args


def main():
    """启动桥接与 HTTP 接题服务，应用已解析的有效配置。"""
    args = parse_args()
    settings = load_settings(profile='development')
    key = os.environ.pop('CLOUD_LLM_API_KEY', '') or getpass.getpass('Cloud API key (hidden): ')
    if not key:
        raise SystemExit('Cloud API key is required')
    generation = {} if args.reasoning_effort == 'default' else {'reasoning_effort':args.reasoning_effort}
    bridge = ThreadingHTTPServer(('127.0.0.1', args.bridge_port), bridge_handler(args.upstream.rstrip('/'), key, args.model,generation))
    bridge.daemon_threads = True
    threading.Thread(target=bridge.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{bridge.server_port}/v1'
    try:
        with urllib.request.urlopen(base + '/models', timeout=30) as response:
            found = any(item['id'] == args.model for item in json.load(response)['data'])
        if not found:
            raise SystemExit('Requested model is not advertised by upstream')
        print('Cloud model advertised:', args.model, flush=True)
        if args.probe:
            request = urllib.request.Request(base + '/chat/completions',
                data=json.dumps({'model': args.model, 'messages': [{'role': 'user', 'content': 'Reply with OK.'}],
                                 'temperature': 0, 'top_p': 1, 'max_tokens': 64}).encode(),
                headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=310) as response:
                result = json.load(response)
            choice = result['choices'][0]
            print(json.dumps({'generation_received': True, 'finish_reason': choice.get('finish_reason'),
                              'content_present': bool(choice['message'].get('content')),
                              'usage': result.get('usage')}, ensure_ascii=True), flush=True)
            return
        install = Path(args.vivado).resolve()
        for name in ('vivado', 'xvlog', 'xelab', 'xsim'):
            if not (install / 'bin' / (name + ('.bat' if os.name == 'nt' else ''))).is_file():
                raise SystemExit('Missing Vivado entry: ' + name)
        os.environ.update(LLM_BASE_URL=base, MODEL_NAME=args.model, XILINX_VIVADO=str(install),
                          PORT=str(args.port), PYTHONUTF8='1',
                          AGENT_MAX_OUTPUT_TOKENS=str(args.output_tokens),
                          AGENT_CODE_FIRST=str(int(args.code_first)),
                          AGENT_STREAM=str(int(args.stream)),
                          AGENT_REASONING_EFFORT='' if args.reasoning_effort == 'default' else args.reasoning_effort,
                          AGENT_CONTEXT_TOKENS=str(args.context_tokens),
                          AGENT_MAX_RESPONSE_BYTES=str(settings.max_response_bytes))
        os.environ['PATH'] = str(install / 'bin') + os.pathsep + os.environ['PATH']
        os.environ.setdefault('RUN_ARCHIVE_DIR', str(ROOT / 'out' / 'local-cloud-test'))
        if not os.environ.get('FPGACHINA_TOKEN'):
            os.environ['FPGACHINA_TOKEN'] = getpass.getpass('Local Agent bearer token (hidden): ')
        from serve.api import main as serve
        print(f'Development Agent: http://127.0.0.1:{args.port}/v1/solve', flush=True)
        print('POST returns HTTP 202; poll GET /v1/jobs/<job_id> until done.',flush=True)
        print('Agent cumulative token usage: recorded without a task limit', flush=True)
        print(f'Agent output cap: {args.output_tokens}; input reserve deducted from {args.context_tokens} context tokens.', flush=True)
        print('Production health still requires local GPU telemetry; cloud tests are not final acceptance.', flush=True)
        serve()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.shutdown()
        bridge.server_close()


if __name__ == '__main__':
    main()
