"""Export logged routing decisions for offline completion-time model work."""
import json
import sqlite3
import sys
from pathlib import Path

root = Path.home()/'.local/share/qless-training'
db = sqlite3.connect(f"file:{root/'central-training.sqlite3'}?mode=ro", uri=True)
keep = ('server_id', 'inflight', 'estimated_seconds', 'estimated_service_seconds', 'estimated_queued_seconds',
        'running', 'waiting', 'kv_usage', 'score', 'selected')
for row in db.execute('''SELECT request_index, server, candidates, input_tokens, output_tokens, predicted_tokens,
        first_seconds, completion_seconds, queue_seconds, after_routing_seconds, burst_size, recorded
        FROM samples WHERE outcome='complete' AND candidates IS NOT NULL ORDER BY request_index'''):
    item = dict(zip(('request_index', 'server', 'candidates', 'input_tokens', 'output_tokens', 'predicted_tokens',
                     'first_seconds', 'completion_seconds', 'queue_seconds', 'after_routing_seconds',
                     'burst_size', 'recorded'), row))
    item['candidates'] = [{k: c.get(k) for k in keep} for c in json.loads(item['candidates'])]
    sys.stdout.write(json.dumps(item) + '\n')
