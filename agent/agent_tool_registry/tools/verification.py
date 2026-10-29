"""Verification tool contracts and lifecycle; shared services enforce invariants."""
import json

from agent.tooling.checks import expected_module
from agent.tooling.testbench import build_testbench, normalize_stimulus
from agent.tooling.workspace import sha
from agent.agent_tool_registry.base import Directive, Tool, ToolResult
from agent.agent_tool_registry.schema import CHECKS, TEXT, parameters
from agent.agent_tool_registry.tools.files import file_metadata
from agent.agent_tool_registry.feedback import remember_reports, simulation_action
from agent.agent_tool_registry.rtl_check import add_testbench_precheck, check_saved_testbench


DUT_TOP = dict(TEXT, description='DUT module name from the specification (e.g. TopModule).')


class SetContract(Tool):
    name = 'set_contract'
    description = 'Optionally register immutable public requirements.'
    parameters = parameters({'contract':parameters({'checks':CHECKS}, ('checks',))}, ('contract',))

    def execute(self, ctx, args):
        contract = args['contract']
        ctx.workspace.set_contract(contract)
        return ToolResult({'contract_sha256':sha(json.dumps(contract, sort_keys=True)),
                           'checks':sorted(item['id'] for item in contract['checks'])})


class AmendTest(Tool):
    name = 'amend_test'
    description = 'Revise a saved TB after any simulation result. Use old/new patch or full content with reason, then rerun.'
    parameters = parameters({'path':TEXT, 'old':TEXT, 'new':TEXT, 'content':TEXT, 'reason':TEXT}, ('path','reason'))

    def validate(self, ctx, args):
        ws = ctx.workspace
        if not ws.tests_locked or not ws.contract:
            raise ValueError('Use ordinary writes before first simulation')
        if not args['reason'].strip():
            raise ValueError('Test amendment requires a reason explaining the change')
        if not args['path'].startswith('tests/'):
            raise ValueError('Amendment only applies to tests/')

    def prepare(self, ctx, args):
        old = ctx.workspace.read_text(args['path'], errors='strict')
        if 'content' in args and ('old' in args or 'new' in args):
            raise ValueError('Choose full content or a unique patch, not both')
        if 'content' in args:
            content, mode = args['content'], 'full'
        else:
            before, after = args.get('old'), args.get('new')
            if not before or not isinstance(after, str) or old.count(before) != 1:
                raise ValueError('Test amendment patch must match exactly once')
            content, mode = old.replace(before, after, 1), 'patch'
        return dict(args=args, old=old, content=content, mode=mode)

    def execute(self, ctx, prepared):
        ctx.workspace.tests_locked = False
        try:
            ctx.workspace.write_text(prepared['args']['path'], prepared['content'])
        finally:
            ctx.workspace.tests_locked = True
        return ToolResult(file_metadata(prepared['args']['path'], prepared['content']))

    def apply_result(self, ctx, prepared, result):
        ctx.candidates.invalidate_simulations()
        if ctx.verification.pending_counterexample:
            ctx.verification.record('counterexample_invalidated',
                reason='testbench_amended', counterexample=ctx.verification.pending_counterexample)
            ctx.verification.pending_counterexample = None
        ctx.verification.record('test_amendment', old_sha256=sha(prepared['old']),
            new_sha256=result.data['sha256'], reason=prepared['args']['reason'], edit_mode=prepared['mode'])
        ctx.candidates.select_best()
        add_testbench_precheck(ctx, result, prepared['args']['path'])


class CheckInterface(Tool):
    name = 'check_interface'
    description = 'Check saved RTL against the public interface.'

    def execute(self, ctx, args):
        item = ctx.candidates.current()
        if not item:
            raise ValueError('Write src/solution.sv first')
        return ToolResult({'errors':item['interface_errors'], 'checked_ports':item['checked_ports']})


class EdaTool(Tool):
    parameters = parameters({'top':DUT_TOP})

    def prepare(self, ctx, args):
        item = ctx.candidates.current()
        if not item:
            raise ValueError('Write src/solution.sv first')
        if item['interface_errors']:
            return dict(item=item, args=args, blocked=True)
        expected = expected_module(ctx.candidates.prompt, ctx.candidates.interface)
        if expected and args.get('top', 'TopModule') != expected:
            raise ValueError('Tool top must match the public specification')
        args = self.prepare_inputs(ctx, args)
        return dict(item=item, args=args, blocked=False)

    def prepare_inputs(self, ctx, args):
        return args

    def execute(self, ctx, prepared):
        if prepared['blocked']:
            return ToolResult({'status':'interface_error', 'errors':prepared['item']['interface_errors']})
        data = ctx.eda.operation(self.name, prepared['args'], ctx.candidates.source, ctx.workspace.contract)
        directive = Directive.STOP_RESOURCE if data['status'] in ('budget','artifact_budget') else Directive.CONTINUE
        return ToolResult(data, directive)

    def apply_result(self, ctx, prepared, result):
        if prepared['blocked']:
            return
        pending = ctx.verification.pending_counterexample
        self.record_verification(ctx, prepared, result)
        remember_reports(ctx, result.data)
        ctx.candidates.record_result(prepared['item'], self.name, result.data)
        best = ctx.candidates.select_best()
        current_pending = ctx.verification.pending_counterexample
        if current_pending and current_pending != pending:
            ctx.events.emit('verification.counterexample_found', counterexample=current_pending)
        elif pending and not current_pending:
            ctx.events.emit('verification.counterexample_resolved', candidate_sha256=prepared['item']['sha256'])
        ctx.events.emit('candidate.updated', current_sha256=prepared['item']['sha256'],
                        selected_sha256=best['sha256'] if best else None)

    def record_verification(self, ctx, prepared, result):
        """Optional verification-specific state changes before evidence selection."""


class Compile(EdaTool):
    name = 'compile'
    description = 'Compile and elaborate saved RTL with Vivado.'


class Lint(EdaTool):
    name = 'lint'
    description = 'Run Vivado synthesis lint.'


class Simulate(EdaTool):
    name = 'simulate'
    description = 'Create a small self-checking TB: at most 8 representative scenarios initially, using tasks/bounded loops instead of copied case blocks. Use check_equal(actual,expected,id,inputs) or check(condition,id). For frozen TB reruns pass only top/testbench/test_top; omit content and fragments.'
    parameters = parameters({'top':DUT_TOP, 'testbench_declarations':TEXT, 'testbench_stimulus':TEXT,
        'checks':CHECKS, 'testbench':TEXT,
        'test_top':dict(TEXT, description='Testbench module name (default: tb).'), 'testbench_content':TEXT})

    def execute(self, ctx, prepared):
        if prepared['blocked']:
            return super().execute(ctx, prepared)
        testbench = prepared['args'].get('testbench', 'tests/tb.sv')
        precheck = check_saved_testbench(ctx, prepared['item'], testbench)
        if precheck and precheck['status'] in ('compile_error', 'timeout', 'environment_error'):
            ctx.workspace.tests_locked = True
            return ToolResult({'stage':'slang', 'status':'tb_error' if precheck['status'] == 'compile_error' else precheck['status'],
                'candidate_sha256':precheck['candidate_sha256'], 'testbench_sha256':precheck['testbench_sha256'],
                'precheck':precheck, 'runs':[], 'next_step':'Fix the DUT/TB diagnostics, then rerun simulate.'},
                Directive.RETURN_TO_MODEL)
        result = super().execute(ctx, prepared)
        if precheck:
            result.data['precheck'] = precheck
        return result

    def prepare_inputs(self, ctx, args):
        ws = ctx.workspace
        if ws.tests_locked and any(key in args for key in ('testbench_declarations','testbench_stimulus','testbench_content')):
            action = json.dumps(simulation_action(ctx))
            raise ValueError('TB is frozen: this call attempted to overwrite it; no simulation ran. '
                             'Rerun the existing TB without content/fragments: '+action+
                             '. Use amend_test with a reason to revise the saved TB after any simulation result.')
        fragment_mode = 'testbench_declarations' in args or 'testbench_stimulus' in args
        if fragment_mode and 'testbench_content' in args:
            raise ValueError('Choose fragment mode or a complete testbench, not both')
        if fragment_mode:
            stimulus = args.get('testbench_stimulus')
            if normalize_stimulus(stimulus) != stimulus:
                ctx.verification.record('testbench_normalization', transformation='outer-initial-to-procedural-block',
                    reason='Fragment executes inside the harness initial block; all stimulus/check statements retained')
            if ws.contract is None:
                ws.set_contract({'checks':args.get('checks')})
            ws.write_text('tests/tb.sv', build_testbench(args.get('testbench_declarations'), stimulus, ws.contract['checks']))
            args = dict(args, testbench='tests/tb.sv', test_top='tb')
        if 'testbench_content' in args:
            testbench = args.get('testbench', 'tests/tb.sv')
            if not isinstance(testbench, str) or not testbench.startswith('tests/'):
                raise ValueError('Testbench must be in tests/')
            ws.write_text(testbench, args['testbench_content'])
        if ws.contract is None and 'checks' in args:
            ws.set_contract({'checks':args['checks']})
        return args

    def record_verification(self, ctx, prepared, result):
        state, data = ctx.verification, result.data
        data.update(testbench=prepared['args'].get('testbench', 'tests/tb.sv'),
                    test_top=prepared['args'].get('test_top', 'tb'))
        state.last_simulation = data
        if data['status'] == 'selftest_fail':
            failures = [row for run in data.get('runs', []) for row in run.get('diagnostics', [])
                        if 'RTL_SELFTEST_FAIL' in row.get('message', '')]
            state.pending_counterexample = {
                'failed_candidate_sha256':prepared['item']['sha256'],
                'testbench_sha256':data.get('testbench_sha256'), 'diagnostics':failures[:3],
                'log_paths':[run['log_path'] for run in data.get('runs', []) if 'log_path' in run],
                'instruction':'Check whether this failure comes from the DUT or TB. Repair the DUT or revise an incorrect TB using amend_test with a reason, then rerun. A file edit alone is not a verified repair.',
                'next_action':simulation_action(ctx)}
            data['failure_values_recorded'] = any('expected=' in row['message'] and 'actual=' in row['message'] for row in failures)
            if not data['failure_values_recorded']:
                data['diagnostic_note'] = 'This TB recorded a failed condition but no expected/actual values. Rereading its log cannot recover unrecorded values. Use amend_test to add diagnostics if needed, then rerun.'
        elif data['status'] == 'passed' and state.pending_counterexample:
            pending = state.pending_counterexample
            if (data.get('testbench_sha256') == pending['testbench_sha256']
                    and prepared['item']['sha256'] != pending['failed_candidate_sha256']):
                state.pending_counterexample = None
        if data['status'] == 'tb_error':
            data['testbench_source'] = ctx.workspace.read_text(prepared['args'].get('testbench', 'tests/tb.sv'), errors='strict')[:6000]
            data['repair_hint'] = ('Tool-confirmed TB error: use these diagnostics and TB source. '
                'Prefer amend_test with path, unique old/new text and reason, then simulate the existing TB. '
                'Driven DUT inputs must be reg/logic, declared before the DUT instance. '
                'Do not reread unchanged files or modify DUT to compensate for a TB defect.')


class Synth(EdaTool):
    name = 'synth'
    description = 'Synthesize saved DUT with Vivado.'


TOOLS = (SetContract, AmendTest, CheckInterface, Compile, Lint, Simulate, Synth)
