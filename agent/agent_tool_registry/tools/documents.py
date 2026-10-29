"""Local diagnostic and document tools."""
import re

from agent.paths import ROOT
from agent.tooling.eda import diagnostics
from agent.agent_tool_registry.base import Tool, ToolResult
from agent.agent_tool_registry.schema import TEXT, parameters
from agent.agent_tool_registry.feedback import report_feedback


class AnalyzeLog(Tool):
    name = 'analyze_log'
    description = 'Extract actual tool diagnostics from a reports file.'
    parameters = parameters({'path':TEXT}, ('path',))

    def execute(self, ctx, args):
        name = args['path']
        if not name.startswith('reports/'):
            raise ValueError('Analyze only tool reports')
        log = ctx.workspace.read_text(name)
        return ToolResult({'diagnostics':diagnostics(log), 'note':'Actual tool diagnostics only; not a correctness verdict',
                           'report':report_feedback(ctx, name, log, ('diagnostics',))})


class SearchDocs(Tool):
    name = 'search_docs'
    description = 'Search frozen local HDL documentation.'
    parameters = parameters({'query':TEXT}, ('query',))

    def execute(self, ctx, args):
        query = args['query']
        if not 1 <= len(query) <= 300:
            raise ValueError('Invalid query')
        words = set(re.findall(r'[\w-]+', query.lower()))
        hits = []
        for file in (ROOT/'skill/knowledge').glob('*.md'):
            for chunk in file.read_text(encoding='utf-8').split('\n## '):
                score = sum(word in chunk.lower() for word in words)
                if score:
                    hits.append({'document':file.name, 'score':score, 'text':chunk[:2200]})
        return ToolResult({'hits':sorted(hits, key=lambda hit:hit['score'], reverse=True)[:3],
                           'backend':'frozen-local-markdown', 'network':False})


TOOLS = (AnalyzeLog, SearchDocs)
