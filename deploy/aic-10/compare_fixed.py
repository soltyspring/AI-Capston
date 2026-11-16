"""Run isolated, paired GPU trials using the existing experiment endpoints."""
import argparse
import asyncio
import json
from pathlib import Path
import time

import httpx
from compare_shadow import ROOT, summary
from continuous_worker import ROUTER

TOPICS = ('a public library', 'city buses', 'a school garden', 'a science museum')
QUESTIONS = tuple(
    {'prompt': f'{instruction} about {topic}.', 'max_tokens': 2000}
    for topic in TOPICS
    for instruction in ('Give one sentence', 'List five practical recommendations',
                        'Write a detailed beginner guide with examples')
)
SHORT = tuple({'prompt': f'In one sentence, explain the purpose of {topic}.', 'max_tokens': 2000}
              for topic in TOPICS)
LONG = tuple({'prompt': f'Write an extensive 30-section manual about {topic}, with examples in every section.',
              'max_tokens': 2000} for topic in TOPICS)
REPORT = ROOT / 'shadow-comparison.json'


def publish(report):
    existing = json.loads(REPORT.read_text()) if REPORT.exists() else {}
    existing['fixed_trials'] = report
    target = REPORT.with_name('fixed-trials.next')
    target.write_text(json.dumps(existing, allow_nan=False))
    target.replace(REPORT)


def metrics(rows):
    result = summary(rows)
    observed = sum(row is not None and row['first_seconds'] is not None for row in rows)
    return {**result, 'first_samples': observed,
            'over_2s_pct': round(100 * result['first_over_2s'] / observed, 2) if observed else None}


async def trial(client, row, policy, checkpoint):
    started = time.monotonic()
    first = route = None
    body = {'messages': [{'role': 'user', 'content': row['prompt']}], 'training_probe': True,
            'training_max_tokens': row['max_tokens'], 'benchmark': True,
            'probe_model': 'production_eval' if policy == 'round_robin' else policy}
    if policy == 'round_robin':
        body['policy_override'] = 'round_robin'
    if checkpoint is not None:
        body['evaluation_checkpoint'] = checkpoint
    async with client.stream('POST', ROUTER + '/chat/stream', json=body, timeout=150) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line:
                continue
            event = json.loads(line)
            if event['event'] == 'error':
                raise RuntimeError('router rejected an experiment request')
            if event['event'] == 'routed':
                route = event
                if route.get('training_output_limit') != row['max_tokens']:
                    raise RuntimeError('unexpected generation limit')
                if policy == 'shortest_eval' and any(
                        item.get('selected') and item.get('selection_method') != 'shortest_predicted'
                        for item in route['candidates']):
                    raise RuntimeError('shortest policy fell back to rotation')
            if event['event'] == 'chunk' and first is None and any(
                    (choice.get('delta') or {}).get('content')
                    for choice in (event.get('vllm') or {}).get('choices', [])):
                first = time.monotonic() - started
            if event['event'] == 'complete' and route:
                return {'first_seconds': first, 'completion_seconds': time.monotonic() - started,
                        'queue_seconds': route['queue_seconds'], 'output_tokens': event['actual_output_tokens'],
                        'finish_reason': event.get('finish_reason'), 'raw_prediction': route['predicted_output_tokens'],
                        'server': route['server_id'], 'input_tokens': route['input_tokens'],
                        'original_prediction': route.get('raw_predicted_output_tokens', route['predicted_output_tokens'])}
    raise RuntimeError('incomplete experiment stream')


async def run(checkpoint, repeats):
    report = {'status': 'running', 'checkpoint': checkpoint, 'repeats': repeats,
              'forecast': [], 'congestion': [], 'started_at': time.time()}
    publish(report)
    async with httpx.AsyncClient(trust_env=False) as client:
        async def status():
            response = await client.get(ROUTER + '/status', timeout=5)
            response.raise_for_status()
            data = response.json()
            if sum(bool(s.get('healthy')) for s in data['servers'].values()) != 3:
                raise RuntimeError('three healthy servers required')
            if data['queue']['waiting'] or sum(data['inflight'].values()) > data['training_inflight']:
                raise RuntimeError('foreground traffic detected')
            return data

        async def arm(rows, policy, version, warm=False):
            for _ in range(60):
                snap = await status()
                if not snap['training_inflight'] and not snap['training_waiting']:
                    break
                await asyncio.sleep(.5)
            else:
                raise RuntimeError('experiment queue did not drain')
            tasks = []
            try:
                if warm:
                    tasks.extend(asyncio.create_task(trial(client, row, 'round_robin', checkpoint))
                                 for row in (*LONG, *LONG[:2]))
                    for _ in range(80):
                        snap = await status()
                        if all(value >= 1 for value in snap['inflight'].values()) and sum(snap['inflight'].values()) + snap['training_waiting'] >= 6:
                            break
                        if any(task.done() for task in tasks):
                            raise RuntimeError('long work finished before all three servers were loaded')
                        await asyncio.sleep(.25)
                    else:
                        raise RuntimeError('could not verify congestion on all three servers')
                probes = [asyncio.create_task(trial(client, row, policy, version)) for row in rows]
                tasks.extend(probes)
                while not all(task.done() for task in tasks):
                    await status()
                    for task in tasks:
                        if task.done():
                            task.result()
                    await asyncio.sleep(.4)
                return [task.result() for task in probes]
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        try:
            for i in range(repeats):
                forecasts = (('original', None), ('candidate', checkpoint))
                pair = {}
                for name, version in forecasts[::1 if i % 2 == 0 else -1]:
                    pair[name] = []
                    for offset in range(0, len(QUESTIONS), 4):
                        pair[name].extend(await arm(QUESTIONS[offset:offset+4], 'shortest_eval', version))
                matched = [(a, b) for a, b in zip(pair['original'], pair['candidate'])
                           if a['finish_reason'] == b['finish_reason'] == 'stop'
                           and a['output_tokens'] == b['output_tokens'] > 0]
                for pos, name in enumerate(('original', 'candidate')):
                    result = metrics(pair[name])
                    result['matched_mae'] = (round(sum(abs(p[pos]['raw_prediction'] - p[pos]['output_tokens'])
                                                       for p in matched) / len(matched), 2) if matched else None)
                    report['forecast'].append(dict(result, repeat=i+1, variant=name, matched=len(matched)))
                publish(report)
                policies = ('round_robin', 'shortest_eval', 'shadow_eval')
                for policy in policies[i % 3:] + policies[:i % 3]:
                    rows = await arm(SHORT, policy, checkpoint, warm=True)
                    report['congestion'].append(dict(metrics(rows), repeat=i+1, policy=policy))
                    publish(report)
            report['status'] = 'completed'
        except Exception as exc:
            report.update(status='interrupted', reason=str(exc)[:160])
            raise
        finally:
            report['finished_at'] = time.time()
            publish(report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkpoint', type=int, required=True)
    parser.add_argument('--repeats', type=int, choices=range(2, 7), default=3)
    args = parser.parse_args()
    asyncio.run(run(args.checkpoint, args.repeats))
