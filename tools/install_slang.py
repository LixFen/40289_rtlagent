"""Install a pre-downloaded, checksum-pinned slang archive; never downloads at runtime."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile
import zipfile


def install(archive, prefix):
    release = json.loads((Path(__file__).resolve().parents[1]/'serve/slang-release.json').read_text())
    platform = 'windows' if os.name == 'nt' else 'linux'
    expected = release['assets'][platform]['sha256']
    archive, prefix = Path(archive), Path(prefix)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError('slang archive SHA-256 does not match the pinned official release')
    name = 'slang.exe' if platform == 'windows' else 'slang'
    if platform == 'windows':
        with zipfile.ZipFile(archive) as package:
            content = package.read(name)
    else:
        with tarfile.open(archive) as package:
            content = package.extractfile(name).read()
    target = prefix/'bin'/name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    target.chmod(0o755)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--prefix', type=Path, required=True)
    args = parser.parse_args()
    print(install(args.archive, args.prefix))
