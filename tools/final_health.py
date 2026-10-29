"""Save authenticated readiness using the pre-filled token file; never log the token."""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from serve.credentials import load_token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'out/health-final.json')
    args = parser.parse_args()
    load_token()
    port = int(os.environ.get('PORT', '7860'))
    request = urllib.request.Request('http://127.0.0.1:'+str(port)+'/v1/health',
                                     headers={'Authorization':'Bearer '+os.environ['FPGACHINA_TOKEN']})
    with urllib.request.urlopen(request, timeout=5) as response:
        body = json.load(response)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(body, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(body), flush=True)
    return 0 if body.get('ready') is True else 1


if __name__ == '__main__':
    raise SystemExit(main())
