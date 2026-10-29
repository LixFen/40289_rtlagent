"""Discover trusted installed tools; never load code from task workspaces."""
from functools import lru_cache
import importlib
import pkgutil

from agent.agent_tool_registry.base import Directive, HookDecision, Tool, ToolContext, ToolResult
from agent.agent_tool_registry.events import EventBus
from agent.agent_tool_registry.registry import ToolRegistry


def discover():
    from agent.agent_tool_registry import tools
    discovered = []
    for module in sorted(pkgutil.iter_modules(tools.__path__), key=lambda item:item.name):
        if module.name.startswith('_'):
            continue
        loaded = importlib.import_module(tools.__name__+'.'+module.name)
        for tool_class in getattr(loaded, 'TOOLS', ()):
            if not isinstance(tool_class, type) or not issubclass(tool_class, Tool):
                raise ValueError('TOOLS must export Tool classes: '+module.name)
            discovered.append(tool_class())
    return discovered


def build_registry(context=None, authorize=None):
    return ToolRegistry(discover(), context, authorize)


@lru_cache(maxsize=1)
def catalog():
    """Unbound definitions; per-task registries have independent hooks/context."""
    return build_registry()
