"""Trusted offline recovery of validated inference binaries, never weights or secrets.

The official Ubuntu/Vivado base and GPU driver remain prerequisites. System
libraries are checked, not overwritten. Existing mismatched software fails closed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def payload_path(name, prefix):
    path = PurePosixPath(name)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Invalid recovery path')
    allowed = (name.startswith(('/opt/llamacpp/', '/opt/slang/12.0/',
                               '/opt/python/lib/python3.14/site-packages/_rocm_sdk_devel/lib/'))
               or name == '/root/.local/bin/rc-tunnel'
               or (name.startswith('/root/.local/') and path.name == 'frpc'))
    if not allowed:
        raise ValueError('Recovery target is outside approved software paths: '+name)
    return Path(prefix).joinpath(*path.parts[1:])


def capture(bundle, model_pid):
    bundle = Path(bundle)
    bundle.parent.mkdir(parents=True, exist_ok=True)
    if bundle.exists() or bundle.with_suffix('.json').exists():
        raise FileExistsError('Recovery capsule already exists; use a new filename')
    files = set()
    for directory in (Path('/opt/llamacpp'), Path('/opt/slang/12.0')):
        files.update(path for path in directory.rglob('*') if path.is_file())
    for path in (Path('/root/.local/bin/rc-tunnel'), *Path('/root/.local').rglob('frpc')):
        if path.is_file():
            files.add(path)
    ldd = subprocess.run(['ldd', '/opt/llamacpp/llama-server'], capture_output=True,
                         text=True, check=True).stdout
    loaded = {Path(name) for name in re.findall(r'(/[\w./+-]+)', ldd) if Path(name).is_file()}
    for line in Path('/proc', str(model_pid), 'maps').read_text().splitlines():
        parts = line.split(None, 5)
        if len(parts) == 6 and parts[5].startswith('/') and '.so' in parts[5]:
            path = Path(parts[5])
            if path.is_file():
                loaded.add(path)
    system = {}
    for path in loaded:
        if str(path).startswith(('/usr/lib/', '/lib/', '/lib64/')):
            system[str(path.resolve())] = digest(path)
        else:
            payload_path(str(path), '/')
            files.add(path)
    for path in files:
        payload_path(str(path), '/')
    size = sum(path.stat().st_size for path in files)
    if shutil.disk_usage(bundle.parent).free < size + 512*1024**2:
        raise OSError('Not enough persistent space for a conservative capsule write')
    manifest = {'schema': 1, 'machine': platform.machine(),
                'os_release': platform.freedesktop_os_release().get('VERSION_ID'),
                'system_files': system, 'files': {}}
    temporary = bundle.with_name(bundle.name+'.part')
    try:
        with tarfile.open(temporary, 'w:gz', compresslevel=1, dereference=True) as archive:
            for path in sorted(files):
                row = {'sha256': digest(path), 'bytes': path.stat().st_size,
                       'mode': path.stat().st_mode & 0o777}
                archive.add(path, arcname=str(path).lstrip('/'), recursive=False)
                manifest['files'][str(path)] = row
        manifest['archive_sha256'] = digest(temporary)
        os.replace(temporary, bundle)
        bundle.with_suffix('.json').write_text(json.dumps(manifest, indent=2)+'\n')
    finally:
        temporary.unlink(missing_ok=True)
    return {'payload_files':len(files), 'payload_bytes':size,
            'archive_bytes':bundle.stat().st_size, 'bundle':str(bundle)}


def restore(bundle, manifest_path, prefix=Path('/')):
    bundle, prefix = Path(bundle), Path(prefix)
    manifest = json.loads(Path(manifest_path).read_text())
    if digest(bundle) != manifest['archive_sha256']:
        raise ValueError('Recovery archive SHA-256 mismatch')
    if platform.machine() != manifest['machine']:
        raise RuntimeError('Recovery capsule CPU architecture differs')
    if platform.freedesktop_os_release().get('VERSION_ID') != manifest['os_release']:
        raise RuntimeError('Recovery capsule requires the validated OS base')
    for name, expected in manifest['system_files'].items():
        if not Path(name).is_file() or digest(name) != expected:
            raise RuntimeError('Base system library differs; it will not be overwritten: '+name)
    restored, present = [], []
    # Validate all existing files before changing any software.
    for name, row in manifest['files'].items():
        target = payload_path(name, prefix)
        if target.exists() and digest(target) != row['sha256']:
            raise RuntimeError('Existing software differs; refusing to overwrite: '+str(target))
    with tarfile.open(bundle, 'r:gz') as archive:
        for name, row in manifest['files'].items():
            target = payload_path(name, prefix)
            if target.exists():
                present.append(str(target))
                continue
            member = archive.getmember(name.lstrip('/'))
            if not member.isfile() or member.size != row['bytes']:
                raise ValueError('Unexpected recovery archive member: '+name)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    with archive.extractfile(member) as source:
                        shutil.copyfileobj(source, stream)
                if digest(temporary) != row['sha256']:
                    raise ValueError('Recovered file SHA-256 mismatch: '+name)
                temporary.chmod(row['mode'])
                os.replace(temporary, target)
                restored.append(str(target))
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    return {'restored_files':len(restored), 'already_verified_files':len(present),
            'prefix':str(prefix)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    capture_parser = commands.add_parser('capture')
    capture_parser.add_argument('--bundle', type=Path, required=True)
    capture_parser.add_argument('--model-pid', type=int, required=True)
    restore_parser = commands.add_parser('restore')
    restore_parser.add_argument('--bundle', type=Path, required=True)
    restore_parser.add_argument('--manifest', type=Path, required=True)
    restore_parser.add_argument('--prefix', type=Path, default=Path('/'))
    args = parser.parse_args()
    if args.action == 'capture':
        result = capture(args.bundle, args.model_pid)
    else:
        result = restore(args.bundle, args.manifest, args.prefix)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
