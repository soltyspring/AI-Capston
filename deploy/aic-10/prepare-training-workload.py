#!/usr/bin/env python3
"""Build a seekable request set from the installed QLess question corpus."""
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys


def prepare(source, destination):
    destination.mkdir(parents=True, exist_ok=False)
    counts = Counter()
    seen = set()
    digest = hashlib.sha256()
    with source.open('rb') as rows, (destination / 'requests.jsonl').open('xb') as output:
        for line in rows:
            row = json.loads(line)
            prompt = row.get('prompt')
            if not isinstance(prompt, str) or not prompt.strip():
                continue
            encoded = prompt.encode('utf-8')
            if len(encoded) > 32_000:
                continue
            signature = hashlib.sha256(encoded).digest()
            if signature in seen:
                continue
            seen.add(signature)
            # Exercise different decode budgets with a deterministic, reproducible mix.
            budget = (64, 128, 224, 320)[signature[0] % 4]
            selected = {'prompt': prompt, 'max_tokens': budget}
            data = (json.dumps(selected, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
            output.write(data)
            digest.update(data)
            counts[str(budget)] += 1
    if not counts:
        raise ValueError('empty corpus')
    manifest = {'version': 1, 'requests': sum(counts.values()), 'sha256': digest.hexdigest(),
                'decode_budgets': dict(counts), 'source': source.name}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    print(json.dumps(prepare(Path(sys.argv[1]), Path(sys.argv[2]))))
