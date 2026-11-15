"""Read-only experiment checkpoints for the loopback router."""
from contextlib import closing
from functools import lru_cache
import json
from pathlib import Path
import sqlite3

import numpy as np

from length_forecast import load_candidate
from vllm_bandit import DIM

ROOT = Path.home()/'.local/share/qless-training/cycles'


class FrozenLinUCB:
    def __init__(self, path, alpha):
        self.alpha = alpha
        with closing(sqlite3.connect(f'{path.as_uri()}?mode=ro&immutable=1', uri=True)) as db:
            self.arms = {sid:(np.array(json.loads(a)),np.array(json.loads(b)))
                         for sid,a,b in db.execute('SELECT server_id,a,b FROM arms')}

    def score(self, sid, x):
        a,b = self.arms.get(sid,(np.eye(DIM),np.zeros(DIM)))
        v = np.linalg.solve(a,x)
        return float(b @ v + self.alpha*np.sqrt(max(0,x @ v)))


@lru_cache(maxsize=2)
def models(checkpoint, alpha):
    root = ROOT/str(checkpoint)
    candidate = load_candidate(root/'predictor.json')
    if not candidate or candidate.get('version') != 3 or candidate.get('before') != checkpoint:
        raise ValueError('Missing experiment predictor checkpoint')
    return candidate, FrozenLinUCB(root/'linucb.sqlite3', alpha)


def summary():
    try:
        return json.loads((ROOT/'dashboard.json').read_text())
    except (OSError, ValueError):
        return None
