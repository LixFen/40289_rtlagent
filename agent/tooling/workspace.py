"""Model-visible file capability: no shell, absolute paths, links or controller state."""
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def safe_hdl(text):
    # Keep strings when scanning: filenames and DPI imports must not be hidden by comments.
    code = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/',
                  lambda m: m[0] if m[0].startswith('"') else ' ', text, flags=re.S)
    if re.search(r'`\s*(?:include|pragma)|\bimport\s*"|\b(?:DPI|PLI|VPI)\b', code):
        raise ValueError('External HDL directives/imports are forbidden')
    if any(name not in ('timescale', 'default_nettype') for name in re.findall(r'`\s*([A-Za-z_]\w*)', code)):
        raise ValueError('HDL macros/directives are restricted to prevent hidden external operations')
    allowed = {'display', 'write', 'sformatf', 'error', 'fatal', 'warning', 'info', 'finish', 'stop',
               'time', 'realtime', 'signed', 'unsigned', 'clog2', 'bits', 'size', 'countones',
               'onehot', 'onehot0', 'isunknown', 'urandom', 'urandom_range', 'random',
               'value$plusargs', 'test$plusargs', 'past', 'rose', 'fell', 'stable'}
    for name in re.findall(r'\$([A-Za-z_][\w$]*)', code):
        if name not in allowed:
            raise ValueError('Unsupported system task/function: $' + name)


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.contract = None
        self.tests_locked = False
        for folder in ('src', 'tests', 'notes', 'reports'):
            (self.root / folder).mkdir(exist_ok=True)

    def path(self, name, write=False):
        if not isinstance(name, str) or not name or '\\' in name or ':' in name:
            raise ValueError('Use a workspace-relative POSIX path')
        relative = Path(name)
        if relative.is_absolute() or PureWindowsPath(name).is_absolute() or '..' in relative.parts:
            raise ValueError('Path escapes workspace')
        if relative.parts[0] not in ('src', 'tests', 'notes', 'reports'):
            raise ValueError('Path is outside visible workspace directories')
        cursor = self.root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink() or (hasattr(cursor, 'is_junction') and cursor.is_junction()):
                raise ValueError('Links are forbidden')
        path = cursor.resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('Path escapes workspace')
        if write:
            if relative.as_posix() == 'notes/contract.json' and self.contract is not None:
                raise ValueError('Test contract is immutable')
            if relative.parts[0] == 'reports':
                raise ValueError('Tool reports are read-only')
            if relative.parts[0] == 'tests' and self.tests_locked:
                raise ValueError('Use amend_test with a reason to revise tests after simulation')
            if relative.parts[0] in ('src', 'tests') and path.suffix not in ('.v', '.sv'):
                raise ValueError('Only Verilog/SystemVerilog sources are supported')
            if relative.parts[0] == 'notes' and path.suffix not in ('.md', '.txt', '.json'):
                raise ValueError('Only text notes are supported')
        return path

    def write_text(self, name, content):
        return self.write(name, content)

    def read_text(self, name, *, errors='replace', for_write=False):
        return self.path(name, write=for_write).read_text(encoding='utf-8', errors=errors)

    def write(self, name, content):
        path = self.path(name, write=True)
        if not isinstance(content, str) or len(content.encode()) > 131072:
            raise ValueError('File exceeds 128 KiB')
        if path.suffix in ('.v', '.sv'):
            safe_hdl(content)
        files = [p for folder in ('src', 'tests', 'notes') for p in (self.root / folder).rglob('*') if p.is_file()]
        total = sum(p.stat().st_size for p in files if p != path) + len(content.encode())
        if total > 2 * 1024**2 or (not path.exists() and len(files) >= 64):
            raise ValueError('Workspace file/byte budget exhausted')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)
        return {'path': name, 'sha256': sha(content), 'bytes': len(content.encode())}

    def read(self, name, offset=0, limit=12000):
        if not isinstance(offset, int) or offset < 0 or not isinstance(limit, int) or not 1 <= limit <= 16000:
            raise ValueError('Invalid read range')
        text = self.path(name).read_text(encoding='utf-8', errors='replace')
        return {'path': name, 'content': text[offset:offset+limit], 'total_chars': len(text), 'sha256': sha(text)}

    def listing(self):
        return [{'path': p.relative_to(self.root).as_posix(), 'bytes': p.stat().st_size}
                for folder in ('src', 'tests', 'notes', 'reports')
                for p in sorted((self.root / folder).rglob('*')) if p.is_file() and not p.is_symlink()]

    def set_contract(self, value):
        if self.contract is not None:
            raise ValueError('Test contract is immutable for this request')
        if not isinstance(value, dict) or not isinstance(value.get('checks'), list) or not value['checks']:
            raise ValueError('Contract needs nonempty checks')
        seen = set()
        for item in value['checks']:
            if not isinstance(item, dict) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,47}', item.get('id', '')):
                raise ValueError('Invalid check id')
            if item['id'] in seen or not isinstance(item.get('statement'), str) or not item['statement'].strip():
                raise ValueError('Checks need unique ids and nonempty statements')
            seen.add(item['id'])
        if len(json.dumps(value)) > 16000:
            raise ValueError('Contract too large')
        self.write('notes/contract.json', json.dumps(value, indent=2))
        self.contract = value
        return {'contract_sha256': sha(json.dumps(value, sort_keys=True)), 'checks': sorted(seen)}
