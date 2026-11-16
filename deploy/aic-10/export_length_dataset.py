"""Export completed experiment outcomes with prompts for offline model work."""
import json
import sqlite3
import sys
from pathlib import Path

root = Path.home()/'.local/share/qless-training'
db = sqlite3.connect(f"file:{root/'central-training.sqlite3'}?mode=ro", uri=True)
rows = {r[0]: r for r in db.execute('''SELECT s.request_index, s.input_tokens, s.output_tokens, s.budget,
    e.finish_reason, e.baseline, e.candidate, s.recorded, s.server, s.first_seconds, s.completion_seconds,
    s.queue_seconds, s.after_routing_seconds, s.burst_size, s.predicted_tokens
    FROM samples s JOIN length_evaluation e USING(request_index) WHERE s.outcome='complete' ''')}
keys = ('request_index','input_tokens','output_tokens','budget','finish_reason','baseline','candidate','recorded',
        'server','first_seconds','completion_seconds','queue_seconds','after_routing_seconds','burst_size','predicted_tokens')
out = sys.stdout
with (root/'workload/requests.jsonl').open(encoding='utf-8') as stream:
    for i, line in enumerate(stream):
        if i > max(rows):
            break
        if i in rows:
            item = dict(zip(keys, rows[i]))
            item['prompt'] = json.loads(line)['prompt']
            out.write(json.dumps(item, ensure_ascii=False) + '\n')
