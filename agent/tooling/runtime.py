"""本题工具装配与派发；具体工具行为由职责模块实现。"""
import os
from pathlib import Path

from agent.tooling.candidates import CandidateStore, summary
from agent.config import runtime_value
from agent.tooling.eda import EDA
from agent.tooling.verification import VerificationState
from agent.tooling.workspace import Workspace
from agent.agent_tool_registry import Directive, EventBus, ToolContext, ToolResult, build_registry


class Runtime:
    """装配本题工具，提供公开状态，并统一检查工具调用权限和次数。"""

    def __init__(self, prompt, interface, out, end, record):
        """创建共享工作区及各职责组件，注册模型可调用的工具。"""
        self.out = Path(out)
        self.ws = Workspace(self.out/'workspace')
        self.source = self.ws.root/'src/solution.sv'
        self.events = EventBus(record)
        self.candidates = CandidateStore(prompt, interface, self.source, self.out, self.events.record)
        self.eda = EDA(self.ws, out, end, self.events.record)
        self.verification = VerificationState(self.events.record)
        self.tool_calls = 0
        self.last_action = None
        self.repeats = 0
        self.max_tools = int(runtime_value('agent', 'max_actions', 'AGENT_MAX_ACTIONS'))
        self.disabled = set(filter(None, os.environ.get('AGENT_DISABLED_TOOLS', '').split(',')))
        self.plan = ''
        context = ToolContext(self.ws, self.candidates, self.eda, self.verification, self.events)
        self.registry = build_registry(context, self._authorize)

    def state(self):
        """提供模型可见的精简状态，不包含控制器额度。"""
        current = self.candidates.current()
        return {'current': summary(current), 'best': summary(self.candidates.best),
                'files': self.ws.listing()[:100], 'contract': self.ws.contract,
                'pending_counterexample': self.verification.pending_counterexample,
                'tests_frozen': self.ws.tests_locked, 'disabled_tools': sorted(self.disabled), 'last_plan': self.plan}

    def dispatch(self, action):
        """Compatibility entry point returning raw data; lifecycle lives in registry."""
        return self.invoke(action).data

    def invoke(self, action, *, invocation_id=None):
        if (not isinstance(action, dict) or not isinstance(action.get('tool'), str)
                or not isinstance(action.get('args', {}), dict)):
            raise ValueError('Decision must contain a known tool and object args')
        self.plan = action.get('plan', '')[:2000]
        return self.registry.invoke(action['tool'], action.get('args', {}), invocation_id=invocation_id)

    def _authorize(self, tool, args):
        """Mandatory policy, evaluated before any tool-specific hook."""
        if tool in self.disabled:
            raise ValueError('Tool disabled for this ablation: ' + tool)
        if self.tool_calls >= self.max_tools:
            return ToolResult({'status':'budget', 'done':True}, Directive.STOP_RESOURCE)
        self.tool_calls += 1
