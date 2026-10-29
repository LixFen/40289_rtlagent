"""File tool contracts and behavior, using the shared workspace capability."""
from agent.agent_tool_registry.base import Directive, Tool, ToolResult
from agent.agent_tool_registry.schema import TEXT, parameters
from agent.tooling.workspace import sha
from agent.agent_tool_registry.feedback import report_feedback, simulation_action
from agent.agent_tool_registry.rtl_check import add_testbench_precheck, check_saved_rtl


def file_metadata(path, content):
    """Tool-facing file result; storage primitives do not choose response fields."""
    return {'path':path, 'sha256':sha(content), 'bytes':len(content.encode('utf-8'))}


class ListFiles(Tool):
    name = 'list_files'
    description = 'List task workspace files.'

    def execute(self, ctx, args):
        return ToolResult({'files':ctx.workspace.listing()})


class ReadFile(Tool):
    name = 'read_file'
    description = 'Read a task file window.'
    parameters = parameters({'path':TEXT, 'offset':{'type':'integer'}, 'limit':{'type':'integer'}}, ('path',))

    def execute(self, ctx, args):
        offset, limit = args.get('offset', 0), args.get('limit', 12000)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16000:
            raise ValueError('Invalid read range')
        text = ctx.workspace.read_text(args['path'])
        data = {'path':args['path'], 'content':text[offset:offset+limit],
                'total_chars':len(text), 'sha256':sha(text)}
        if args['path'].startswith('reports/'):
            data['report'] = report_feedback(ctx, args['path'], text, ('read',offset,limit))
        return ToolResult(data)


class WriteFile(Tool):
    name = 'write_file'
    description = 'Save complete RTL or notes in the task workspace.'
    parameters = parameters({'path':TEXT, 'content':TEXT}, ('path','content'))

    def execute(self, ctx, args):
        ctx.workspace.write_text(args['path'], args['content'])
        return ToolResult(file_metadata(args['path'], args['content']))

    def apply_result(self, ctx, args, result):
        item = ctx.candidates.current()
        precheck = check_saved_rtl(ctx, item) if args['path'] == 'src/solution.sv' else None
        best = ctx.candidates.select_best()
        if args['path'].startswith('tests/'):
            add_testbench_precheck(ctx, result, args['path'])
        if args['path'] == 'src/solution.sv':
            result.data['precheck'] = precheck
            code = ctx.workspace.read_text(args['path'], errors='strict')
            result.data.update(source_content=code[:12000], source_chars=len(code),
                source_truncated=len(code)>12000,
                next_step='Test this RTL now using simulate with compact testbench_declarations and testbench_stimulus. Limit the first TB to 8 representative scenarios; reuse a drive/wait/check task and bounded loops. Do not copy per-case blocks or enumerate many vectors; group check ids by requirement. Use check_equal(actual,expected,id,$sformatf("inputs=...")) for values, or check(condition,id). The harness supplies module tb, comparison tasks, token, counters, watchdog and PASS markers. Drive off the active edge and sample after NBA. Run the small TB immediately; do not rewrite RTL without a concrete defect.')
            if ctx.workspace.tests_locked:
                result.data.update(next_step='Test this RTL by reusing the saved TB with next_action. Omit testbench content/fragments on reruns. If the TB is incorrect, revise it using amend_test with a reason, then rerun.',
                                   next_action=simulation_action(ctx))
            if precheck['status'] in ('compile_error','timeout','environment_error'):
                result.directive = Directive.RETURN_TO_MODEL
                result.data.pop('next_action', None)
                result.data['next_step'] = ('Automatic slang check did not pass. Read precheck diagnostics and repair the saved RTL with write_file or patch_file before running further tools. '
                                            'For timeout/environment errors, do not change RTL merely to silence the checker; Vivado remains the validation authority.')
            ctx.events.emit('candidate.updated', current_sha256=ctx.candidates.current()['sha256'],
                            selected_sha256=best['sha256'] if best else None)


class PatchFile(WriteFile):
    name = 'patch_file'
    description = 'Replace exactly one occurrence in a writable task file.'
    parameters = parameters({'path':TEXT, 'old':TEXT, 'new':TEXT}, ('path','old','new'))

    def execute(self, ctx, args):
        content = ctx.workspace.read_text(args['path'], errors='strict', for_write=True)
        old, new = args['old'], args['new']
        if not old or content.count(old) != 1:
            raise ValueError('Patch old text must match exactly once')
        return super().execute(ctx, {'path':args['path'], 'content':content.replace(old, new, 1)})


TOOLS = (ListFiles, ReadFile, WriteFile, PatchFile)
