"""Tool lifecycle and explicit control decisions; no controller internals exposed."""
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent.agent_tool_registry.observations import model_observation


class Directive(str, Enum):
    CONTINUE = 'continue'
    RETURN_TO_MODEL = 'return_to_model'
    SUBMIT = 'submit'
    STOP_RESOURCE = 'resource_budget'


@dataclass
class ToolResult:
    data: dict
    directive: Directive = Directive.CONTINUE
    invocation_id: str | None = None

    def __post_init__(self):
        if not isinstance(self.data, dict) or not isinstance(self.directive, Directive):
            raise TypeError('ToolResult needs a data object and an explicit Directive')

    @property
    def status(self):
        return self.data.get('status', 'ok')

    @property
    def artifacts(self):
        return self.data.get('artifacts', [])


@dataclass(frozen=True)
class HookDecision:
    """Hooks may cancel a call or stop the run, but cannot bypass validation."""
    cancel: bool = False
    directive: Directive = Directive.CONTINUE
    reason: str = ''


@dataclass(frozen=True)
class ToolContext:
    workspace: Any
    candidates: Any
    eda: Any
    verification: Any
    events: Any


class Tool:
    name = ''
    description = ''
    parameters = {'type':'object', 'properties':{}, 'required':[], 'additionalProperties':False}

    def configure(self, ctx, add_hook):
        """Optional once-per-task event/hook registration, after catalog validation."""

    def validate(self, ctx, args):
        """Optional business checks, after mandatory schema/policy validation."""

    def prepare(self, ctx, args):
        return args

    def execute(self, ctx, prepared):
        raise NotImplementedError

    def apply_result(self, ctx, prepared, result):
        """Commit candidate/evidence changes before publishing notifications."""

    def observation(self, ctx, result):
        return model_observation(result.data)

    def cleanup(self, ctx, prepared):
        """Called even when prepare/execute/apply_result raises."""
