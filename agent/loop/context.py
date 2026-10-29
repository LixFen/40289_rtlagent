"""上下文处理：提示词、观察压缩和完整工具轮次的历史管理。"""
import json

from agent.llm.protocol import history_turn
from agent.paths import ROOT
from agent.config import load_settings
from agent.agent_tool_registry.observations import model_observation  # compatibility for callers


class Conversation:
    """维护公开任务及模型可见历史，不暴露控制器时间或额度。"""

    def __init__(self, prompt, interface, code_first):
        """加载工程提示词，初始化首轮指导和空历史。"""
        self.prompt, self.interface, self.code_first = prompt, interface, code_first
        self.system = (ROOT/'skill/autonomous.md').read_text(encoding='utf-8')
        self.system += '\nRTL guidance:\n'+(ROOT/'skill/design.md').read_text(encoding='utf-8').replace(
            'Return code only,', 'When writing src/solution.sv,')
        self.initial_system = self.system
        self.observation = {'status':'new_task', 'instruction':'Understand the specification, plan, then choose your first action.'}
        if code_first:
            self.initial_system += ('\nSave a complete RTL candidate to src/solution.sv as your first action. '
                                    'Use the next tool observation for compact self-testing; do not plan all later stages now.\n')
            self.observation['instruction'] = 'Write a complete RTL candidate using write_file now; validate after it is saved.'
        self.history = []

    @property
    def input_char_limit(self):
        """逐轮读取有效裁剪门槛，保留运行中环境覆盖的原有语义。"""
        return load_settings().input_char_limit

    def build_request(self, model, runtime):
        """组织任务、证据和历史，超限时整轮裁剪，再由模型适配器构建请求。"""
        state = runtime.state()
        content = json.dumps({'public_specification':self.prompt, 'interface':self.interface, 'state':state,
                              'last_observation':self.observation}, ensure_ascii=False)
        messages = [{'role':'system', 'content':self.initial_system if self.code_first and not state['current'] else self.system},
                    {'role':'user', 'content':self.prompt+('\nInterface:\n'+self.interface if self.interface else '')}]
        messages.extend(item for turn in self.history for item in turn)
        messages.append({'role':'user', 'content':content})
        tools = runtime.registry.definitions(runtime.disabled)
        body = model.create_request(messages, tools)
        limit = self.input_char_limit
        while self.history and (len(json.dumps(body, ensure_ascii=False)) > limit or body['max_tokens'] <= 0):
            oldest = self.history.pop(0)
            del messages[2:2+len(oldest)]
            body = model.create_request(messages, tools)
        return body

    def remember(self, parsed_calls, observations, feedback=None, assistant_message=None):
        """保留最近三轮工具调用、结果及已启用的推理，整轮裁剪保持配对。"""
        self.observation = feedback if feedback is not None else observations[-1]
        self.history.append(history_turn([call for _,call in parsed_calls], observations, assistant_message))
        del self.history[:-3]
