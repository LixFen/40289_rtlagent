import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

from agent.loop.tools import execute_tools
from agent.autonomous import solve
from agent.loop.lifecycle import prepare_task
from agent.tooling.runtime import Runtime
from agent.agent_tool_registry import Directive, EventBus, HookDecision, Tool, ToolContext, ToolResult, build_registry
from agent.agent_tool_registry.events import ArchiveError, CriticalSubscriberError
from agent.agent_tool_registry.registry import ToolRegistry
from agent.agent_tool_registry.schema import parameters


class Example(Tool):
    name = 'example'
    description = 'Exercise lifecycle behavior.'

    def __init__(self, fail=None):
        self.fail = fail

    def step(self, ctx, phase):
        ctx.workspace.steps.append(phase)
        if self.fail == phase:
            raise ValueError('failure in '+phase)

    def validate(self, ctx, args):
        self.step(ctx, 'validate')

    def prepare(self, ctx, args):
        self.step(ctx, 'prepare')
        return {'resource':'owned'}

    def execute(self, ctx, prepared):
        self.step(ctx, 'execute')
        return ToolResult({'done':True, 'runs':[{'tool':'xsim','excerpt':'raw evidence'}]})

    def apply_result(self, ctx, prepared, result):
        self.step(ctx, 'apply_result')

    def observation(self, ctx, result):
        self.step(ctx, 'observation')
        result.data['runs'][0]['excerpt'] = 'projected'
        return result.data

    def cleanup(self, ctx, prepared):
        self.step(ctx, 'cleanup')


class RegistryTests(unittest.TestCase):
    def setup_registry(self, tool=None, record=None, authorize=None):
        rows = []
        def save(kind, **fields):
            rows.append(dict(tool=kind, **fields))
        ctx = ToolContext(SimpleNamespace(steps=[]), None, None, None, EventBus(record or save))
        return ToolRegistry([tool or Example()], ctx, authorize), ctx, rows

    def test_catalog_matches_manifest_and_returns_independent_instances(self):
        first, second = build_registry(), build_registry()
        manifest = json.loads((Path(__file__).resolve().parents[1]/'manifest.json').read_text())
        self.assertEqual(set(first.names), set(manifest['agent']['tools']))
        self.assertEqual(first.names, sorted(first.names))
        for name in first.names:
            self.assertIsNot(first.get(name), second.get(name))
        definition = first.definitions()[0]
        definition['function']['parameters']['properties']['injected'] = {'type':'string'}
        self.assertNotIn('injected', first.definitions()[0]['function']['parameters']['properties'])

    def test_duplicate_names_missing_execute_and_invalid_schema_fail_at_registration(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            ToolRegistry([Example(), Example()])
        class Missing(Tool):
            name, description = 'missing', 'No implementation'
        with self.assertRaisesRegex(ValueError, 'execute'):
            ToolRegistry([Missing()])
        class Invalid(Example):
            parameters = {'type':'object', 'additionalProperties':False, 'oneOf':[]}
        with self.assertRaisesRegex(ValueError, 'Unsupported schema'):
            ToolRegistry([Invalid()])
        class ArrayArgs(Example):
            parameters = {'type':'array', 'items':{'type':'string'}}
        with self.assertRaisesRegex(ValueError, 'object schema'):
            ToolRegistry([ArrayArgs()])

    def test_new_installed_module_is_discovered_without_editing_dispatch_tables(self):
        from agent.agent_tool_registry import tools
        module = SimpleNamespace(name='new_tool')
        with patch('agent.agent_tool_registry.pkgutil.iter_modules', return_value=[module]) as scan, \
             patch('agent.agent_tool_registry.importlib.import_module', return_value=SimpleNamespace(TOOLS=(Example,))):
            self.assertEqual(build_registry().names, ['example'])
        scan.assert_called_once_with(tools.__path__)

    def test_registry_is_in_delivery_and_freeze_allowlists(self):
        from tools.freeze import inventory
        root = Path(__file__).resolve().parents[1]
        self.assertIn('COPY agent ./agent', (root/'Dockerfile').read_text())
        self.assertIn('!agent/**', (root/'.dockerignore').read_text())
        self.assertIn('agent/agent_tool_registry/registry.py', inventory())

    def test_image_preparation_rejects_stale_tool_declaration(self):
        from serve.prepare_image import prepare
        from agent.config import load_runtime
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root/'manifest.json').read_text())
        manifest['agent']['tools'] = []
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target/'manifest.json').write_text(json.dumps(manifest))
            (target/'serve').mkdir()
            runtime = load_runtime(root)
            (target/'serve/runtime.json').write_text(json.dumps(runtime))
            for name in runtime['baseline_source']['canonical_sha256']:
                (target/name).write_bytes((root/name).read_bytes().replace(b'\r\n', b'\n'))
            with self.assertRaisesRegex(ValueError, 'Tool declaration mismatch'):
                prepare(target)

    def test_order_and_raw_archive_precede_projection(self):
        registry, ctx, rows = self.setup_registry()
        registry.add_hook('before_prepare', lambda ctx, call:ctx.workspace.steps.append('before_prepare'))
        registry.add_hook('after_result', lambda ctx, call:ctx.workspace.steps.append('after_result'))
        result = registry.invoke('example', {})
        self.assertEqual(ctx.workspace.steps, ['validate','before_prepare','prepare','execute','apply_result','after_result','cleanup'])
        raw = next(row for row in rows if row['tool'] == 'tool_result')
        projected = registry.observe('example', result)
        self.assertEqual(projected['runs'][0]['excerpt'], 'projected')
        self.assertEqual(result.data['runs'][0]['excerpt'], 'raw evidence')
        self.assertEqual(raw['result']['runs'][0]['excerpt'], 'raw evidence')
        self.assertEqual([row['event'] for row in rows if row['tool']=='tool_event'], ['tool.started','tool.completed'])

    def test_tool_can_install_its_hooks_without_changing_runtime(self):
        class Configured(Example):
            def configure(self, ctx, add_hook):
                add_hook('before_prepare', lambda *_:HookDecision(cancel=True, reason='configured'))
                ctx.events.subscribe('tool.completed', lambda event:ctx.workspace.steps.append('notified'))
        registry, ctx, _ = self.setup_registry(Configured())
        self.assertEqual(registry.invoke('example', {}).status, 'cancelled')
        self.assertEqual(ctx.workspace.steps, ['validate','notified'])

    def test_cleanup_runs_on_prepare_execute_apply_and_cleanup_failures(self):
        for phase in ('prepare','execute','apply_result','cleanup'):
            with self.subTest(phase=phase):
                registry, ctx, rows = self.setup_registry(Example(phase))
                with self.assertRaisesRegex(ValueError, 'failure in '+phase):
                    registry.invoke('example', {})
                self.assertEqual(ctx.workspace.steps[-1], 'cleanup')
                self.assertEqual(rows[-1]['event'], 'tool.failed')
                self.assertEqual(rows[-1]['payload']['phase'], phase)
                if phase in ('apply_result','cleanup'):
                    self.assertTrue(any(row['tool']=='tool_result' for row in rows))

    def test_invalid_result_still_cleans_up_and_reports_contract_error(self):
        class InvalidResult(Example):
            def execute(self, ctx, prepared):
                return {'done':True}
        registry, ctx, rows = self.setup_registry(InvalidResult())
        with self.assertRaisesRegex(TypeError, 'ToolResult'):
            registry.invoke('example', {})
        self.assertEqual(ctx.workspace.steps[-1], 'cleanup')
        self.assertEqual(rows[-1]['payload']['phase'], 'execute')

    def test_recursive_event_publication_is_rejected_without_looping(self):
        registry, _, rows = self.setup_registry()
        events = registry.context.events
        events.subscribe('tool.started', lambda _:events.emit('tool.started'))
        registry.invoke('example', {})
        errors = [row for row in rows if row['tool']=='subscriber_error']
        self.assertEqual(len(errors), 1)
        self.assertIn('Recursive', errors[0]['message'])

    def test_schema_is_validated_before_authorization_hooks_or_execution(self):
        class IntegerTool(Example):
            parameters = parameters({'count':{'type':'integer','minimum':0}}, ('count',))
        effects = []
        registry, ctx, _ = self.setup_registry(IntegerTool(), authorize=lambda *_:effects.append('policy'))
        registry.add_hook('before_prepare', lambda *_:effects.append('hook'))
        for args in ({}, {'count':True}, {'count':-1}, {'count':0,'extra':1}):
            with self.assertRaises(ValueError):
                registry.invoke('example', args)
        self.assertEqual(effects, [])
        self.assertEqual(ctx.workspace.steps, [])

    def test_disabled_tools_and_budget_cannot_be_bypassed_by_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime('', '', Path(directory), time.monotonic()+10, lambda *_a,**_k:None)
            calls = []
            runtime.registry.add_hook('before_prepare', lambda *_:calls.append('hook'))
            runtime.disabled.add('list_files')
            with self.assertRaisesRegex(ValueError, 'disabled'):
                runtime.dispatch({'tool':'list_files'})
            runtime.disabled.clear()
            runtime.max_tools = 0
            result = runtime.invoke({'tool':'list_files'})
            self.assertEqual(result.directive, Directive.STOP_RESOURCE)
            self.assertEqual(calls, [])

    def test_cancel_hook_skips_execution_and_cannot_forge_submission(self):
        registry, ctx, rows = self.setup_registry()
        registry.add_hook('before_prepare', lambda *_:HookDecision(cancel=True, reason='not needed'))
        result = registry.invoke('example', {})
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(ctx.workspace.steps, ['validate'])
        self.assertTrue(any(row['tool']=='tool_hook' for row in rows))
        registry, _, _ = self.setup_registry()
        registry.add_hook('before_prepare', lambda *_:HookDecision(directive=Directive.SUBMIT))
        with self.assertRaisesRegex(TypeError, 'HookDecision'):
            registry.invoke('example', {})

    def test_after_result_hook_can_stop_after_committing_evidence(self):
        registry, ctx, _ = self.setup_registry()
        def stop(ctx, call):
            self.assertIn('apply_result', ctx.workspace.steps)
            with self.assertRaises(TypeError):
                call['result']['done'] = False
            return HookDecision(directive=Directive.STOP_RESOURCE, reason='stop after this tool')
        registry.add_hook('after_result', stop)
        self.assertEqual(registry.invoke('example', {}).directive, Directive.STOP_RESOURCE)

    def test_event_snapshots_are_immutable_and_optional_failures_are_diagnostics(self):
        registry, _, rows = self.setup_registry()
        def broken(event):
            event.payload['args']['injected'] = True
        registry.context.events.subscribe('tool.started', broken)
        self.assertEqual(registry.invoke('example', {}).status, 'ok')
        self.assertTrue(any(row['tool']=='subscriber_error' for row in rows))

    def test_critical_subscriber_failure_fails_call_and_keeps_archived_result(self):
        registry, _, rows = self.setup_registry()
        def broken(_):
            raise ValueError('critical observer')
        registry.context.events.subscribe('tool.completed', broken, critical=True)
        with self.assertRaisesRegex(CriticalSubscriberError, 'critical observer'):
            registry.invoke('example', {})
        self.assertTrue(any(row['tool']=='tool_result' for row in rows))

    def test_recursive_tool_invocation_fails_without_reexecuting(self):
        registry, ctx, _ = self.setup_registry()
        registry.add_hook('before_prepare', lambda *_:registry.invoke('example', {}))
        with self.assertRaisesRegex(RuntimeError, 'Recursive'):
            registry.invoke('example', {})
        self.assertEqual(ctx.workspace.steps, ['validate'])

    def test_archive_failure_propagates_out_of_batch(self):
        def broken(kind, **_):
            if kind == 'tool_result':
                raise OSError('disk full')
        registry, _, _ = self.setup_registry(record=broken)
        runtime = SimpleNamespace(tool_calls=0, max_tools=10, registry=registry,
            invoke=lambda action, **meta:registry.invoke(action['tool'], action.get('args', {}), **meta))
        with self.assertRaises(ArchiveError):
            execute_tools(runtime, [({'tool':'example','args':{}}, {'id':'one'})],
                          time.monotonic()+5, lambda *_a,**_k:None, 1)

    def test_only_explicit_directive_stops_batch_and_remaining_ids_are_preserved(self):
        class Finish(Example):
            name = 'finish'
            def execute(self, ctx, prepared):
                return ToolResult({}, Directive.SUBMIT)
        _, ctx, rows = self.setup_registry()
        registry = ToolRegistry([Example(), Finish()], ctx)
        runtime = SimpleNamespace(tool_calls=0, max_tools=10, registry=registry,
            invoke=lambda action, **meta:registry.invoke(action['tool'], action.get('args', {}), **meta))
        calls = [({'tool':name,'args':{}}, {'id':str(index)}) for index,name in enumerate(['example','finish','example'])]
        result = execute_tools(runtime, calls, time.monotonic()+5,
            lambda kind,**fields:rows.append(dict(tool=kind,**fields)), 1)
        self.assertEqual(result.stop_reason, 'submitted')
        self.assertEqual(result.observations[-1]['status'], 'not_executed')
        self.assertEqual([row['tool_call_id'] for row in rows if row['tool']=='action'], ['0','1','2'])
        self.assertEqual(ctx.workspace.steps.count('execute'), 1)

    def test_candidate_archive_failure_aborts_before_next_tool(self):
        rows = []
        def broken(kind, **fields):
            if kind == 'candidate':
                raise OSError('candidate event not saved')
            rows.append(dict(tool=kind, **fields))
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime('', '', Path(directory), time.monotonic()+10, broken)
            calls = [({'tool':'write_file', 'args':{'path':'src/solution.sv',
                       'content':'module TopModule; endmodule'}}, {'id':'write'}),
                     ({'tool':'list_files','args':{}}, {'id':'next'})]
            with self.assertRaises(ArchiveError):
                execute_tools(runtime, calls, time.monotonic()+10, broken, 1)
            self.assertEqual(runtime.tool_calls, 1)
            self.assertFalse(any(row.get('tool_call_id')=='next' for row in rows))

    def test_completed_subscriber_failure_preserves_terminal_decision(self):
        for tool, expected in [('submit','submitted'), ('list_files','resource_budget')]:
            with self.subTest(tool=tool), tempfile.TemporaryDirectory() as directory:
                rows = []
                def record(kind, **fields):
                    rows.append(dict(tool=kind, **fields))
                runtime = Runtime('', '', Path(directory), time.monotonic()+10, record)
                if tool == 'list_files':
                    runtime.registry.add_hook('before_prepare', lambda ctx, call:HookDecision(directive=Directive.STOP_RESOURCE) if call['tool']==tool else None)
                def broken(event):
                    if event.payload['name'] == tool:
                        raise ValueError('critical observer after terminal decision')
                runtime.events.subscribe('tool.completed', broken, critical=True)
                calls = [({'tool':tool, 'args':{}}, {'id':'terminal'}),
                         ({'tool':'write_file','args':{'path':'src/solution.sv',
                          'content':'module TopModule; endmodule'}}, {'id':'must_not_run'})]
                batch = execute_tools(runtime, calls, time.monotonic()+10, record, 1)
                self.assertEqual(batch.stop_reason, expected)
                self.assertEqual(batch.observations[-1]['status'], 'not_executed')
                self.assertEqual(runtime.tool_calls, 1)

    def test_new_subscribers_wait_until_the_next_event_dispatch(self):
        events = EventBus(lambda *_a, **_k:None)
        calls = []
        def growing(event):
            calls.append(event.name)
            if len(calls) < 4:
                events.subscribe('tick', growing)
        events.subscribe('tick', growing)
        events.emit('tick')
        self.assertEqual(len(calls), 1)
        events.emit('tick')
        self.assertEqual(len(calls), 3)

    def test_eda_and_verification_archive_callbacks_abort_batch(self):
        for component in ('eda','verification'):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as directory:
                marker = component+'_record'
                def record(kind, **fields):
                    if kind == marker:
                        raise OSError('mandatory component event not saved')
                runtime = Runtime('', '', Path(directory), time.monotonic()+10, record)
                class RecordComponent(Example):
                    name = 'record_component'
                    def validate(self, ctx, args):
                        pass
                    def prepare(self, ctx, args):
                        return args
                    def execute(self, ctx, args):
                        getattr(ctx, component).record(marker)
                        return ToolResult({})
                    def cleanup(self, ctx, prepared):
                        pass
                runtime.registry = ToolRegistry([RecordComponent()], runtime.registry.context, runtime._authorize)
                calls = [({'tool':'record_component','args':{}}, {'id':str(i)}) for i in range(2)]
                with self.assertRaises(ArchiveError):
                    execute_tools(runtime, calls, time.monotonic()+10, record, 1)
                self.assertEqual(runtime.tool_calls, 1)

    def test_critical_failure_without_a_terminal_result_stops_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime('', '', Path(directory), time.monotonic()+10, lambda *_a,**_k:None)
            def broken(_):
                raise ValueError('critical start failure')
            runtime.events.subscribe('tool.started', broken, critical=True)
            calls = [({'tool':'list_files','args':{}}, {'id':str(i)}) for i in range(2)]
            batch = execute_tools(runtime, calls, time.monotonic()+10, lambda *_a,**_k:None, 1)
            self.assertEqual(batch.stop_reason, 'critical_subscriber_error')
            self.assertEqual(batch.observations[-1]['status'], 'not_executed')
            self.assertEqual(runtime.tool_calls, 1)

    def test_hooks_added_during_dispatch_wait_for_the_next_call(self):
        registry, _, _ = self.setup_registry()
        calls = []
        def growing(ctx, call):
            calls.append(call['invocation_id'])
            if len(calls) < 4:
                registry.add_hook('before_prepare', growing)
        registry.add_hook('before_prepare', growing)
        registry.invoke('example', {})
        self.assertEqual(len(calls), 1)
        registry.invoke('example', {})
        self.assertEqual(len(calls), 3)

    def test_invocation_id_correlates_components_hooks_events_actions_and_subscriber_errors(self):
        rows = []
        def record(kind, **fields):
            rows.append(dict(tool=kind, **fields))
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime('', '', Path(directory), time.monotonic()+10, record)
            hook_ids, event_ids = [], []
            def hook(ctx, call):
                hook_ids.append(call['invocation_id'])
                ctx.eda.record('eda_marker')
                ctx.verification.record('verification_marker')
            def broken(event):
                event_ids.append(event.invocation_id)
                raise ValueError('optional subscriber failure')
            runtime.registry.add_hook('before_prepare', hook)
            subscriber_id = runtime.events.subscribe('tool.completed', broken)
            for index, wire in enumerate(('a','b'), 1):
                action = {'tool':'write_file', 'args':{'path':'src/solution.sv',
                          'content':'module TopModule; wire '+wire+'; endmodule'}}
                execute_tools(runtime, [(action, {'id':'reused_native_id'})], time.monotonic()+10, record, index)
            actions = [row for row in rows if row['tool']=='action']
            ids = [row['invocation_id'] for row in actions]
            self.assertEqual(len(set(ids)), 2)
            self.assertEqual(ids, hook_ids)
            self.assertEqual(ids, event_ids)
            for kind in ('candidate','eda_marker','verification_marker','tool_hook','tool_result','subscriber_error'):
                self.assertEqual({row['invocation_id'] for row in rows if row['tool']==kind}, set(ids))
            errors = [row for row in rows if row['tool']=='subscriber_error']
            self.assertTrue(all(row['subscriber_id']==subscriber_id and 'broken' in row['subscriber'] for row in errors))
            self.assertIsNone(runtime.events.invocation_id)

    def test_model_request_archive_failure_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            task, out = Path(directory)/'task', Path(directory)/'out'
            task.mkdir()
            (task/'prompt.txt').write_text('Implement module TopModule.')
            def prepare(task, out):
                prompt, interface, out, save = prepare_task(task, out)
                def record(kind, **fields):
                    if kind == 'llm_request':
                        raise OSError('request event not saved')
                    save(kind, **fields)
                return prompt, interface, out, record
            with patch('agent.autonomous.prepare_task', side_effect=prepare), \
                 patch('agent.llm.tokens.TokenCounter.count_input', return_value=(10,'test_tokenizer')), \
                 patch('agent.llm.client.urllib.request.urlopen') as http:
                with self.assertRaises(ArchiveError):
                    solve(task, out)
            http.assert_not_called()
            summary = json.loads((out/'summary.json').read_text())
            self.assertEqual(summary['reason'], 'archive_error')
            self.assertEqual(summary['model_calls'], 1)
