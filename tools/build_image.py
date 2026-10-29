"""Validate image inputs, then optionally build the production target with Docker."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.config import load_runtime


def source_checks(root=ROOT):
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    runtime = load_runtime(root)
    checks = {}
    release = json.loads((root/'serve/slang-release.json').read_text(encoding='utf-8'))
    archive = root/'vendor/slang-linux-v12.0.tar.gz'
    checks['slang_offline_archive'] = archive.is_file() and (
        hashlib.sha256(archive.read_bytes()).hexdigest() == release['assets']['linux']['sha256'])
    for name, expected in runtime['baseline_source']['canonical_sha256'].items():
        content = (root/name).read_bytes().replace(b'\r\n', b'\n')
        checks['baseline:'+name] = hashlib.sha256(content).hexdigest() == expected
    from agent.agent_tool_registry import catalog
    checks['registry'] = sorted(manifest['agent']['tools']) == catalog().names
    pinned = json.loads((root/'skill/knowledge/ross-sources.json').read_text(encoding='utf-8'))
    for name, entry in pinned['files'].items():
        path = root/name if name.startswith('agent/') else root/'skill/knowledge'/name
        checks['ross:'+name] = hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256']
    checks['model_identity'] = (bool(re.fullmatch(r'[0-9a-f]{64}', runtime['model']['sha256']))
                                and bool(manifest['model']['revision']))
    checks['native_launcher'] = manifest['serving']['start_command'] == 'bash serve/llamacpp.sh'
    checks['target'] = runtime['final_target']['gpu'] == 'AMD Radeon PRO W7900'
    official_base = 'fpgachina-rtlhls@sha256:a7cf627e11708e8a6dcd9de689ff934f7b450b79369ebc7bb0217e8b54d81e56'
    checks['official_base_image'] = runtime['base_image'] == official_base
    checks['dockerfile_base_image'] = ('ARG BASE_IMAGE='+official_base) in (root/'Dockerfile').read_text()
    checks['official_reference'] = runtime['official_reference_commit'] == 'fc8e26a5828b7222e9db439cee29128bcf4f8865'
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only', action='store_true', help='validate repository inputs only')
    parser.add_argument('--base-image', default=os.environ.get('BASE_IMAGE'))
    parser.add_argument('--tag', default='rtl-agent:production')
    parser.add_argument('--build', action='store_true', help='run Docker after all prerequisites pass')
    args = parser.parse_args()
    if args.source_only and args.build:
        parser.error('--source-only cannot be combined with --build')
    checks = source_checks()
    report = {'source': checks}
    ready = all(checks.values())
    if not args.source_only:
        base = args.base_image or load_runtime()['base_image']
        digest_ok = bool(base and re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', base))
        report['prerequisites'] = {'base_image_digest_supplied': digest_ok}
        report['note'] = 'Official base image is platform-private; organizers rebuild in fallback mode. Local Docker is optional.'
        ready = ready and digest_ok
    if args.build:
        docker = shutil.which('docker')
        engine = False
        if docker:
            try:
                result = subprocess.run([docker, 'info', '--format', '{{.OSType}}'],
                                        capture_output=True, text=True, timeout=20)
                engine = result.returncode == 0 and result.stdout.strip() == 'linux'
            except (OSError, subprocess.TimeoutExpired):
                pass
        report['prerequisites']['linux_docker_engine'] = engine
        ready = ready and engine
    report['ready'] = ready
    print(json.dumps(report, indent=2), flush=True)
    if not ready:
        return 1
    if args.build:
        command = [docker, 'build', '--target', 'production', '--build-arg', 'BASE_IMAGE='+base,
                   '--tag', args.tag]
        return subprocess.run(command+['.'], cwd=ROOT).returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
