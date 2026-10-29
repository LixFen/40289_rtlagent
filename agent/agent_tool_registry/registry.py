"""Single source of tool definitions, lifecycle execution and explicit hooks."""
from copy import deepcopy
import json
import re
import uuid

from agent.agent_tool_registry.base import Directive, HookDecision, Tool, ToolResult
from agent.agent_tool_registry.events import ArchiveError, CriticalSubscriberError, snapshot
from agent.agent_tool_registry.schema import check_schema, validate


class ToolRegistry:
    def __init__(self, tools, context=None, authorize=None):
        self.context, self.authorize = context, authorize
        self._tools, self._hooks = {}, {'before_prepare':[], 'after_result':[]}
        self._executing = False
        for tool in tools:
            if (not isinstance(tool, Tool) or not isinstance(tool.name, str)
                    or not re.fullmatch(r'[a-z][a-z0-9_]*', tool.name)):
                raise ValueError('Invalid tool definition')
            if tool.name in self._tools:
                raise ValueError('Duplicate tool: '+tool.name)
            if not isinstance(tool.description, str) or not tool.description.strip() or type(tool).execute is Tool.execute:
                raise ValueError('Tool needs a description and execute method: '+tool.name)
            check_schema(tool.parameters)
            if tool.parameters['type'] != 'object':
                raise ValueError('Tool parameters must be an object schema')
            self._tools[tool.name] = tool
        if context is not None:
            for name in self.names:
                self._tools[name].configure(context, self.add_hook)

    @property
    def names(self):
        return sorted(self._tools)

    def get(self, name):
        if name not in self._tools:
            raise ValueError('Unknown tool: '+str(name))
        return self._tools[name]

    def definitions(self, disabled=()):
        return [{'type':'function', 'function':{'name':name, 'description':self._tools[name].description,
                'parameters':deepcopy(self._tools[name].parameters)}} for name in self.names if name not in disabled]

    def add_hook(self, phase, callback):
        if phase not in self._hooks:
            raise ValueError('Unsupported hook phase: '+phase)
        if not callable(callback):
            raise TypeError('Hook must be callable')
        self._hooks[phase].append(callback)

    def _hook(self, phase, name, args, result=None):
        payload = {'tool':name, 'args':args, 'invocation_id':self.context.events.invocation_id}
        if result is not None:
            payload['result'] = result.data
        call = snapshot(json.loads(json.dumps(payload)))
        for index, callback in enumerate(tuple(self._hooks[phase])):
            decision = callback(self.context, call)
            if decision is None:
                decision = HookDecision()
            if (not isinstance(decision, HookDecision) or not isinstance(decision.directive, Directive)
                    or type(decision.cancel) is not bool or not isinstance(decision.reason, str)
                    or decision.directive not in (Directive.CONTINUE, Directive.STOP_RESOURCE)
                    or (phase == 'after_result' and decision.cancel)):
                raise TypeError('Hook must return an allowed HookDecision or None')
            self.context.events.record('tool_hook', phase=phase, name=name, index=index,
                callback=getattr(callback, '__qualname__', type(callback).__qualname__),
                cancel=decision.cancel, directive=decision.directive.value, reason=decision.reason)
            if decision.cancel or decision.directive != Directive.CONTINUE:
                return decision
        return HookDecision()

    def invoke(self, name, args, *, invocation_id=None):
        if self._executing:
            raise RuntimeError('Recursive tool invocation is not supported')
        if invocation_id is not None and (not isinstance(invocation_id, str) or not invocation_id):
            raise ValueError('invocation_id must be a nonempty string')
        self._executing = True
        try:
            if self.context is None:
                raise RuntimeError('Tool invocation requires a context')
            with self.context.events.invocation(invocation_id or uuid.uuid4().hex):
                return self._invoke(name, args)
        finally:
            self._executing = False

    def _invoke(self, name, args):
        tool = self.get(name)
        validate(tool.parameters, args)
        ctx = self.context
        if ctx is None:
            raise RuntimeError('Tool invocation requires a context')
        args = deepcopy(args)
        prepared, result, error, needs_cleanup = None, None, None, False
        phase = 'authorize'
        try:
            # Mandatory policy is deliberately outside hook control.
            result = self.authorize(name, args) if self.authorize else None
            if result is None:
                phase = 'validate'
                tool.validate(ctx, args)
                ctx.events.emit('tool.started', name=name, args=args)
                phase = 'before_prepare'
                decision = self._hook('before_prepare', name, args)
                if decision.cancel or decision.directive == Directive.STOP_RESOURCE:
                    result = ToolResult({'status':'cancelled', 'message':decision.reason}, decision.directive)
                else:
                    needs_cleanup = True
                    phase = 'prepare'
                    prepared = tool.prepare(ctx, args)
                    phase = 'execute'
                    executed = tool.execute(ctx, prepared)
                    if not isinstance(executed, ToolResult) or not isinstance(executed.directive, Directive):
                        raise TypeError('Tool execute must return ToolResult with a Directive')
                    json.dumps(executed.data, allow_nan=False)
                    result = executed
                    phase = 'apply_result'
                    tool.apply_result(ctx, prepared, result)
                    phase = 'after_result'
                    decision = self._hook('after_result', name, args, result)
                    if decision.directive == Directive.STOP_RESOURCE:
                        result.directive = decision.directive
        except BaseException as exc:
            error = exc
        finally:
            if needs_cleanup:
                try:
                    tool.cleanup(ctx, prepared)
                except BaseException as exc:
                    if error is None:
                        error = exc
                        phase = 'cleanup'
                    else:
                        ctx.events.record('tool_cleanup_error', name=name, error=type(exc).__name__, message=str(exc)[:1500])
        # Preserve an executed result even when apply_result or cleanup failed.
        if result is not None:
            result.invocation_id = ctx.events.invocation_id
            json.dumps(result.data, allow_nan=False)
            ctx.events.record('tool_result', name=name, status=result.status, result=result.data,
                              artifacts=result.artifacts, directive=result.directive.value)
        if error is not None:
            self._raise_failure(name, phase, error, result)
        try:
            ctx.events.emit('tool.completed', name=name, status=result.status, directive=result.directive.value)
        except Exception as exc:
            self._raise_failure(name, 'completed_subscriber', exc, result)
        return result

    def _raise_failure(self, name, phase, error, result):
        if isinstance(error, ArchiveError):
            raise error
        try:
            self.context.events.emit('tool.failed', name=name, phase=phase,
                                     error=type(error).__name__, message=str(error)[:1500])
        except CriticalSubscriberError as notification_error:
            notification_error.result = result
            raise notification_error from error
        if isinstance(error, CriticalSubscriberError):
            error.result = result
        raise error

    def observe(self, name, result):
        # Tools receive a copy so projection cannot overwrite raw evidence.
        with self.context.events.invocation(result.invocation_id):
            projected = self.get(name).observation(self.context, deepcopy(result))
        if not isinstance(projected, dict):
            raise TypeError('Tool observation must be a JSON object')
        json.dumps(projected, allow_nan=False)
        return projected
