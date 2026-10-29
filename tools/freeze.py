"""Allowlisted source freeze: never includes weights, secrets, fixtures, or logs."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ["AGENTS.md", "Dockerfile", ".dockerignore", ".gitignore", ".gitattributes", "manifest.json", "MODEL.md", "baseline.py", "run.sh", "run_baseline.sh", "README.md", "REPORT.md", "serve/container.env.example", "serve/fpga-token.example.txt"]
DIRS = ["agent", "serve", "skill", "model", "tools", "tests", "docs"]
FILES.append('vendor/slang-linux-v12.0.tar.gz')
FILES.append('serve/license_probe.sv')


def inventory():
    paths = [ROOT / item for item in FILES]
    for directory in DIRS:
        paths.extend(path for path in (ROOT / directory).rglob("*") if path.is_file()
                     and "__pycache__" not in path.parts and path.suffix in (".py", ".md", ".sh", ".json", ".toml"))
    return {str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths) if path.is_file()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "freeze.json")
    args = parser.parse_args()
    actual = inventory()
    if args.check:
        expected = json.loads(args.output.read_text(encoding="utf-8"))
        if actual != expected:
            raise SystemExit("Source freeze mismatch")
        print("Source freeze verified:", len(actual), "files")
    else:
        args.output.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
        print("Source freeze written:", len(actual), "files")
