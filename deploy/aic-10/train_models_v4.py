"""Train and evaluate the v4 output-length model and the learned routing-time model offline."""
import argparse
import gzip
import json
import re
import sys
import time
from pathlib import Path

import joblib
import numpy as np
from scipy.stats import kendalltau
from sklearn.ensemble import HistGradientBoostingRegressor

sys.path.insert(0, str(Path(__file__).parent))
from length_forecast import estimate as v3_estimate  # noqa: E402
from length_v4 import LengthV4, route_row  # noqa: E402

TYPES = (('한 문장', re.compile(r'한\s*문장|한두\s*문장|one.sentence|two sentences', re.I)),
         ('JSON', re.compile(r'json|스키마|schema', re.I)),
         ('코드', re.compile(r'코드|code|python|sql|함수|function', re.I)),
         ('요약', re.compile(r'요약|summar', re.I)),
         ('목록·표', re.compile(r'목록|항목|표로|표\s|bullet|table|checklist|체크리스트', re.I)))


def kind(prompt):
    tail = prompt[-400:]
    return next((name for name, pattern in TYPES if pattern.search(tail)), '기타')


def metrics(pred, y, natural):
    pred = np.clip(pred, 1, 2000)
    return {'natural_mae': round(float(np.abs(pred[natural] - y[natural]).mean()), 1),
            'all_mae': round(float(np.abs(pred - y).mean()), 1),
            'kendall_tau': round(float(kendalltau(pred[natural], y[natural]).statistic), 3),
            'natural': int(natural.sum())}


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--length', type=Path, required=True)
    cli.add_argument('--router', type=Path, required=True)
    cli.add_argument('--v3', type=Path, required=True)
    cli.add_argument('--out', type=Path, required=True)
    args = cli.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = sorted((json.loads(line) for line in gzip.open(args.length, 'rt', encoding='utf-8')),
                  key=lambda r: r['request_index'])
    n = len(rows)
    cut, test = int(n * .70), int(n * .85)
    prompts = [r['prompt'] for r in rows]
    tokens = np.array([r['input_tokens'] for r in rows], float)
    base = np.array([min(r['baseline'], 2000) for r in rows], float)
    y = np.array([r['output_tokens'] for r in rows], float)
    natural = np.array([r['finish_reason'] == 'stop' for r in rows])

    started = time.time()
    holdout = LengthV4().fit(prompts[:cut], tokens[:cut], base[:cut], y[:cut])
    late = holdout.predict(prompts[cut:], tokens[cut:], base[cut:])
    v3 = json.loads(args.v3.read_text())
    v3_pred = np.array([v3_estimate(v3, p, t, b) for p, t, b in zip(prompts[test:], tokens[test:], base[test:])], float)
    s = slice(test, n)
    v4_test = late[test - cut:]
    report = {'trained_rows': cut, 'test_rows': n - test, 'test_first_index': rows[test]['request_index'],
              'v3_checkpoint': v3.get('before'),
              'models': {'original': metrics(base[s], y[s], natural[s]), 'keywords_v3': metrics(v3_pred, y[s], natural[s]),
                         'text_v4': metrics(v4_test, y[s], natural[s])},
              'types': []}
    kinds = np.array([kind(p) for p in prompts[test:]])
    for name in ('한 문장', '목록·표', 'JSON', '코드', '요약', '기타'):
        m = (kinds == name) & natural[s]
        if m.sum() >= 20:
            report['types'].append({'type': name, 'samples': int(m.sum()), 'actual_mean': round(float(y[s][m].mean()), 1),
                                    'original': round(float(np.abs(base[s][m] - y[s][m]).mean()), 1),
                                    'keywords_v3': round(float(np.abs(v3_pred[m] - y[s][m]).mean()), 1),
                                    'text_v4': round(float(np.abs(v4_test[m] - y[s][m]).mean()), 1)})
    print(json.dumps(report, ensure_ascii=False), flush=True)

    # Routing-time model uses out-of-sample length predictions, as at deployment.
    v4_late = {rows[i]['request_index']: float(late[i - cut]) for i in range(cut, n)}
    decisions = []
    for line in gzip.open(args.router, 'rt'):
        r = json.loads(line)
        if r['request_index'] not in v4_late or not r['after_routing_seconds']:
            continue
        chosen = next(c for c in r['candidates'] if c['server_id'] == r['server'])
        decisions.append((r, chosen))
    decisions.sort(key=lambda d: d[0]['request_index'])
    x = np.array([route_row(r['input_tokens'], v4_late[r['request_index']], c['inflight'], c['running'],
                            c['waiting'], c['kv_usage']) for r, c in decisions], float)
    t = np.array([r['after_routing_seconds'] for r, _ in decisions])
    rule = np.array([c['estimated_seconds'] if c['estimated_seconds'] is not None else np.nan for _, c in decisions])
    score = np.array([c['score'] if c['score'] is not None else np.nan for _, c in decisions])
    half = len(t) // 2
    # More work on a server must never look faster; this also improves held-out accuracy.
    def route_model():
        return HistGradientBoostingRegressor(max_iter=400, learning_rate=.05, random_state=0,
                                             monotonic_cst=[1] * x.shape[1])
    route = route_model().fit(x[:half], t[:half])
    learned = route.predict(x[half:])
    held = slice(half, None)
    finite = np.isfinite(rule[held]) & np.isfinite(score[held])
    report['routing'] = {'train_decisions': half, 'test_decisions': len(t) - half,
                         'rule_mae': round(float(np.abs(rule[held][finite] - t[held][finite]).mean()), 2),
                         'model_mae': round(float(np.abs(learned - t[held]).mean()), 2),
                         'rule_tau': round(float(kendalltau(rule[held][finite], t[held][finite]).statistic), 3),
                         'linucb_tau': round(float(kendalltau(-score[held][finite], t[held][finite]).statistic), 3),
                         'model_tau': round(float(kendalltau(learned, t[held]).statistic), 3)}
    print(json.dumps(report['routing']), flush=True)
    joblib.dump(route_model().fit(x, t), args.out / 'route_v1.joblib')
    final = LengthV4().fit(prompts, tokens, base, y)
    joblib.dump(final, args.out / 'length_v4.joblib', compress=3)
    report['training_seconds'] = round(time.time() - started)
    (args.out / 'offline-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print('saved', flush=True)


if __name__ == '__main__':
    main()
