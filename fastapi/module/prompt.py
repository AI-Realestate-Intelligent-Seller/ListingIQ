import json
from pathlib import Path

PROMPT_FILE = Path(__file__).resolve().parent / 'prompt.json'

with PROMPT_FILE.open('r', encoding='utf-8') as f:
    prompt = json.load(f)
