"""Bounded startup synthesis probe; retain working licensing, then try the official fallback."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

SOURCE = Path(__file__).with_suffix('.sv')
FALLBACK = '2100@vivado-lic.fpgachina.svc.cluster.local'
KEYS = ('XILINXD_LICENSE_FILE', 'LM_LICENSE_FILE', 'FLEXLM_TIMEOUT')
MARKER = 'RTL_LICENSE_SYNTH_OK'


def state_directory(env):
    return Path(env.get('AGENT_TMP', '/var/tmp/rtl-agent'))/'services/license-probe'


def configuration(env):
    return {key:env[key] for key in KEYS if key in env}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def executable(env):
    if env.get('XILINX_VIVADO'):
        candidate = Path(env['XILINX_VIVADO'])/'bin'/('vivado.bat' if os.name == 'nt' else 'vivado')
        if candidate.is_file():
            return str(candidate.resolve())
    found = shutil.which('vivado', path=env.get('PATH', ''))
    if not found:
        raise RuntimeError('Vivado not found; load its installed environment before checking licensing')
    return str(Path(found).resolve())


def identity(env, binary):
    stat = Path(binary).stat()
    pid1 = Path('/proc/1/stat')
    boot = Path('/proc/sys/kernel/random/boot_id')
    return {'binary':binary, 'size':stat.st_size, 'mtime_ns':stat.st_mtime_ns,
            'part':env.get('FPGA_PART', 'xczu3eg-sbva484-1-e'),
            'source':hashlib.sha256(SOURCE.read_bytes()+Path(__file__).read_bytes()).hexdigest(),
            'session':pid1.read_text().rsplit(')', 1)[1].split()[19] if pid1.exists() else str(os.getpid()),
            'boot':boot.read_text().strip() if boot.exists() else ''}


def cached_selection(env):
    try:
        record = json.loads((state_directory(env)/'success.json').read_text())
        if (record['identity'] == identity(env, executable(env))
                and digest(configuration(env)) in record['accepted_configurations']):
            return record
    except (OSError, ValueError, KeyError, RuntimeError):
        pass
    return None


@contextmanager
def locked(root):
    with (root/'probe.lock').open('a') as lock:
        if os.name == 'posix':
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def synthesize(env, binary, root, label, timeout):
    started = time.monotonic()
    eda = Path(env.get('EDA_TMP', '/var/tmp/eda'))
    eda.mkdir(parents=True, exist_ok=True)
    log_path = root/(label+'.log')
    with tempfile.TemporaryDirectory(prefix='license-probe-', dir=eda) as directory:
        work = Path(directory)
        shutil.copyfile(SOURCE, work/'probe.sv')
        part = env.get('FPGA_PART', 'xczu3eg-sbva484-1-e')
        if not part or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in part):
            raise ValueError('Invalid FPGA_PART')
        (work/'probe.tcl').write_text(
            'set_param general.maxThreads 1\nread_verilog -sv probe.sv\n'
            'synth_design -top license_probe -part '+part+' -mode out_of_context -directive RuntimeOptimized\n'
            'if {[llength [get_cells -hierarchical]] == 0} {error "empty probe design"}\n'
            'puts "'+MARKER+'"\nexit\n')
        timed_out = False
        with log_path.open('wb') as log:
            proc = subprocess.Popen([binary, '-mode', 'batch', '-nojournal', '-nolog', '-notrace',
                                     '-source', 'probe.tcl'], cwd=work, env=env,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=os.name == 'posix')
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == 'posix':
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    proc.kill()
                proc.wait()
        completed = MARKER in log_path.read_text(errors='replace').splitlines()
        return {'passed':not timed_out and proc.returncode == 0 and completed,
                'timeout':timed_out, 'returncode':proc.returncode,
                'seconds':round(time.monotonic()-started, 3), 'log_path':str(log_path)}


def ensure_license(environ=None):
    env = dict(os.environ if environ is None else environ)
    root = state_directory(env)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    timeout = float(env.get('LICENSE_PROBE_TIMEOUT_S', '30'))
    if not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError('LICENSE_PROBE_TIMEOUT_S must be greater than 0 and at most 30')
    with locked(root):
        cached = cached_selection(env)
        if cached:
            return dict(cached, cached=True)
        binary = executable(env)
        original = configuration(env)
        selected = dict(env)
        attempts = [synthesize(selected, binary, root, 'existing', timeout)]
        mode = 'existing'
        if not attempts[-1]['passed']:
            selected = dict(selected, XILINXD_LICENSE_FILE=FALLBACK)
            selected.setdefault('FLEXLM_TIMEOUT', '300000000')
            if configuration(selected) != original:
                attempts.append(synthesize(selected, binary, root, 'fallback', timeout))
                mode = 'fallback'
            if not attempts[-1]['passed']:
                raise RuntimeError('Vivado synthesis licensing probe failed; inspect '+str(root))
        record = {'identity':identity(env, binary), 'exports':configuration(selected),
                  'accepted_configurations':[digest(original), digest(configuration(selected))],
                  'mode':mode, 'attempts':attempts, 'cached':False}
        with tempfile.NamedTemporaryFile(mode='w', dir=root, delete=False, encoding='utf-8') as file:
            json.dump(record, file, indent=2)
            temporary = Path(file.name)
        temporary.replace(root/'success.json')
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--shell', action='store_true')
    group.add_argument('--cached-shell', action='store_true')
    args = parser.parse_args()
    record = cached_selection(os.environ) if args.cached_shell else ensure_license()
    if record is None:
        return
    if args.shell or args.cached_shell:
        for key, value in record['exports'].items():
            print('export '+key+'='+shlex.quote(value))
    else:
        print(json.dumps({key:record[key] for key in ('mode', 'cached', 'attempts')}))


if __name__ == '__main__':
    main()
