"""Retrain the experiment-only v4 models from stored experiment outcomes.

Runs as a low-priority timer job. The recipe is first trained without the newest 15%
of rows and scored on them. The full refit replaces the current model only when that
held-out score is no worse than the last accepted score (within 5%) and beats the
original predictor, so continuous learning cannot silently degrade the experiment
policies. Production never loads these models.
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import joblib
import numpy as np
from scipy.stats import kendalltau
from sklearn.ensemble import HistGradientBoostingRegressor

sys.path.insert(0, str(Path.home()/'round'))
from length_v4 import LengthV4, route_row  # noqa: E402

STEP = 1000
# Held-out natural-stop MAE of the first v4.1 release (offline report, 2,481 rows).
FIRST_SCORE = 171.5


def load_rows(workspace):
    db = sqlite3.connect(f"file:{workspace/'central-training.sqlite3'}?mode=ro", uri=True)
    rows = {r[0]: r for r in db.execute('''SELECT s.request_index, s.input_tokens, s.output_tokens, e.finish_reason,
        e.baseline, s.candidates, s.server, s.after_routing_seconds
        FROM samples s JOIN length_evaluation e USING(request_index)
        WHERE s.outcome='complete' AND s.output_tokens > 0 ORDER BY s.request_index''')}
    prompts = {}
    with (workspace/'workload/requests.jsonl').open(encoding='utf-8') as stream:
        for i, line in enumerate(stream):
            if i > max(rows):
                break
            if i in rows:
                prompts[i] = json.loads(line)['prompt']
    return [dict(zip(('index', 'input', 'output', 'finish', 'baseline', 'candidates', 'server', 'after'), r),
                 prompt=prompts[r[0]]) for r in rows.values()]


def score(pred, y, natural):
    pred = np.clip(pred, 1, 2000)
    return {'natural_mae': round(float(np.abs(pred[natural] - y[natural]).mean()), 1),
            'kendall_tau': round(float(kendalltau(pred[natural], y[natural]).statistic), 3)}


def fit_length(rows):
    return LengthV4().fit([r['prompt'] for r in rows], [r['input'] for r in rows],
                          [min(r['baseline'], 2000) for r in rows], [r['output'] for r in rows])


def predict(model, rows):
    return model.predict([r['prompt'] for r in rows], [r['input'] for r in rows],
                         [min(r['baseline'], 2000) for r in rows])


def route_data(rows, guesses):
    x, y = [], []
    for r, guess in zip(rows, guesses):
        if not r['candidates'] or not r['after']:
            continue
        chosen = next((c for c in json.loads(r['candidates']) if c['server_id'] == r['server']), None)
        if chosen is None:
            continue
        x.append(route_row(r['input'], float(guess), chosen['inflight'], chosen.get('running'),
                           chosen.get('waiting'), chosen.get('kv_usage')))
        y.append(r['after'])
    return np.array(x, float), np.array(y)


def route_model():
    return HistGradientBoostingRegressor(max_iter=400, learning_rate=.05, random_state=0, monotonic_cst=[1] * 6)


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--workspace', type=Path, default=Path.home()/'.local/share/qless-training')
    cli.add_argument('--force', action='store_true')
    args = cli.parse_args()
    out = args.workspace/'models-v4'
    state_path = out/'state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {'trained_until': 18512, 'version': 'v4.1'}
    started = time.time()
    rows = load_rows(args.workspace)
    newest = rows[-1]['index'] + 1
    if not args.force and newest - state.get('trained_until', 0) < STEP:
        print(json.dumps({'skipped': True, 'new_rows': newest - state.get('trained_until', 0)}))
        return
    # Hold out the newest 15% for the replace-or-keep decision.
    cut = int(len(rows) * .85)
    train, hold = rows[:cut], rows[cut:]
    y = np.array([r['output'] for r in hold], float)
    natural = np.array([r['finish'] == 'stop' for r in hold])
    candidate = fit_length(train)
    new_score = score(predict(candidate, hold), y, natural)
    original = score(np.array([min(r['baseline'], 2000) for r in hold], float), y, natural)
    # The current model has trained on part of this window, so its own score here would be
    # optimistic. Compare with the score it earned when it was accepted on unseen rows.
    reference = (state.get('holdout') or {}).get('natural_mae', FIRST_SCORE)
    accepted = new_score['natural_mae'] <= reference * 1.05 and new_score['natural_mae'] < original['natural_mae']
    record = {'at': time.time(), 'rows': len(rows), 'trained_until': newest, 'holdout': len(hold),
              'holdout_first_index': hold[0]['index'], 'candidate': new_score,
              'current': {'natural_mae': reference}, 'original': original, 'accepted': bool(accepted)}
    if accepted:
        # The router sees forecasts for requests the length model has not trained on,
        # so fit the completion-time model only on held-out forecasts.
        x, t = route_data(hold, predict(candidate, hold))
        final_length = fit_length(rows)
        final_route = route_model().fit(x, t)
        stage = out/'next'
        stage.mkdir(exist_ok=True)
        joblib.dump(final_length, stage/'length_v4.joblib', compress=3)
        joblib.dump(final_route, stage/'route_v1.joblib')
        for name in ('length_v4.joblib', 'route_v1.joblib'):
            (stage/name).replace(out/name)
        state = {'version': state.get('version', 'v4.1'), 'trained_until': newest, 'rows': len(rows),
                 'updated_at': time.time(), 'holdout': new_score, 'route_rows': int(len(t))}
        state_path.write_text(json.dumps(state))
    else:
        state['last_rejected_at'] = time.time()
        state_path.write_text(json.dumps(state))
    record['seconds'] = round(time.time() - started)
    history_path = out/'history.json'
    history = json.loads(history_path.read_text()) if history_path.exists() else []
    history.append(record)
    history_path.write_text(json.dumps(history[-100:]))
    print(json.dumps(record))


if __name__ == '__main__':
    main()
