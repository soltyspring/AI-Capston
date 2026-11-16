"""Paired real-GPU comparison of the existing policies and the v4 models."""
import argparse
import asyncio
import json
import math
import random
import time
from pathlib import Path

import httpx
from compare_fixed import LONG, REPORT, ROOT, SHORT
from continuous_worker import ROUTER
from length_forecast import estimate, load_candidate

POLICIES = ('round_robin', 'shadow_eval', 'v4_eval', 'v4_sched')


def p95(values):
    values = sorted(v for v in values if v is not None)
    return round(values[math.ceil(.95 * len(values)) - 1], 3) if values else None


async def trial(client, prompt, policy, checkpoint):
    started = time.monotonic()
    first = route = None
    body = {'messages': [{'role': 'user', 'content': prompt}], 'training_probe': True,
            'training_max_tokens': 2000, 'benchmark': True,
            'probe_model': 'production_eval' if policy == 'round_robin' else policy}
    if policy == 'round_robin':
        body['policy_override'] = 'round_robin'
    if policy == 'shadow_eval':
        body['evaluation_checkpoint'] = checkpoint
    async with client.stream('POST', ROUTER + '/chat/stream', json=body, timeout=240) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line:
                continue
            event = json.loads(line)
            if event['event'] == 'error':
                raise RuntimeError(f"router error {event.get('status')} {event.get('detail')}")
            if event['event'] == 'routed':
                route = event
            if event['event'] == 'chunk' and first is None and any(
                    (c.get('delta') or {}).get('content') for c in (event.get('vllm') or {}).get('choices', [])):
                first = time.monotonic() - started
            if event['event'] == 'complete' and route:
                return {'first': first, 'done': time.monotonic() - started, 'wait': route['queue_seconds'],
                        'server': route['server_id'], 'input_tokens': route['input_tokens'],
                        'predicted': route['predicted_output_tokens'],
                        'original': route.get('raw_predicted_output_tokens', route['predicted_output_tokens']),
                        'output': event['actual_output_tokens'], 'finish': event.get('finish_reason')}
    raise RuntimeError('incomplete stream')


def summary(rows, started, ended):
    first = [r['first'] for r in rows]
    return {'requests': len(rows), 'first_p95': p95(first),
            'over_2s_pct': round(100 * sum(f is not None and f > 2 for f in first) / len(rows), 1),
            'completion_p95': p95([r['done'] for r in rows]),
            'completion_mean': round(sum(r['done'] for r in rows) / len(rows), 2),
            'wait_p95': p95([r['wait'] for r in rows]), 'makespan': round(ended - started, 1)}


async def main(checkpoint, repeats, stream_size):
    report = {'status': 'running', 'started_at': time.time(), 'checkpoint': checkpoint, 'policies': POLICIES,
              'burst': [], 'stream': [], 'length': None}
    offline = Path.home()/'.local/share/qless-training/models-v4/offline-report.json'
    report['offline'] = json.loads(offline.read_text(encoding='utf-8'))

    def save():
        data = json.loads(REPORT.read_text()) if REPORT.exists() else {}
        data['v4_trials'] = report
        tmp = REPORT.with_name('v4-trials.next')
        tmp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        tmp.replace(REPORT)

    # Future corpus requests are unseen by every model trained so far.
    corpus = []
    with (ROOT/'workload/requests.jsonl').open(encoding='utf-8') as stream:
        for i, line in enumerate(stream):
            if i >= 60000 + stream_size:
                break
            if i >= 60000:
                corpus.append(json.loads(line)['prompt'])
    gaps = [random.Random(7).expovariate(1.0) for _ in corpus]
    v3 = load_candidate(ROOT/'cycles'/str(checkpoint)/'predictor.json')
    save()
    async with httpx.AsyncClient(trust_env=False) as client:
        async def idle():
            for _ in range(240):
                d = (await client.get(ROUTER + '/status', timeout=5)).json()
                if not d.get('v4_policies'):
                    raise RuntimeError('router lacks v4 policies')
                if sum(bool(s.get('healthy')) for s in d['servers'].values()) != 3:
                    raise RuntimeError('three healthy servers required')
                if not any(d['inflight'].values()) and not d['queue']['waiting'] and not d['training_waiting']:
                    return
                await asyncio.sleep(.5)
            raise RuntimeError('router did not drain')

        async def burst(policy):
            await idle()
            started = time.monotonic()
            longs = [asyncio.create_task(trial(client, LONG[i % len(LONG)]['prompt'], policy, checkpoint)) for i in range(18)]
            await asyncio.sleep(3)
            d = (await client.get(ROUTER + '/status', timeout=5)).json()
            queued = {'router': d['training_waiting'],
                      'engine': sum(int((s.get('vllm') or {}).get('num_requests_waiting') or 0) for s in d['servers'].values())}
            shorts = [asyncio.create_task(trial(client, p['prompt'], policy, checkpoint)) for p in SHORT]
            short_rows = await asyncio.gather(*shorts)
            long_rows = await asyncio.gather(*longs)
            ended = time.monotonic()
            return {'short': summary(short_rows, started, ended), 'long': summary(long_rows, started, ended),
                    'queued_at_short': queued}

        async def mixed(policy):
            await idle()
            started = time.monotonic()
            tasks = []
            for prompt, gap in zip(corpus, gaps):
                tasks.append(asyncio.create_task(trial(client, prompt, policy, checkpoint)))
                await asyncio.sleep(gap)
            rows = await asyncio.gather(*tasks)
            return rows, summary(rows, started, time.monotonic())

        try:
            for repeat in range(repeats):
                order = POLICIES if repeat % 2 == 0 else POLICIES[::-1]
                for policy in order:
                    report['current'] = f'burst {repeat + 1} {policy}'
                    save()
                    report['burst'].append({'repeat': repeat + 1, 'policy': policy, **await burst(policy)})
                    save()
                for policy in order:
                    report['current'] = f'stream {repeat + 1} {policy}'
                    save()
                    rows, result = await mixed(policy)
                    report['stream'].append({'repeat': repeat + 1, 'policy': policy, **result})
                    if policy == 'v4_eval' and report['length'] is None:
                        natural = [(p, r) for p, r in zip(corpus, rows) if r['finish'] == 'stop' and r['output'] > 0]
                        def mae(values):
                            return round(sum(abs(v - r['output']) for v, (_, r) in zip(values, natural)) / len(natural), 1)
                        report['length'] = {
                            'natural': len(natural), 'actual_mean': round(sum(r['output'] for _, r in natural) / len(natural), 1),
                            'original': mae([min(r['original'], 2000) for _, r in natural]),
                            'keywords_v3': mae([min(estimate(v3, p, r['input_tokens'], r['original']), 2000) for p, r in natural]),
                            'text_v4': mae([r['predicted'] for _, r in natural])}
                    save()
            report['status'] = 'completed'
        except Exception as exc:
            report.update(status='interrupted', reason=str(exc)[:200])
            raise
        finally:
            report.pop('current', None)
            report['finished_at'] = time.time()
            save()


if __name__ == '__main__':
    cli = argparse.ArgumentParser()
    cli.add_argument('--checkpoint', type=int, required=True)
    cli.add_argument('--repeats', type=int, default=2)
    cli.add_argument('--stream-size', type=int, default=60)
    args = cli.parse_args()
    asyncio.run(main(args.checkpoint, args.repeats, args.stream_size))
