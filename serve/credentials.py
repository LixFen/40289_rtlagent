"""Load the competition token saved before deployment, without logging it."""
import os
from pathlib import Path

from agent.paths import ROOT


def load_token(environ=None, root=ROOT):
    env = os.environ if environ is None else environ
    path = Path(env.get('FPGACHINA_TOKEN_FILE') or Path(root)/'serve/fpga-token.local.txt')
    try:
        token = path.read_text(encoding='utf-8-sig').strip()
    except FileNotFoundError:
        token = ''
    if not token and env.get('FPGACHINA_TOKEN'):
        return
    if not token or any(character.isspace() for character in token):
        raise RuntimeError('FPGACHINA_TOKEN is missing or invalid; fill the single-line token file: '+str(path))
    env['FPGACHINA_TOKEN'] = token
