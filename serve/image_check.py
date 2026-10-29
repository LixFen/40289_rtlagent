"""Check installed image software without claiming GPU, license or offline acceptance."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from agent.paths import ROOT
from agent.config import load_runtime


def check():
    report = {'checks': {}, 'scope': 'software only; GPU/model/license/offline acceptance is separate'}
    checks = report['checks']
    manifest = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
    runtime = load_runtime(ROOT)
    for name in ('bash', 'xvlog', 'xelab', 'xsim', 'vivado'):
        checks[name] = bool(shutil.which(name))
    if checks['vivado']:
        result = subprocess.run(['vivado', '-version'], capture_output=True, text=True, timeout=60)
        checks['vivado_version'] = result.returncode == 0 and (
            'v'+runtime['final_target']['vivado'] in result.stdout+result.stderr)
    for name, variable in (('slang', 'SLANG_BIN'), ('llama-server', 'LLAMA_SERVER')):
        executable = os.environ.get(variable) or shutil.which(name)
        checks[name] = bool(executable and os.access(executable, os.X_OK))
        if executable and checks[name]:
            # ldd resolves shared libraries without initializing a GPU.
            result = subprocess.run(['ldd', executable], capture_output=True, text=True, timeout=30)
            output = result.stdout + result.stderr
            checks[name+'_libraries'] = 'not found' not in output and (
                result.returncode == 0 or 'not a dynamic executable' in output or 'statically linked' in output)
    if checks.get('slang_libraries'):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'smoke.sv'
            source.write_text('module TopModule(input logic a, output logic q); assign q=a; endmodule\n')
            result = subprocess.run([os.environ.get('SLANG_BIN') or shutil.which('slang'),
                                     '--lint-only', str(source)], capture_output=True, timeout=30)
            checks['slang_minimal_rtl'] = result.returncode == 0
    from agent.agent_tool_registry import catalog
    checks['tool_registry'] = sorted(manifest['agent']['tools']) == catalog().names
    # Exercise the storage API used by registered tools; importing alone misses API drift.
    from agent.tooling.workspace import Workspace
    with tempfile.TemporaryDirectory() as directory:
        workspace = Workspace(directory)
        workspace.write_text('src/solution.sv', 'module TopModule; endmodule')
        checks['workspace_api'] = workspace.read_text('src/solution.sv') == 'module TopModule; endmodule'
    return report


if __name__ == '__main__':
    result = check()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if all(result['checks'].values()) else 1)
