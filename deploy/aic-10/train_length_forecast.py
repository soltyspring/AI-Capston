"""Fit and evaluate a censored log-length model on stored QLess experiment outcomes."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import time

import joblib
import numpy as np
from scipy.optimize import minimize
from scipy.special import log_ndtr

from length_forecast import features, extra_features, keyword_terms, load_candidate, estimate, TOPICS


def score(y, guesses, natural, budgets):
    absolute = np.abs(y - guesses)
    return {'natural_samples': int(natural.sum()),
            'natural_wape': round(float(100 * absolute[natural].sum()/y[natural].sum()), 3),
            'natural_mae': round(float(absolute[natural].mean()), 3),
            'natural_predicted_mean': round(float(guesses[natural].mean()), 3),
            'capped_mae': round(float(np.abs(y - np.minimum(budgets, guesses)).mean()), 3),
            'capped_underprediction': round(float(np.maximum(y - np.minimum(budgets, guesses), 0).mean()), 3)}


def fit_model(x, y, natural, cut):
    center = x[:cut].mean(axis=0)
    scale = np.maximum(x[:cut].std(axis=0), .1)
    xz = (x-center)/scale
    design = np.column_stack((np.ones(cut), xz[:cut]))
    z = np.log1p(y[:cut])
    finished = natural[:cut]

    def loss(params):
        sigma = np.exp(params[-1])
        q = (z - design @ params[:-1]) / sigma
        tail = log_ndtr(-q)
        dq = np.where(finished, q, np.exp(-.5*q*q - .5*np.log(2*np.pi) - tail))
        value = np.where(finished, .5*q*q+params[-1], -tail).mean() + .003*(params[1:-1]**2).sum()
        grad = np.r_[design.T @ (-dq/sigma)/cut, (finished-dq*q).mean()]
        grad[1:-1] += .006*params[1:-1]
        return value, grad

    fitted = minimize(loss, [np.log1p(np.median(y[:cut]))]+[0]*x.shape[1]+[0],
                      method='L-BFGS-B', jac=True, options={'maxiter':500},
                      bounds=[(0,9)]+[(-6,6)]*x.shape[1]+[(-2.5,2)])
    if not fitted.success:
        raise RuntimeError(fitted.message)
    w = fitted.x
    value = w[0]+xz[cut:] @ w[1:-1]
    guess = np.rint(np.expm1(np.clip(value, np.log1p(1), np.log1p(4096))))
    return guess, {'center':center.tolist(), 'scale':scale.tolist(),
                   'weights':w[1:-1].tolist(), 'intercept':float(w[0])}


def train(workspace, router, before=None):
    config = json.loads((router/'config.vllm.json').read_text())
    def model_file(key):
        path = Path(config[key])
        return joblib.load(path if path.is_absolute() else router/path)
    classifier, forecaster = model_file('category_model_path'), model_file('predictor_path')
    db_file = workspace/'central-training.sqlite3'
    with sqlite3.connect(f'file:{db_file}?mode=ro', uri=True) as db:
        logged = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='length_evaluation'").fetchone()
        metadata = 'e.finish_reason, e.baseline' if logged else 'NULL, NULL'
        join = 'LEFT JOIN length_evaluation e USING(request_index)' if logged else ''
        rows = db.execute(f'''SELECT s.request_index, input_tokens, output_tokens, budget, {metadata}
            FROM samples s {join} WHERE outcome='complete' AND input_tokens BETWEEN 1 AND 15872
              AND output_tokens > 0 AND output_tokens <= budget AND
              {"(e.finish_reason IS NULL OR e.finish_reason IN ('stop','length'))" if logged else '1=1'}
              AND (? IS NULL OR s.request_index < ?)
            ORDER BY {"s.request_index DESC LIMIT 8000" if before is not None else "recorded, s.request_index"}''',
            (before,before)).fetchall()
        if before is not None:
            rows.reverse()
    if len(rows)<1000:
        raise ValueError('At least 1,000 completed samples are needed')
    wanted = {row[0] for row in rows}
    prompts = {}
    with (workspace/'workload/requests.jsonl').open(encoding='utf-8') as stream:
        for i,line in enumerate(stream):
            if i in wanted:
                prompts[i] = json.loads(line)['prompt']
                if len(prompts) == len(wanted):
                    break
    words = [prompts[row[0]] for row in rows]
    class_model = classifier['estimator']
    category = class_model.classes_[class_model.predict_proba([p[:classifier['max_chars']] for p in words]).argmax(axis=1)]
    lookups = [[row[1], str(c)] for row,c in zip(rows,category)]
    baseline = np.maximum(0, np.rint(forecaster['estimator'].predict(lookups))).astype(int)
    baseline = np.array([r[5] if r[5] is not None else b for r,b in zip(rows,baseline)])
    y = np.array([row[2] for row in rows])
    budgets = np.array([row[3] for row in rows])
    # Legacy rows lack a finish reason. A below-cap result is a completion proxy.
    natural = np.array([row[4]=='stop' if row[4] is not None else row[2]<row[3] for row in rows])
    cut = len(rows) if before is not None else int(len(rows)*.8)
    verified = np.array([row[4]=='stop' for row in rows[cut:]])
    if before is None and verified.sum()<30:
        raise ValueError('At least 30 confirmed stop results are needed in the holdout')
    counts = Counter(word for prompt in words[:cut] for word in keyword_terms(prompt))
    vocabulary = [word for word, count in sorted(counts.items(), key=lambda item: (-item[1],item[0]))
                  if 10 <= count <= cut*.65][:96]
    base = np.array([features(p,row[1],b) for p,row,b in zip(words,rows,baseline)])
    extras = np.array([extra_features(p,vocabulary) for p in words])
    if before is not None:
        if natural.sum()<30:
            raise ValueError('Too few uncensored training observations')
        _, params = fit_model(np.column_stack((base, extras[:,len(TOPICS):])), y,natural,cut)
        return {'version':3,'accepted':True,'created_at':time.time(),'vocabulary':vocabulary,
                'training_samples':cut,'before':before,**params}, None
    end_topics, end_keywords = len(TOPICS), len(TOPICS)+len(vocabulary)
    groups = {'topic':extras[:,:end_topics], 'keywords':extras[:,end_topics:end_keywords],
              'format':extras[:,end_keywords:]}

    def metrics(guess):
        return score(y[cut:],guess,verified,budgets[cut:])

    comparisons = {'original':metrics(baseline[cut:])}
    old_path = workspace/'output-length-v1.json'
    old = load_candidate(old_path) if old_path.exists() else load_candidate(workspace/'output-length-candidate.json')
    if old and old['version']==1:
        fixed = np.array([estimate(old,p,row[1],b) for p,row,b in zip(words[cut:],rows[cut:],baseline[cut:])])
        comparisons['previous_candidate'] = metrics(fixed)
    variants = {'base':(), 'full':tuple(groups),
                **{f'without_{name}':tuple(k for k in groups if k!=name) for name in groups}}
    for name,included in variants.items():
        guess, params = fit_model(np.column_stack([base,*[groups[k] for k in included]]),y,natural,cut)
        comparisons[name] = metrics(guess)
        if name=='full':
            full_params = params
    candidate = comparisons['full']
    reference = comparisons.get('previous_candidate', comparisons['base'])
    original = comparisons['original']
    accepted = (candidate['natural_mae'] < min(reference['natural_mae'], original['natural_mae'])
                and candidate['capped_mae'] <= reference['capped_mae']
                and candidate['capped_underprediction'] <= reference['capped_underprediction']+5)
    report = {'training_samples':cut, 'holdout_samples':len(rows)-cut,
              'verified_natural_holdout':int(verified.sum()), 'accepted_for_shadow':bool(accepted),
              'variants':comparisons}
    return {'version':2, 'accepted':bool(accepted), 'created_at':time.time(), 'vocabulary':vocabulary,
            **full_params}, report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace',type=Path,default=Path.home()/'.local/share/qless-training')
    parser.add_argument('--router',type=Path,default=Path.home()/'round')
    parser.add_argument('--before', type=int)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if (args.before is None) != (args.output is None):
        parser.error('--before and --output must be supplied together')
    model, report = train(args.workspace,args.router,args.before)
    if args.output:
        staged = args.output.with_suffix('.next')
        staged.write_text(json.dumps(model,allow_nan=False),encoding='utf-8')
        staged.replace(args.output)
        return
    for name,result in [('output-length-candidate-v2.json',model),('output-length-comparison.json',report)]:
        output = args.workspace/name
        staged = output.with_suffix('.next')
        staged.write_text(json.dumps(result,allow_nan=False),encoding='utf-8')
        staged.replace(output)
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    main()
