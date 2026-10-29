"""Fixed Vivado operations. Model data never becomes a shell or Tcl command."""
import csv
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time

from agent.tooling.workspace import safe_hdl, sha
from agent.processes import register, terminate
from agent.config import runtime_value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', value):
        raise ValueError('Invalid module identifier')
    return value


def log_excerpt(log, limit=2048):
    """按 UTF-8 字节限制日志首尾摘要，完整日志另存。"""
    raw=log.encode('utf-8')
    if len(raw)<=limit:
        return log
    half=(limit-5)//2
    return raw[:half].decode('utf-8',errors='ignore')+'\n...\n'+raw[-half:].decode('utf-8',errors='ignore')


def directory_size(root):
    """统计仍存在的文件；Vivado 可能在扫描与取大小之间清理临时文件。"""
    total = 0
    for path in root.rglob('*'):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


def diagnostics(log):
    rows = []
    for line in log.splitlines():
        if re.search(r'ERROR|CRITICAL WARNING|WARNING|FATAL|RTL_SELFTEST_FAIL', line, re.I):
            match = re.search(r'\[([\w]+ \d+-\d+)\]', line)
            rows.append({'id': match[1] if match else None, 'message': line[:1200]})
    return rows[:30]


def lint_rows(file):
    """Vivado 2026.1 canonical headerless seven-column CSV, including row zero."""
    columns = ('rule_id', 'description', 'rtl_name', 'info', 'module', 'file_name', 'line_number')
    with Path(file).open(encoding='utf-8-sig', newline='') as stream:
        rows = [row for row in csv.reader(stream) if row and any(row)]
    if rows and rows[0][0].strip().lower() == 'rule_id':
        rows = rows[1:]
    if any(len(row) != 7 for row in rows):
        raise ValueError('Unsupported lint CSV schema; refusing to invent diagnostics')
    return [dict(zip(columns, row)) for row in rows]


def canonical_lint(file):
    raw = Path(file).read_text(encoding='utf-8', errors='replace')
    if '2. Expanded' not in raw or 'RTL Linter Report' not in raw:
        return Path(file), 'native-CSV'
    from agent.vendor.ross_parse_lint_report import extract_synthesis_insights
    # Upstream parser can slice the waiver footer into bogus rows on zero-message
    # reports. Remove that known non-table footer; retain original report verbatim.
    sanitized = Path(file).with_name('lint-parser-input.rpt')
    sanitized.write_text(raw.split('\n* create_waiver', 1)[0], encoding='utf-8')
    canonical = Path(file).with_name('lint-canonical.csv')
    canonical.write_text(extract_synthesis_insights(str(sanitized)), encoding='utf-8')
    return canonical, 'AMD-Ross-pinned-ASCII-to-CSV'


class EDA:
    def __init__(self, workspace, out, end, record=None):
        self.ws, self.out, self.end = workspace, Path(out), end
        self.calls, self.seconds = 0, 0.0
        self.max_calls = int(runtime_value('agent', 'max_tool_runs', 'AGENT_MAX_TOOL_RUNS'))
        self.max_seconds = float(runtime_value('agent', 'tool_seconds', 'AGENT_TOOL_SECONDS'))
        self.cap = float(runtime_value('agent', 'tool_timeout_s', 'AGENT_TOOL_TIMEOUT_S'))
        self.max_bytes = int(runtime_value('agent', 'artifact_mb', 'AGENT_ARTIFACT_MB')) * 1024**2
        self.record = record or (lambda *_a, **_kw: None)

    def executable(self, name):
        install = os.environ.get('XILINX_VIVADO')
        if install:
            suffix = '.bat' if os.name == 'nt' else ''
            target = Path(install) / 'bin' / (name + suffix)
            if target.is_file():
                return str(target)
        found = shutil.which(name)
        if found:
            return found
        raise FileNotFoundError(name)

    def run(self, work, tool, args):
        remaining = min(self.end-time.monotonic()-0.15, self.cap, self.max_seconds-self.seconds)
        if self.calls >= self.max_calls or remaining <= 0.05:
            return {'status': 'budget', 'tool': tool}
        if directory_size(self.out) > self.max_bytes:
            return {'status': 'artifact_budget', 'tool': tool}
        try:
            executable = self.executable(tool)
        except FileNotFoundError:
            return {'status': 'unavailable', 'tool': tool}
        self.calls += 1
        command = [executable, *args]
        if os.name == 'nt' and executable.lower().endswith('.bat'):
            command = ['cmd.exe', '/d', '/c', *command]
        # No API token or model credentials in the tool environment. Preserve vendor setup.
        keys = ('PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'LD_LIBRARY_PATH', 'LANG',
                'PROCESSOR_ARCHITECTURE', 'PROCESSOR_ARCHITEW6432', 'NUMBER_OF_PROCESSORS',
                'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'ALLUSERSPROFILE',
                'USERNAME', 'USERDOMAIN',
                'LM_LICENSE_FILE', 'XILINXD_LICENSE_FILE', 'FLEXLM_TIMEOUT', 'XILINX_VIVADO', 'XILINX_VITIS',
                'RDI_APPROOT', 'RDI_BASEROOT', 'RDI_DATADIR')
        env = {k: v for k, v in os.environ.items() if k.upper() in keys}
        home = work / '.home'
        home.mkdir(exist_ok=True)
        env.update(HOME=str(home), USERPROFILE=str(home), TMP=str(home), TEMP=str(home), TMPDIR=str(home))
        log_file = work / (str(self.calls) + '-' + tool + '.log')
        start = time.monotonic()
        status, rc = 'completed', None
        self.record(tool, phase='start', command=command, timeout_s=remaining,
                    evidence_dir=(Path('evidence')/work.name).as_posix(), work_dir=str(work))
        try:
            with log_file.open('wb') as log:
                def limits():
                    import resource
                    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
                    memory = int(runtime_value('agent', 'tool_memory_mb_linux', 'AGENT_TOOL_MEMORY_MB')) * 1024**2
                    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
                    cpu = max(1, math.ceil(remaining))
                    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu+1))
                    resource.setrlimit(resource.RLIMIT_FSIZE, (self.max_bytes, self.max_bytes))
                proc = subprocess.Popen(command, cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=os.name == 'posix',
                                        preexec_fn=limits if os.name == 'posix' else None)
                register(self.out, proc.pid, True)
                try:
                    while proc.poll() is None:
                        if time.monotonic()-start > remaining:
                            status = 'timeout'
                            break
                        size = directory_size(self.out) + directory_size(work)
                        if size > self.max_bytes:
                            status = 'artifact_budget'
                            break
                        time.sleep(0.05)
                finally:
                    # On Windows avoid taskkill after a normally exited PID (PID reuse).
                    if proc.poll() is None or os.name == 'posix':
                        terminate(proc.pid)
                    rc = proc.wait()
                    register(self.out, proc.pid, False)
        except OSError as exc:
            status = 'environment_error'
            log_file.write_text(type(exc).__name__ + ': ' + str(exc), encoding='utf-8')
        elapsed = time.monotonic()-start
        self.seconds += elapsed
        log = log_file.read_text(encoding='utf-8', errors='replace')
        if re.search(r'license.*(?:fail|not found|unavailable)|unable to.*license|ERROR.*license', log, re.I):
            status = 'license_error'
        elif re.search(r'Expected a switch|Unknown option|Unrecognized option', log, re.I):
            status = 'environment_error'
        report = self.ws.root / 'reports' / (work.name + '-' + tool + '.log')
        report.write_text(log[:262144], encoding='utf-8')
        self.record(tool, phase='end', status=status, rc=rc, seconds=elapsed,
                    log_path=report.relative_to(self.ws.root).as_posix())
        return {'status': status, 'rc': rc, 'tool': tool, 'seconds': elapsed,
                'log_path': report.relative_to(self.ws.root).as_posix(),
                'diagnostics': diagnostics(log), 'excerpt': log_excerpt(log),
                '_log': log}

    def operation(self, kind, args, source, contract):
        """在本地 EDA_TMP 下运行，归档小文件证据并清理仿真/综合临时树。"""
        local = '/var/tmp/eda' if os.name == 'posix' else str(Path(tempfile.gettempdir())/'rtl-eda')
        root = Path(os.environ.get('EDA_TMP',local)).resolve()
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='rtl-eda-',dir=root) as directory:
            work = Path(directory).resolve()
            if not work.is_relative_to(root):
                raise ValueError('EDA work directory escapes EDA_TMP')
            (work/'owner.json').write_text(json.dumps({'pid':os.getpid()}),encoding='utf-8')
            try:
                return self._operation(kind,args,source,contract,work)
            finally:
                evidence = self.out/'evidence'/work.name
                evidence.mkdir(parents=True,exist_ok=True)
                for path in work.iterdir():
                    if path.is_file() and path.suffix in ('.sv','.tcl','.json','.csv','.rpt','.log') and path.stat().st_size<=1048576:
                        shutil.copyfile(path,evidence/path.name)

    def _operation(self, kind, args, source, contract, work):
        """执行固定 Vivado 操作；模型数据不会作为任意 shell/Tcl 命令。"""
        if kind not in ('compile', 'lint', 'simulate', 'synth'):
            raise ValueError('Unknown EDA operation')
        top = identifier(args.get('top', 'TopModule'))
        if kind == 'simulate' and contract is None:
            raise ValueError('Set a public-specification test contract before simulation')
        source_text = source.read_text(encoding='utf-8')
        safe_hdl(source_text)
        (work / 'candidate.sv').write_text(source_text, encoding='utf-8')
        record = {'stage': kind, 'candidate_sha256': sha(source_text), 'top': top, 'runs': []}

        def invoke(tool, arguments):
            result = self.run(work, tool, arguments)
            log = result.pop('_log', '')
            record['runs'].append(result)
            return result, log

        def okay(result, log):
            return result['status'] == 'completed' and result['rc'] == 0 and not re.search(r'\b(?:ERROR|FATAL):', log)

        if kind in ('compile', 'simulate'):
            files = ['candidate.sv']
            if kind == 'simulate':
                tb = self.ws.path(args.get('testbench', 'tests/tb.sv'))
                if not tb.relative_to(self.ws.root).as_posix().startswith('tests/'):
                    raise ValueError('Testbench must be in tests/')
                tb_text = tb.read_text(encoding='utf-8')
                safe_hdl(tb_text)
                (work / 'tb.sv').write_text(tb_text, encoding='utf-8')
                files.append('tb.sv')
                self.ws.tests_locked = True
                record.update(testbench_sha256=sha(tb_text), contract_sha256=sha(json.dumps(contract, sort_keys=True)))
            result, log = invoke('xvlog', ['--sv', *files])
            if okay(result, log):
                elaborate_top = identifier(args.get('test_top', 'tb')) if kind == 'simulate' else top
                result, log = invoke('xelab', [elaborate_top, '-s', 'agent_snapshot', '-timescale', '1ns/1ps', '-mt', '2'])
            if not okay(result, log):
                if result['status'] != 'completed':
                    record['status'] = result['status']
                elif kind == 'simulate' and any('tb.sv' in row['message'] for row in diagnostics(log)):
                    record['status'] = 'tb_error'
                else:
                    record['status'] = 'compile_error'
            elif kind == 'compile':
                record['status'] = 'passed'
            else:
                token = secrets.token_hex(12)
                # Finite simulator runtime, plus process deadline, even for a broken TB.
                (work / 'run.tcl').write_text('run 100 us\nquit\n', encoding='utf-8')
                result, log = invoke('xsim', ['agent_snapshot', '-tclbatch', 'run.tcl', '-testplusarg', 'RTL_TOKEN_' + token])
                marker = re.search(r'RTL_SELFTEST_PASS token=' + token + r' checks=(\d+)', log)
                required = {item['id'] for item in contract['checks']}
                covered = set(re.findall(r'RTL_CHECK:([A-Za-z][A-Za-z0-9_]*)', log))
                record.update(checks=int(marker[1]) if marker else 0, covered=sorted(covered), required=sorted(required))
                if result['status'] != 'completed':
                    record['status'] = result['status']
                elif 'RTL_SELFTEST_FAIL' in log:
                    record['status'] = 'selftest_fail'
                elif re.search(r'\b(?:Fatal|Error):', log, re.I):
                    record['status'] = 'tb_error'
                elif okay(result, log) and marker and int(marker[1]) > 0 and required <= covered:
                    record['status'] = 'passed'
                else:
                    record['status'] = 'unknown'
        else:
            part = os.environ.get('FPGA_PART', 'xczu3eg-sbva484-1-e')
            if not re.fullmatch(r'[A-Za-z0-9_-]+', part):
                raise ValueError('Invalid configured FPGA part')
            script = 'set_param general.maxThreads 2\nread_verilog -sv candidate.sv\n'
            if kind == 'lint':
                script += f'synth_design -top {{{top}}} -part {part} -lint -file lint.csv\n'
            else:
                script += f'synth_design -top {{{top}}} -mode out_of_context -part {part}\n'
            script += 'puts "RTL_EDA_COMPLETE"\nexit\n'
            (work / 'run.tcl').write_text(script, encoding='utf-8')
            result, log = invoke('vivado', ['-mode', 'batch', '-nojournal', '-source', 'run.tcl'])
            record['status'] = 'passed' if okay(result, log) and 'RTL_EDA_COMPLETE' in log else (
                'design_error' if result['status'] == 'completed' else result['status'])
            if kind == 'lint' and record['status'] == 'passed':
                csv_file = work / 'lint.csv'
                if not csv_file.exists():
                    record['status'] = 'report_missing'
                else:
                    canonical, record['parser'] = canonical_lint(csv_file)
                    rows = lint_rows(canonical)
                    count = re.search(r'Total of\s+(\d+)\s+linter message', log, re.I)
                    if count and int(count[1]) != len(rows):
                        record['status'] = 'report_count_mismatch'
                    record['violations'] = rows[:30]
                    record['violation_count'] = len(rows)
                    report = self.ws.root / 'reports' / (work.name + '-lint.csv')
                    shutil.copyfile(canonical, report)
                    record['report_path'] = report.relative_to(self.ws.root).as_posix()
        record['functional_verified'] = False  # only the external official judge can establish that
        (work / 'result.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
        return record
