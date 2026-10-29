"""Trusted platform provisioning: run official rc-tunnel from an SSH session."""
import argparse
import os
from pathlib import Path
import subprocess
import re
import time


def platform_environment(environ=None, notebook=Path('/proc/1/environ')):
    env = dict(os.environ if environ is None else environ)
    if not env.get('FRP_BROKER_URL'):
        for entry in notebook.read_bytes().split(b'\0'):
            if b'=' in entry:
                key, value = entry.split(b'=', 1)
                if key in (b'FRP_BROKER_URL', b'FRP_BROKER_TLS_SERVER_NAME'):
                    env.setdefault(os.fsdecode(key), os.fsdecode(value))
    env.setdefault('TMPDIR', '/var/tmp')
    for key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'all_proxy'):
        env.pop(key, None)
    return env


def install(binary, env):
    state = Path(env.get('AGENT_TMP', '/var/tmp/rtl-agent'))/'platform-tunnel'
    state.mkdir(parents=True, exist_ok=True)
    log = state/'install.private.log'
    with log.open('wb') as stream:
        log.chmod(0o600)
        result = subprocess.run(['/var/run/secrets/frp-self-service/install'], env=env,
                                stdout=stream, stderr=stream)
    if result.returncode:
        raise RuntimeError('Tunnel installation failed; private log: '+str(log))
    print('Official tunnel client installed: '+str(binary))


def ensure(binary, env, timeout=60):
    """Preserve a live URL; only request a new lease when no client is running."""
    if not binary.is_file():
        install(binary, env)
    end = time.monotonic()+timeout
    exposed = False
    while True:
        result = subprocess.run([str(binary), 'status'], env=env, capture_output=True, text=True)
        status = result.stdout
        if result.returncode == 0 and re.search(r'Status:\s*active\b', status, re.I):
            print(status, end='')
            return
        running = re.search(r'FRPC running:\s*true\b', status, re.I)
        if not running and not exposed:
            subprocess.run([str(binary), 'expose', '--port', '7860'], env=env, check=True)
            exposed = True
        if time.monotonic() >= end:
            raise RuntimeError('Tunnel did not become active; existing URL was not replaced repeatedly')
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'expose', 'status', 'ensure'))
    args = parser.parse_args()
    env = platform_environment()
    binary = Path.home()/'.local/bin/rc-tunnel'
    if args.action == 'install':
        install(binary, env)
        return
    if args.action == 'ensure':
        ensure(binary, env)
        return
    command = [str(binary), args.action]
    if args.action == 'expose':
        command += ['--port', '7860']
    raise SystemExit(subprocess.run(command, env=env).returncode)


if __name__ == '__main__':
    main()
