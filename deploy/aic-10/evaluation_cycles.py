"""Resumable 1,000-request training and paired GPU evaluation windows."""
import asyncio
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3

from length_forecast import estimate, load_candidate

WINDOW = 100
POLICIES = ('round_robin','shortest_eval','shadow_eval')


def stage(start, index):
    block = (index-start)//WINDOW
    edge = start+(block+1)*WINDOW
    return {'phase':'evaluation' if block%2 else 'training', 'done':(index-start)%WINDOW,
            'checkpoint': edge-WINDOW if block%2 else edge, 'end':edge, 'label':(block//2+1)*WINDOW}


def p95(values):
    return round(sorted(values)[math.ceil(.95*len(values))-1],3) if values else None


class EvaluationCycles:
    def __init__(self, db, workspace):
        self.db, self.workspace = db, workspace
        self.root = workspace/'cycles'
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.report = self.root/'dashboard.json'
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS cycle_start (id INTEGER PRIMARY KEY CHECK(id=1), start INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS cycle_checkpoints (boundary INTEGER PRIMARY KEY, observations INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS cycle_batches (start INTEGER PRIMARY KEY, stop INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS cycle_trials (request_index INTEGER, policy TEXT, boundary INTEGER,
                outcome TEXT NOT NULL, PRIMARY KEY(request_index,policy));
            CREATE INDEX IF NOT EXISTS cycle_trials_boundary ON cycle_trials(boundary,policy);
            CREATE TABLE IF NOT EXISTS cycle_results (boundary INTEGER PRIMARY KEY, result TEXT NOT NULL);
        ''')
        with db:
            db.execute('INSERT OR IGNORE INTO cycle_start SELECT 1,next_index FROM progress WHERE id=1')
        self.start = db.execute('SELECT start FROM cycle_start').fetchone()[0]
        self.publish(db.execute('SELECT next_index FROM progress').fetchone()[0])

    def phase(self, index):
        return stage(self.start,index)

    async def freeze(self, boundary, observations):
        if self.db.execute('SELECT 1 FROM cycle_checkpoints WHERE boundary=?',(boundary,)).fetchone():
            return
        root = self.root/str(boundary)
        root.mkdir(mode=0o700, exist_ok=True)
        target = root/'linucb.sqlite3'
        if not target.exists():
            staged = root/'linucb.next'
            source = self.workspace/'shadow-linucb.sqlite3'
            with closing(sqlite3.connect(f'{source.as_uri()}?mode=ro',uri=True)) as db, closing(sqlite3.connect(staged)) as out:
                # Save the learned scores, without duplicating historical request logs.
                with out:
                    out.execute('CREATE TABLE IF NOT EXISTS arms (server_id TEXT PRIMARY KEY,a TEXT,b TEXT,n INTEGER)')
                    out.executemany('INSERT OR REPLACE INTO arms VALUES (?,?,?,?)',db.execute('SELECT * FROM arms'))
            staged.replace(target)
        model = root/'predictor.json'
        if not model.exists():
            env = {**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
            python = Path.home()/'qwen3_8b_100k_questions_bundle/.token_predict_venv/bin/python'
            child = await asyncio.create_subprocess_exec(str(python),str(self.workspace/'train_length_forecast.py'),
                '--workspace',str(self.workspace),'--before',str(boundary),'--output',str(model),env=env)
            if await child.wait():
                raise RuntimeError('Checkpoint output prediction training failed')
        with self.db:
            self.db.execute('INSERT INTO cycle_checkpoints VALUES (?,?)',(boundary,observations))

    def batch_size(self, cycle, index, desired):
        row = self.db.execute('SELECT stop FROM cycle_batches WHERE start<=? AND stop>? ORDER BY start DESC LIMIT 1',
                              (index,index)).fetchone()
        return row[0]-index if row else min(desired,cycle['end']-index)

    async def evaluate(self, cycle, index, requests, client, health, save, run_batch, available, rejected_type):
        if not requests:
            return
        boundary = cycle['checkpoint']
        remembered = self.db.execute('SELECT start FROM cycle_batches WHERE start<=? AND stop>? ORDER BY start DESC LIMIT 1',
                                     (index,index)).fetchone()
        anchor = remembered[0] if remembered else index
        if not remembered:
            with self.db:
                self.db.execute('INSERT INTO cycle_batches VALUES (?,?)',(anchor,requests[-1][0]+1))
        rotation = (self.db.execute('SELECT COUNT(*) FROM cycle_batches WHERE start>=? AND start<?',
                                    (boundary,anchor)).fetchone()[0])%len(POLICIES)
        ids = [item[0] for item in requests]
        primary = {}
        forecasts = {}
        for policy in POLICIES[rotation:]+POLICIES[:rotation]:
            existing = {key:json.loads(outcome) for key,outcome in self.db.execute(
                'SELECT request_index,outcome FROM cycle_trials WHERE boundary=? AND policy=? AND request_index BETWEEN ? AND ?',
                (boundary,policy,ids[0],ids[-1]))}
            todo = [item for item in requests if item[0] not in existing]
            if todo:
                if len(todo)!=len(requests) or await available(health)!=3:
                    return
                results = {}
                def record(item, outcome):
                    if isinstance(outcome,rejected_type):
                        results[item[0]] = {'failure':'input_limit'}
                    elif isinstance(outcome,dict):
                        results[item[0]] = outcome
                await run_batch(client,todo,health,record,policy,boundary)
                if len(results)!=len(todo) or await available(health)!=3:
                    return
                if policy!='shadow_eval':
                    for outcome in results.values():
                        outcome.pop('candidates',None)
                with self.db:
                    self.db.executemany('INSERT INTO cycle_trials VALUES (?,?,?,?)',
                        ((key,policy,boundary,json.dumps(outcome,allow_nan=False)) for key,outcome in results.items()))
                existing = results
                # Drain a burst before replaying it with another policy.
                await asyncio.sleep(.5)
            if policy=='shadow_eval':
                primary = existing
            for key, outcome in existing.items():
                if not outcome.get('failure'):
                    observation = (outcome['input_tokens'], outcome['predicted_tokens'], outcome.get('effective_output_limit'))
                    if key in forecasts and forecasts[key] != observation:
                        raise RuntimeError('Policy trials used different input or forecast tokens')
                    forecasts[key] = observation
        model = load_candidate(self.root/str(boundary)/'predictor.json')
        for item in requests:
            outcome = primary[item[0]]
            if not outcome.get('failure') and not ('cycle_prediction' in outcome):
                outcome = dict(outcome, cycle_prediction=estimate(model,item[2]['prompt'],outcome['input_tokens'],
                                                                 outcome['raw_prediction']))
                # Persist the length result with the selected policy's single actual response.
                with self.db:
                    self.db.execute('UPDATE cycle_trials SET outcome=? WHERE request_index=? AND policy=?',
                                    (json.dumps(outcome,allow_nan=False),item[0],'shadow_eval'))
            save(item,outcome)

    def finish(self, boundary):
        slices = {policy:[] for policy in POLICIES}
        for policy,outcome in self.db.execute('SELECT policy,outcome FROM cycle_trials WHERE boundary=?',(boundary,)):
            slices[policy].append(json.loads(outcome))
        if any(len(rows)!=WINDOW for rows in slices.values()):
            return
        metrics = {}
        for policy,rows in slices.items():
            success = [r for r in rows if not r.get('failure')]
            metrics[policy] = {'first_p95':p95([r['first_seconds'] for r in success if r['first_seconds'] is not None]),
                               'completion_p95':p95([r['completion_seconds'] for r in success]),
                               'failures':WINDOW-len(success)}
        natural = [r for r in slices['shadow_eval'] if r.get('finish_reason')=='stop' and r['output_tokens']>0]
        prediction = {'samples':len(natural)}
        if natural:
            for name,field in [('original','raw_prediction'),('candidate','cycle_prediction')]:
                prediction[name] = round(sum(abs(r[field]-r['output_tokens']) for r in natural)/len(natural),3)
        result = {'checkpoint':boundary-self.start,'prediction':prediction,'policies':metrics}
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO cycle_results VALUES (?,?)',(boundary,json.dumps(result)))

    def publish(self,index):
        state = self.phase(index)
        previous = index-WINDOW
        if index>self.start and (index-self.start)%(2*WINDOW)==0 and not self.db.execute(
                'SELECT 1 FROM cycle_results WHERE boundary=?',(previous,)).fetchone():
            self.finish(previous)
        state['frozen'] = bool(self.db.execute('SELECT 1 FROM cycle_checkpoints WHERE boundary=?',
                                              (state['checkpoint'],)).fetchone())
        state['results'] = [json.loads(r[0]) for r in self.db.execute('SELECT result FROM cycle_results ORDER BY boundary')]
        state['window'] = WINDOW
        if index >= self.db.execute('SELECT total FROM progress').fetchone()[0]:
            state['phase'] = 'finished'
        staged = self.report.with_suffix('.next')
        staged.write_text(json.dumps(state,allow_nan=False))
        staged.replace(self.report)
