"""Inspect the installed catalog or update its delivery declaration."""
import argparse
import json

from agent.paths import ROOT
from agent.agent_tool_registry import catalog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--write-manifest', action='store_true')
    args = parser.parse_args()
    names = catalog().names
    if args.write_manifest:
        path = ROOT/'manifest.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest['agent']['tools'] = names
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(names))


if __name__ == '__main__':
    main()
