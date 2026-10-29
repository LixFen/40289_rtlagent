"""Automatic slang check of saved RTL; diagnostic failure yields to the model."""
import os
import shutil
import subprocess
import time

from agent.agent_tool_registry.events import ArchiveError
from agent.agent_tool_registry.base import Directive
from agent.tooling.workspace import sha


def check_saved_rtl(ctx, item, testbench=None):
    executable = os.environ.get('SLANG_BIN') or shutil.which('slang')
    data = {'stage':'slang', 'candidate_sha256':item['sha256'], 'checked':False}
    if testbench:
        data['testbench_sha256'] = sha(ctx.workspace.read_text(testbench, errors='strict'))
    if not executable:
        data.update(status='unavailable', message='slang is not installed. Automatic check did not run; use the normal Vivado validation tools.')
    else:
        timeout = min(3.0, max(0.01, ctx.eda.end-time.monotonic()-0.1))
        command = [executable, '--error-limit', '20', '--color-diagnostics=false']
        command += ['--timescale', '1ns/1ps'] if testbench else ['--lint-only']
        command += ['src/solution.sv']
        if testbench:
            command.append(testbench)
        started = time.monotonic()
        try:
            process = subprocess.run(command, cwd=ctx.workspace.root, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, timeout=timeout)
            output = process.stdout.decode('utf-8', errors='replace')
            data.update(checked=True, rc=process.returncode,
                        status='passed' if process.returncode == 0 else (
                            'compile_error' if process.returncode == 1 else 'environment_error'))
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or b'').decode('utf-8', errors='replace')
            data.update(status='timeout', message='slang timed out; this is not a syntax verdict.')
        except OSError as exc:
            output = str(exc)
            data.update(status='unavailable', message='slang could not run; this is not a syntax verdict.')
        data['seconds'] = time.monotonic()-started
        suffix = '-'+data['testbench_sha256'] if testbench else ''
        report = 'reports/slang-'+item['sha256']+suffix+'.log'
        try:
            (ctx.workspace.root/report).write_text(output, encoding='utf-8')
        except OSError as exc:
            raise ArchiveError('Could not archive slang diagnostics') from exc
        data.update(diagnostics=output[:16000], diagnostics_truncated=len(output)>16000, log_path=report)
        ctx.verification.report_sources[report] = {
            'candidate_sha256':item['sha256'], 'testbench_sha256':data.get('testbench_sha256'),
            'stage':'slang', 'status':data['status']}
    ctx.events.record('testbench_precheck' if testbench else 'rtl_precheck', **data)
    if not testbench:
        ctx.candidates.record_result(item, 'slang', data)
    return data


def check_saved_testbench(ctx, item, testbench):
    if not item or not testbench.startswith('tests/'):
        return None
    if not ctx.workspace.path(testbench).is_file():
        return None
    return check_saved_rtl(ctx, item, testbench)


def add_testbench_precheck(ctx, result, testbench):
    precheck = check_saved_testbench(ctx, ctx.candidates.current(), testbench)
    if precheck:
        result.data['precheck'] = precheck
        if precheck['status'] in ('compile_error', 'timeout', 'environment_error'):
            result.directive = Directive.RETURN_TO_MODEL
            result.data['next_step'] = 'Fix the DUT/TB diagnostics, then rerun simulate; use amend_test for saved TB revisions.'
