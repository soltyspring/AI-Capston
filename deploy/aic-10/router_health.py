import json, urllib.request
d = json.load(urllib.request.urlopen('http://127.0.0.1:8008/status', timeout=5))
print('v4', d.get('v4_policies'), 'policy', d['policy'], 'healthy', [s['healthy'] for s in d['servers'].values()],
      'inflight', d['inflight'], 'waiting', d['queue']['waiting'])
