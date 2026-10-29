"""Extract Ubuntu 24.04 user-space libraries without replacing host system files."""
import gzip
import hashlib
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path('/tmp/rtl-runtime/compat')
ROOT.mkdir(exist_ok=True)
BASE = 'https://mirrors.aliyun.com/ubuntu/'


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


if __name__ == '__main__':
    index = gzip.decompress(fetch(BASE + 'dists/noble/main/binary-amd64/Packages.gz')).decode()
    for name in ('libc6', 'libstdc++6', 'libgcc-s1', 'libgomp1'):
        block = next(block for block in index.split('\n\n') if block.startswith('Package: '+name+'\n'))
        fields = dict(line.split(': ', 1) for line in block.splitlines() if ': ' in line and not line.startswith(' '))
        raw = fetch(BASE + fields['Filename'])
        if hashlib.sha256(raw).hexdigest() != fields['SHA256']:
            raise RuntimeError('Package hash mismatch: ' + name)
        file = ROOT / Path(fields['Filename']).name
        file.write_bytes(raw)
        subprocess.run(['dpkg-deb', '-x', str(file), str(ROOT / 'root')], check=True)
        print('EXTRACTED', name, fields['Version'], fields['SHA256'], flush=True)
