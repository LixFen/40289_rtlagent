"""Host launcher: verify artifacts, start inference, warm up, then expose local HTTP."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from agent.config import load_settings, load_runtime, local_address
from agent.paths import ROOT
from serve.credentials import load_token
from serve.watchdog import running, state_directory


def model_identity(model, stat):
    return dict(path=str(model.resolve()), device=stat.st_dev, inode=stat.st_ino,
                size=stat.st_size, mtime_ns=stat.st_mtime_ns, ctime_ns=stat.st_ctime_ns)


def verify_model(model, expected, cache, force=False):
    """Reuse a previous full hash only while the file identity and timestamps match."""
    before = model_identity(model, model.stat())
    record = dict(schema=1, sha256=expected, file=before)
    if not force:
        try:
            if json.loads(cache.read_text(encoding='utf-8')) == record:
                print('Model unchanged; reusing verified SHA-256 record.', flush=True)
                return
        except (OSError, ValueError):
            pass
    print('Checking full model SHA-256...', flush=True)
    digest = hashlib.sha256()
    with model.open('rb') as stream:
        if model_identity(model, os.fstat(stream.fileno())) != before:
            raise RuntimeError('Model changed before SHA-256 verification')
        while block := stream.read(16*1024*1024):
            digest.update(block)
        if model_identity(model, os.fstat(stream.fileno())) != before:
            raise RuntimeError('Model changed during SHA-256 verification')
    if model_identity(model, model.stat()) != before:
        raise RuntimeError('Model changed during SHA-256 verification')
    if digest.hexdigest() != expected:
        raise RuntimeError('Model SHA-256 mismatch')
    cache.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=cache.parent,
                                     prefix='model-sha256-', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(record, stream)
    try:
        os.replace(temporary, cache)
    finally:
        temporary.unlink(missing_ok=True)


def prerequisites():
    """Fail before starting processes when configuration or pinned files are missing."""
    load_token()
    settings = load_settings()
    local_address(settings.base_url)
    manifest = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
    runtime = load_runtime(ROOT)
    if settings.model_name != manifest['model']['name']:
        raise RuntimeError('MODEL_NAME must match manifest.json')
    model = Path(os.environ['MODEL_PATH'])
    verification = os.environ.get('AGENT_VERIFY_MODEL', 'cached')
    if verification not in ('cached', 'full'):
        raise RuntimeError('AGENT_VERIFY_MODEL must be cached or full')
    cache = Path(os.environ.get('AGENT_TMP', '/var/tmp/rtl-agent'))/'services/model-sha256.json'
    expected_sha = os.environ['MODEL_SHA256'] if 'MODEL_SHA256' in os.environ else runtime['model']['sha256']
    verify_model(model, expected_sha, cache, force=verification == 'full')
    for name, expected in runtime['baseline_source']['canonical_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Official baseline hash mismatch: '+name)
    server = os.environ.get('LLAMA_SERVER', '')
    if not server or not os.access(server, os.X_OK):
        raise RuntimeError('LLAMA_SERVER must name an executable ROCm llama-server')
    for name in ('vivado', 'xvlog', 'xelab', 'xsim'):
        if not shutil.which(name):
            raise RuntimeError('Missing tool in PATH: '+name+'; load Vivado settings in the environment file')
    return settings


def warmup(settings):
    """Require actual model output, beyond the /models readiness check."""
    payload = dict(model=settings.model_name,
                   messages=[dict(role='user', content='Reply with OK.')],
                   temperature=0, max_tokens=1024)
    request = urllib.request.Request(settings.base_url+'/chat/completions',
                                     data=json.dumps(payload).encode(),
                                     headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    message = result['choices'][0]['message']
    if not (message.get('content') or '').strip():
        raise RuntimeError('Model warmup returned no text')
    print('Real model generation passed.', flush=True)


def http_health(url):
    request = urllib.request.Request(url, headers={
        'Authorization':'Bearer '+os.environ['FPGACHINA_TOKEN']})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError('Existing HTTP service returned status '+str(exc.code)) from None
    except urllib.error.URLError:
        return None


def main():
    import fcntl
    os.environ.setdefault('EDA_TMP', '/var/tmp/eda')
    os.environ.setdefault('AGENT_TMP', '/var/tmp/rtl-agent')
    root = Path(os.environ['AGENT_TMP']).resolve()/'services'
    root.mkdir(parents=True, exist_ok=True)
    started_api = False
    try:
        with (root/'deploy.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            settings = prerequisites()
            subprocess.run(['bash', str(ROOT/'serve/start.sh')], cwd=ROOT, check=True)
            warmup(settings)
            port = int(os.environ.get('PORT', '7860'))
            if not 1024 <= port <= 65535:
                raise RuntimeError('PORT must be within 1024..65535')
            url = 'http://127.0.0.1:'+str(port)+'/v1/health'
            current = http_health(url)
            if current is None:
                started_api = running(state_directory()) is None
                subprocess.run([sys.executable, '-m', 'serve.watchdog', 'start'], cwd=ROOT, check=True)
            elif not running(state_directory()):
                raise RuntimeError('Existing HTTP service has no watchdog; stop it before deploying this version')
            end = time.monotonic()+30
            while time.monotonic() < end:
                if not running(state_directory()):
                    raise RuntimeError('HTTP watchdog exited; inspect '+str(state_directory()/'serve_api.log'))
                current = http_health(url)
                if (current and current.get('ready') is True
                        and current.get('model') == settings.model_name
                        and current.get('track') == 'rtl'):
                    print(json.dumps(current), flush=True)
                    print('HTTP ready: '+url+'; logs: '+str(root), flush=True)
                    return
                time.sleep(1)
            raise TimeoutError('HTTP service did not become ready within 30 seconds')
    except BaseException:
        if started_api:
            subprocess.run([sys.executable, '-m', 'serve.watchdog', 'stop'], cwd=ROOT, check=False)
        raise


if __name__ == '__main__':
    main()
