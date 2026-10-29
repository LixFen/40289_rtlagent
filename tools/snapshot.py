"""Capture an allowlisted development source package; never overwrite an experiment."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.freeze import ROOT, inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    archive, manifest = args.out/'source.zip', args.out/'source-hashes.json'
    if archive.exists() or manifest.exists():
        parser.error('Snapshot already exists; select a fresh output directory')
    hashes = inventory()
    text = json.dumps(hashes, indent=2)+'\n'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, expected in hashes.items():
            data = (ROOT/name).read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                raise RuntimeError('Source changed during snapshot: '+name)
            bundle.writestr(name, data)
        bundle.writestr('source-hashes.json', text)
    with manifest.open('x', encoding='utf-8') as stream:
        stream.write(text)
    print(json.dumps({'archive':str(archive.absolute()), 'files':len(hashes)}))


if __name__ == '__main__':
    main()
