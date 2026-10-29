"""Report local prerequisites and baseline integrity without claiming a score."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.agent_tool_registry import catalog

official = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "rtlagent2026"
report = {"python": sys.version.split()[0], "tools": {name: shutil.which(name) for name in ("xvlog", "xelab", "xsim", "vivado", "slang", "docker", "vllm")}}
report['tools']['slang'] = os.environ.get('SLANG_BIN') or shutil.which('slang')
declared = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))['agent']['tools']
report['tool_registry'] = {'names':catalog().names, 'matches_manifest':sorted(declared)==catalog().names}
report["baseline_integrity"] = {}
manifest = json.loads((ROOT/'skill/knowledge/ross-sources.json').read_text(encoding='utf-8'))
report['ross_assets'] = {'commit': manifest['commit'], 'files': {}}
for name, item in manifest['files'].items():
    path = ROOT/name if name.startswith('agent/') else ROOT/'skill/knowledge'/name
    report['ross_assets']['files'][name] = hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
for name in ("baseline.py", "run_baseline.sh"):
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    report["baseline_integrity"][name] = dict(sha256=digest(ROOT / name), matches_official=digest(ROOT / name) == digest(official / "example" / name))
print(json.dumps(report, indent=2))
if not all(item["matches_official"] for item in report["baseline_integrity"].values()):
    raise SystemExit(1)
if not all(report['ross_assets']['files'].values()):
    raise SystemExit('Pinned Ross asset hash mismatch')
if not report['tool_registry']['matches_manifest']:
    raise SystemExit('Tool declaration mismatch; run python -m agent.agent_tool_registry --write-manifest')
