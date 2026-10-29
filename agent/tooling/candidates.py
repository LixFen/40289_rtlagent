"""候选仓库：按源码哈希保存版本、维护证据并原子发布最佳代码。"""
import json

from agent.tooling.checks import interface_errors
from agent.tooling.workspace import sha


def summary(item):
    """生成模型可见的候选摘要，不传递源码副本和完整报告。"""
    if not item:
        return None
    return {'sha256': item['sha256'], 'interface_errors': item['interface_errors'],
            'evidence': {k: {'status': v['status'], 'checks': v.get('checks'),
                             'violation_count': v.get('violation_count')}
                         for k, v in item['evidence'].items()}}


class CandidateStore:
    """集中管理源码版本及其证据；工具调度不负责候选排序和落盘。"""

    def __init__(self, prompt, interface, source, out, record):
        """绑定公开规格、源码文件和控制器私有的候选归档目录。"""
        self.prompt, self.interface = prompt, interface
        self.source, self.out, self.record = source, out, record
        self.entries = {}
        self.best = None

    def current(self):
        """按源码哈希登记候选，首次出现时检查接口并保存源码快照。"""
        if not self.source.exists():
            return None
        code = self.source.read_text(encoding='utf-8')
        digest = sha(code)
        if digest not in self.entries:
            errors, ports = interface_errors(self.prompt, self.interface, code)
            self.entries[digest] = {'sha256': digest, 'code': code, 'interface_errors': errors,
                                       'checked_ports': ports, 'evidence': {}}
            directory = self.out / 'candidates' / digest
            directory.mkdir(parents=True)
            (directory / 'solution.sv').write_text(code, encoding='utf-8')
            self.record('candidate', sha256=digest, interface_errors=errors, checked_ports=ports)
        return self.entries[digest]

    def select_best(self):
        """按验证证据排序候选，原子更新交付文件并归档候选索引。"""
        def rank(item):
            statuses = {key: value.get('status') for key, value in item['evidence'].items()}
            contradiction = any(statuses.get(key) in ('selftest_fail', 'compile_error', 'design_error')
                                for key in ('simulate', 'compile', 'synth', 'slang'))
            return (not item['interface_errors'] and not contradiction,
                    not item['interface_errors'], statuses.get('simulate') == 'passed',
                    statuses.get('synth') == 'passed', statuses.get('compile') == 'passed',
                    statuses.get('lint') == 'passed', bool(item['code'].strip()))
        choices = [item for item in self.entries.values() if item['code'].strip()]
        # Newer candidates win only when their validation ranks are equal.
        self.best = max(reversed(choices), key=rank) if choices else None
        if self.best:
            file = self.out / 'solution.v'
            temporary = file.with_suffix('.tmp')
            temporary.write_text(self.best['code'], encoding='utf-8')
            temporary.replace(file)
        inventory = [{k: v for k, v in item.items() if k != 'code'} for item in self.entries.values()]
        (self.out / 'candidates.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
        return self.best

    def invalidate_simulations(self):
        """测试台修订后撤销旧仿真证据，并保留撤销记录。"""
        for item in self.entries.values():
            previous = item['evidence'].pop('simulate', None)
            if previous:
                self.record('evidence_invalidated', sha256=item['sha256'], stage='simulate', previous=previous)

    def record_result(self, item, tool, result):
        """只接纳确定的设计证据，同一源码的已知反例不能被后续通过覆盖。"""
        if result['status'] not in ('passed', 'selftest_fail', 'compile_error', 'design_error'):
            return
        old = item['evidence'].get(tool)
        if tool == 'simulate' and old and old['status'] == 'selftest_fail' and result['status'] == 'passed':
            self.record('counterexample_retained', sha256=item['sha256'], previous=old)
        else:
            item['evidence'][tool] = result
