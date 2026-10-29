"""镜像构建时采用官方 Git blob 的 LF 行尾，校验基线内容没有变化。"""
import hashlib
import argparse
import json
from pathlib import Path
import re
from agent.config import load_runtime


def prepare(root, base_image=None):
    """规范化 Linux 脚本及基线行尾，并与固定参考提交的字节哈希核对。"""
    root=Path(root)
    if base_image is not None and not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', base_image):
        raise ValueError('BASE_IMAGE must use an immutable sha256 digest')
    paths=list(root.glob('*.sh'))+list((root/'serve').glob('*.sh'))+[root/'baseline.py']
    for path in paths:
        path.write_bytes(path.read_bytes().replace(b'\r\n',b'\n'))
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    runtime = load_runtime(root)
    expected=runtime['baseline_source']['canonical_sha256']
    for name,digest in expected.items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Official baseline source hash mismatch: '+name)
    if 'tools' in manifest.get('agent', {}):
        from agent.agent_tool_registry import catalog
        if sorted(manifest['agent']['tools']) != catalog().names:
            raise ValueError('Tool declaration mismatch; run python -m agent.agent_tool_registry --write-manifest')
    if base_image is not None:
        runtime['base_image'] = base_image
        (root/'serve/runtime.json').write_text(json.dumps(runtime, indent=2)+'\n', encoding='utf-8')


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-image')
    prepare(Path(__file__).resolve().parents[1], parser.parse_args().base_image)
