"""Resumable ranged download of the user's exact ModelScope GGUF, with SHA-256."""
import concurrent.futures
import argparse
import hashlib
import json
import sys
from pathlib import Path
import time
import urllib.request

REPO = 'ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF'
NAME = 'Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf'
REVISION = 'fc62412a020e0c1ee3a8f9a12f10aea2102478f0'
SIZE = 11771546784
SHA256 = '64b53b64c7aa39f20a7e54bd80582fe595b1d745624ee8a72e92508c0326d810'
URL = f'https://www.modelscope.cn/models/{REPO}/resolve/{REVISION}/{NAME}'
ROOT = Path('/workspace/models')
CHUNK = 256*1024*1024
WORKERS = 8

if '--runtime' in sys.argv:
    REPO = 'ggml-org/llama.cpp'
    NAME = 'llama-b11552-bin-ubuntu-rocm-10.0-x64.tar.gz'
    REVISION = 'b11552'
    SIZE = 244930330
    SHA256 = '256673debf9313a8e8994a739ccf49de15d4091029b460af1d5889491c63eb84'
    URL = Path('/tmp/rtl-runtime/llama-direct-url.txt').read_text().strip()
    ROOT = Path('/tmp/rtl-runtime/runtime-ranged')
    CHUNK = 8*1024*1024
    WORKERS = 24


def main():
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', action='store_true', help='historical b11552 runtime download')
    parser.add_argument('--output-dir', type=Path, default=ROOT,
                        help='persistent directory with sufficient free space; do not use a small tmpfs')
    ROOT = parser.parse_args().output_dir
    ROOT.mkdir(parents=True, exist_ok=True)
    target = ROOT / NAME
    partial = ROOT / (NAME + '.part')
    if not target.exists():
        with partial.open('ab') as stream:
            stream.truncate(SIZE)

        def part(index):
            marker = ROOT / f'chunk-{index}.done'
            if marker.exists():
                return
            start, end = index*CHUNK, min(SIZE, (index+1)*CHUNK)-1
            for attempt in range(4):
                try:
                    request = urllib.request.Request(URL, headers={'Range': f'bytes={start}-{end}'})
                    with urllib.request.urlopen(request, timeout=60) as response, partial.open('r+b') as stream:
                        if response.status != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {start}-'):
                            raise RuntimeError('Server did not honor the requested byte range')
                        stream.seek(start)
                        count = 0
                        while data := response.read(1024*1024):
                            if count+len(data) > end-start+1:
                                raise RuntimeError('Oversized range response')
                            stream.write(data)
                            count += len(data)
                        if count != end-start+1:
                            raise RuntimeError('Incomplete range')
                    marker.write_text(str(count))
                    print('chunk', index, 'complete', count, flush=True)
                    return
                except Exception as exc:
                    print('retry', index, attempt, str(exc), flush=True)
                    if attempt == 3:
                        raise
                    time.sleep(2)

        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(part, range((SIZE+CHUNK-1)//CHUNK)))
        partial.rename(target)
    digest = hashlib.sha256()
    with target.open('rb') as stream:
        while data := stream.read(16*1024*1024):
            digest.update(data)
    actual = digest.hexdigest()
    if actual != SHA256:
        raise RuntimeError('Model SHA-256 mismatch: ' + actual)
    metadata = dict(repo=REPO, name=NAME, revision=REVISION, sha256=actual, bytes=target.stat().st_size,
                    quantization='GSQ-RCO IQ3_S' if NAME.endswith('.gguf') else None, path=str(target), source=URL)
    (ROOT / ('model.json' if NAME.endswith('.gguf') else 'artifact.json')).write_text(json.dumps(metadata, indent=2))
    print('ARTIFACT_VERIFIED', json.dumps(metadata), flush=True)


if __name__ == '__main__':
    main()
