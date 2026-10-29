"""Explicit submission, with the normal candidate-selection policy."""
from agent.agent_tool_registry.base import Directive, Tool, ToolResult


class Submit(Tool):
    name = 'submit'
    description = 'Return the evidence-selected best candidate.'

    def execute(self, ctx, args):
        best = ctx.candidates.select_best()
        return ToolResult({'done':True, 'selected_sha256':best['sha256'] if best else None,
                           'official_verified':False}, Directive.SUBMIT)


TOOLS = (Submit,)
