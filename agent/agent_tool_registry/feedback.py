"""Actionable verification feedback shared by registered tools."""
from agent.tooling.checks import expected_module
from agent.tooling.workspace import sha


def simulation_action(ctx):
    last = ctx.verification.last_simulation or {}
    return {'tool':'simulate', 'args':{
        'top':last.get('top') or expected_module(ctx.candidates.prompt, ctx.candidates.interface) or 'TopModule',
        'testbench':last.get('testbench', 'tests/tb.sv'),
        'test_top':last.get('test_top', 'tb')}}


def remember_reports(ctx, result):
    for run in result.get('runs', []):
        if 'log_path' in run:
            ctx.verification.report_sources[run['log_path']] = {
                key:result.get(key) for key in ('candidate_sha256','testbench_sha256','stage','status')}


def report_feedback(ctx, path, text, window):
    source = ctx.verification.report_sources.get(path)
    current = ctx.candidates.current()
    digest = current['sha256'] if current else None
    key = (path, sha(text), digest, window)
    reads = ctx.verification.report_reads
    reads[key] = reads.get(key, 0)+1
    info = {'provenance_known':source is not None, 'current_candidate_sha256':digest,
            'read_count':reads[key], 'unchanged_since_previous_read':reads[key]>1}
    if source is not None:
        info.update(source)
        info['matches_current_candidate'] = digest is not None and source['candidate_sha256'] == digest
    simulation = current['evidence'].get('simulate') if current else None
    info['current_simulation_status'] = simulation['status'] if simulation else 'not_verified'
    if source and source['stage'] == 'simulate' and not info['matches_current_candidate']:
        info['instruction'] = 'This log belongs to an older RTL candidate. It does not test the current source. Rerun the frozen TB to obtain current evidence.'
        info['next_action'] = simulation_action(ctx)
    elif reads[key]>1:
        info['instruction'] = 'This unchanged report adds no new evidence. Use the existing diagnostics to repair the RTL, then rerun the existing TB; rereading will not reveal unrecorded values.'
    return info
