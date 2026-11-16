#!/usr/bin/env python3
"""Generate training traffic through the production router and keep shadow models."""
import argparse
import asyncio
from contextlib import closing, suppress
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
import time

import httpx
from length_forecast import estimate, load_candidate

ROUTER = 'http://127.0.0.1:8008'
KST = timezone(timedelta(hours=9))
POLL = 3
DIM = 4
LOAD_PER_GPU = (1, 2, 3, 1, 5, 2, 7, 3)
CHECKPOINT_INTERVAL = 1000
LENGTH_CANDIDATE = Path.home()/'.local/share/qless-training/output-length-candidate.json'
length_model_cache = {'until':0, 'model':None}


def length_candidate():
    if time.monotonic() >= length_model_cache['until']:
        length_model_cache.update(until=time.monotonic()+20, model=load_candidate(LENGTH_CANDIDATE))
    return length_model_cache['model']


def burst_size(index, capacity):
    return capacity * LOAD_PER_GPU[(index // 24) % len(LOAD_PER_GPU)]


class RejectedInput(Exception):
    pass


def benchmark_window(now=None):
    now = now or datetime.now(KST)
    return now.weekday() == 6 and 455 <= now.hour * 60 + now.minute < 505


async def available(client, *, include_shadow=False):
    if benchmark_window():
        return False
    try:
        response = await client.get(ROUTER + '/status')
        response.raise_for_status()
        router = response.json()
        if not router.get('training_burst_supported') or 'shadow_linucb' not in router:
            return False
        if router['queue']['waiting'] or sum(router['inflight'].values()) > router['training_inflight']:
            return 0
        candidates = [row for row in router['servers'].values() if row.get('healthy')]
        if not all(0 <= time.time() - row['metrics']['timestamp'] < 10 and row['metrics']['gpus']
            and row['metrics']['cpu_pct'] < 85 and row['metrics']['memory_pct'] < 90
            and (router['training_inflight'] or row['vllm']['num_requests_waiting'] == 0)
            for row in candidates):
            return 0
        capacity = min(3, len(candidates))
        count = sum(router['shadow_linucb'].values())
        return (capacity, count) if include_shadow else capacity
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return False


def connect(path):
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    return db


def initialize(db, manifest):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS progress (
            id INTEGER PRIMARY KEY CHECK (id=1), corpus TEXT NOT NULL,
            total INTEGER NOT NULL, next_index INTEGER NOT NULL, next_byte INTEGER NOT NULL,
            completed INTEGER NOT NULL, rejected INTEGER NOT NULL, updated REAL);
        CREATE TABLE IF NOT EXISTS duration_models (
            server TEXT PRIMARY KEY, samples INTEGER NOT NULL, matrix TEXT NOT NULL, target TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS prediction_error_batches (
            batch INTEGER PRIMARY KEY, samples INTEGER NOT NULL, absolute_error INTEGER NOT NULL,
            actual_tokens INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS length_evaluation (
            request_index INTEGER PRIMARY KEY, baseline INTEGER NOT NULL, actual INTEGER NOT NULL,
            finish_reason TEXT NOT NULL, candidate INTEGER, model_version REAL);
        CREATE INDEX IF NOT EXISTS length_evaluation_version ON length_evaluation(model_version);
        CREATE TABLE IF NOT EXISTS samples (
            request_index INTEGER PRIMARY KEY, input_tokens INTEGER, output_tokens INTEGER,
            budget INTEGER NOT NULL, first_seconds REAL, completion_seconds REAL,
            outcome TEXT NOT NULL, recorded REAL NOT NULL, server TEXT, policy TEXT,
            candidates TEXT, predicted_tokens INTEGER, queue_seconds REAL, after_routing_seconds REAL,
            next_byte INTEGER NOT NULL);
    ''')
    if 'burst_size' not in {row['name'] for row in db.execute('PRAGMA table_info(samples)')}:
        db.execute('ALTER TABLE samples ADD COLUMN burst_size INTEGER')
    with db:
        db.execute('INSERT OR IGNORE INTO progress VALUES(1, ?, ?, 0, 0, 0, 0, NULL)',
                   (manifest['sha256'], manifest['requests']))
    saved = db.execute('SELECT * FROM progress WHERE id=1').fetchone()
    if (saved['corpus'], saved['total']) != (manifest['sha256'], manifest['requests']):
        raise ValueError('checkpoint and request manifest do not match')


def checkpoint(db, index, offset, row, measured, rejected=False, concurrency=None):
    now = time.time()
    observed = measured or {}
    budget = observed.get('effective_output_limit') or row['max_tokens']
    sid = observed.get('server')
    with db:
        if db.execute('SELECT 1 FROM samples WHERE request_index=?', (index,)).fetchone():
            return
        predicted, actual = observed.get('raw_prediction'), observed.get('output_tokens')
        if type(predicted) is int and type(actual) is int and observed.get('finish_reason'):
            db.execute('INSERT INTO length_evaluation VALUES (?,?,?,?,?,?)',
                       (index, predicted, actual, observed['finish_reason'],
                        observed.get('candidate_prediction'), observed.get('candidate_version')))
        if observed.get('finish_reason') == 'stop' and type(predicted) is int and type(actual) is int and actual > 0:
            latest = db.execute('SELECT batch,samples FROM prediction_error_batches ORDER BY batch DESC LIMIT 1').fetchone()
            batch = (latest['batch'] + int(latest['samples'] == 100)) if latest else 1
            db.execute('''INSERT INTO prediction_error_batches VALUES (?, 1, ?, ?)
                          ON CONFLICT(batch) DO UPDATE SET samples=samples+1,
                            absolute_error=absolute_error+excluded.absolute_error,
                            actual_tokens=actual_tokens+excluded.actual_tokens''',
                       (batch, abs(predicted-actual), actual))
        if measured:
            prior = db.execute('SELECT * FROM duration_models WHERE server=?', (sid,)).fetchone()
            a = json.loads(prior['matrix']) if prior else [[int(i == j) for j in range(DIM)] for i in range(DIM)]
            b = json.loads(prior['target']) if prior else [0.] * DIM
            chosen = next(item for item in measured['candidates'] if item['server_id'] == sid)
            estimate = chosen.get('estimated_seconds')
            if isinstance(estimate, (int, float)) and math.isfinite(estimate) and estimate >= 0:
                x = [1., math.log1p(measured['input_tokens']) / 10,
                     measured['predicted_tokens'] / 320, math.log1p(estimate) / 5]
                # Compare the router's forecast with observed time after assignment.
                target = measured['after_routing_seconds'] - estimate
                for j in range(DIM):
                    b[j] += x[j] * target
                    for k in range(DIM):
                        a[j][k] += x[j] * x[k]
                db.execute('''INSERT INTO duration_models VALUES (?, 1, ?, ?)
                              ON CONFLICT(server) DO UPDATE SET samples=samples+1, matrix=excluded.matrix, target=excluded.target''',
                           (sid, json.dumps(a), json.dumps(b)))
        db.execute('''INSERT INTO samples (request_index,input_tokens,output_tokens,budget,first_seconds,
                        completion_seconds,outcome,recorded,server,policy,candidates,predicted_tokens,
                        queue_seconds,after_routing_seconds,next_byte,burst_size)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                   (index, observed.get('input_tokens'), observed.get('output_tokens'), budget,
                    observed.get('first_seconds'), observed.get('completion_seconds'),
                    'rejected_input' if rejected else 'complete', now, sid, observed.get('policy'),
                    json.dumps(observed['candidates']) if 'candidates' in observed else None,
                    observed.get('predicted_tokens'), observed.get('queue_seconds'),
                    observed.get('after_routing_seconds'), offset, concurrency))
        cursor = db.execute('SELECT next_index,next_byte FROM progress WHERE id=1').fetchone()
        next_index, next_byte = cursor
        while item := db.execute('SELECT next_byte FROM samples WHERE request_index=?', (next_index,)).fetchone():
            next_index, next_byte = next_index + 1, item['next_byte']
        db.execute('''UPDATE progress SET next_index=?,next_byte=?,completed=completed+?,
                       rejected=rejected+?,updated=? WHERE id=1''',
                   (next_index, next_byte, int(not rejected), int(rejected), now))


def solve(matrix, target):
    rows = [list(row) + [value] for row, value in zip(matrix, target)]
    for column in range(len(rows)):
        pivot = rows[column][column]
        rows[column] = [value / pivot for value in rows[column]]
        for i, row in enumerate(rows):
            if i != column:
                factor = row[column]
                rows[i] = [value - row[column] * factor for value, factor in zip(row, rows[column])]
    return [row[-1] for row in rows]


def status(db):
    row = dict(db.execute('SELECT * FROM progress WHERE id=1').fetchone())
    row['shadow_models'] = {item['server']: {'samples': item['samples'],
        'coefficients': solve(json.loads(item['matrix']), json.loads(item['target']))}
        for item in db.execute('SELECT * FROM duration_models ORDER BY server')}
    row['latest_burst'] = [dict(item) for item in db.execute('''SELECT burst_size, COUNT(*) AS finished,
        ROUND(MAX(queue_seconds), 3) AS max_router_wait
        FROM (SELECT burst_size,queue_seconds FROM samples ORDER BY request_index DESC LIMIT 100)
        WHERE burst_size IS NOT NULL GROUP BY burst_size ORDER BY burst_size''')]
    return row


async def generate(client, row, probe_model='shadow_train', evaluation_checkpoint=None):
    started = time.monotonic()
    first = route = routed_at = None
    candidate_prediction = candidate_version = None
    model = length_candidate() if probe_model == 'shadow_train' else None
    experiment = {}
    if evaluation_checkpoint is not None:
        experiment = {'evaluation_checkpoint':evaluation_checkpoint,'benchmark':True}
        if probe_model == 'round_robin':
            experiment['policy_override'] = 'round_robin'
        elif probe_model in ('v4_eval', 'v4_sched'):
            # v4 policies use the continuously retrained models, not a frozen checkpoint.
            experiment = {'benchmark': True}
    async with client.stream('POST', ROUTER + '/chat/stream', json={
        'messages': [{'role': 'user', 'content': row['prompt']}],
        'training_probe': True, 'training_max_tokens': row['max_tokens'],
        'probe_model': 'production_eval' if probe_model == 'round_robin' else probe_model, **experiment,
    }) as response:
        if response.status_code == 422:
            await response.aread()
            if response.json().get('detail') == 'Input exceeds the model context limit':
                raise RejectedInput()
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line:
                continue
            payload = json.loads(line)
            kind = payload.get('event')
            if kind == 'error' and payload.get('status') == 409:
                return None
            if kind == 'error':
                if evaluation_checkpoint is not None:
                    return {'failure':'engine_error'}
                raise ValueError('engine stream error')
            if kind == 'routed':
                route, routed_at = payload, time.monotonic()
                if model and type(route.get('raw_predicted_output_tokens')) is int:
                    candidate_prediction = estimate(model, row['prompt'], route['input_tokens'],
                                                    route['raw_predicted_output_tokens'])
                    candidate_version = model['created_at']
            if kind == 'chunk' and first is None and any((choice.get('delta') or {}).get('content')
                    for choice in (payload.get('vllm') or {}).get('choices', [])):
                first = time.monotonic() - started
            if kind == 'complete' and route:
                expected = {'production_eval':'linucb','shortest_eval':'shortest_predicted',
                            'work_eval':'work_linucb','round_robin':'round_robin',
                            'v4_eval':'v4_route','v4_sched':'v4_sched'}.get(probe_model, 'shadow_linucb')
                if route['policy'] != expected:
                    raise ValueError('probe used the wrong routing model')
                now = time.monotonic()
                return {'input_tokens': route['input_tokens'], 'predicted_tokens': route['predicted_output_tokens'],
                        'output_tokens': payload['actual_output_tokens'], 'first_seconds': first,
                        'completion_seconds': now - started, 'after_routing_seconds': now - routed_at,
                        'server': route['server_id'], 'policy': route['policy'],
                        'candidates': route['candidates'], 'queue_seconds': route['queue_seconds'],
                        'raw_prediction':route.get('raw_predicted_output_tokens'),
                        'candidate_prediction': candidate_prediction, 'candidate_version': candidate_version,
                        'finish_reason':payload.get('finish_reason'), 'effective_output_limit':route.get('training_output_limit'),
                        'slot_seconds':payload.get('slot_seconds'),
                        'decode_seconds':(payload['last_output_seconds'] - payload['ttft_seconds']
                                          if payload.get('last_output_seconds') is not None
                                          and payload.get('ttft_seconds') is not None else None)}
    raise ValueError('stream ended before completion')


async def foreground_guard(client):
    while True:
        await asyncio.sleep(POLL)
        if not await available(client):
            return


async def run_batch(client, requests, health, save, probe_model='shadow_train', evaluation_checkpoint=None):
    tasks = [asyncio.create_task(asyncio.wait_for(generate(client, row, probe_model, evaluation_checkpoint),
                                                 timeout=300 if row['max_tokens']>1024 else 150))
             for _, _, row in requests]
    pending = set(tasks)
    positions = dict(zip(tasks, requests))
    guard = asyncio.create_task(foreground_guard(health))
    try:
        while pending:
            done, _ = await asyncio.wait(pending | {guard}, return_when=asyncio.FIRST_COMPLETED)
            pending.difference_update(done)
            for task in done - {guard}:
                try:
                    save(positions[task], task.result())
                except RejectedInput:
                    save(positions[task], RejectedInput())
                except TimeoutError:
                    if evaluation_checkpoint is not None:
                        save(positions[task], {'failure':'timeout'})
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    # The cursor stays at the first unfinished row.
                    pass
            if guard in done:
                break
    finally:
        for task in (*pending, guard):
            task.cancel()
        await asyncio.gather(*tasks, guard, return_exceptions=True)


async def run(db, corpus):
    from evaluation_cycles import EvaluationCycles
    print(json.dumps(status(db)), flush=True)
    backoff = POLL
    cycles = EvaluationCycles(db, corpus.parent.parent)
    async with httpx.AsyncClient(timeout=4, trust_env=False) as health, httpx.AsyncClient(
            timeout=httpx.Timeout(130, connect=2), trust_env=False) as inference:
        with corpus.open('rb') as stream:
            while True:
                saved = db.execute('SELECT next_index,next_byte,total FROM progress WHERE id=1').fetchone()
                index = saved['next_index']
                if index >= saved['total']:
                    cycles.publish(index)
                    return
                cycle = cycles.phase(index)
                capacity, count = await available(health, include_shadow=True) or (0, 0)
                if not capacity:
                    await asyncio.sleep(POLL)
                    continue
                if cycle['phase']=='evaluation':
                    await cycles.freeze(cycle['checkpoint'], count)
                    if capacity < 3:
                        await asyncio.sleep(POLL)
                        continue
                stream.seek(saved['next_byte'])
                requests = []
                batch = burst_size(index, capacity)
                if cycle['phase']=='evaluation':
                    batch = cycles.batch_size(cycle, index, batch)
                for i in range(index, min(index + batch, cycle['end'], saved['total'])):
                    row = json.loads(stream.readline())
                    if not db.execute('SELECT 1 FROM samples WHERE request_index=?', (i,)).fetchone():
                        requests.append((i, stream.tell(), row))
                # A separate connection pool keeps health checks responsive during generation.
                def save(item, outcome):
                    i, offset, row = item
                    if isinstance(outcome, dict) and not outcome.get('failure'):
                        checkpoint(db, i, offset, row, outcome, concurrency=len(requests))
                    elif isinstance(outcome, RejectedInput) or isinstance(outcome, dict):
                        checkpoint(db, i, offset, row, None, rejected=True, concurrency=len(requests))
                if cycle['phase']=='evaluation':
                    await cycles.evaluate(cycle, index, requests, inference, health, save, run_batch,
                                          available, RejectedInput)
                else:
                    await run_batch(inference, requests, health, save)
                next_index = db.execute('SELECT next_index FROM progress WHERE id=1').fetchone()[0]
                cycles.publish(next_index)
                if next_index == index:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)
                    continue
                backoff = POLL
                if next_index // 100 > index // 100:
                    print(json.dumps(status(db)), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    root = args.workspace
    manifest = json.loads((root / 'workload/manifest.json').read_text())
    with closing(connect(root / 'central-training.sqlite3')) as db:
        initialize(db, manifest)
        if args.status:
            print(json.dumps(status(db)))
        else:
            with suppress(KeyboardInterrupt):
                asyncio.run(run(db, root / 'workload/requests.jsonl'))


if __name__ == '__main__':
    main()
