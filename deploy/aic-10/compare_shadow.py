"""Paired actual-GPU queue experiment; run with the training service paused."""
import asyncio
import argparse
import json
import math
from pathlib import Path
import statistics
import time

import httpx
from continuous_worker import CHECKPOINT_INTERVAL, ROUTER, generate

ROOT = Path.home() / '.local/share/qless-training'
POLICIES = ('production_eval', 'shadow_eval', 'shortest_eval', 'work_eval')
SCENARIOS = ('burst19', 'burst21', 'mixed')
TOPICS = ('library', 'school garden', 'public transit', 'museum', 'weather station',
          'recycling center', 'community theater', 'sports club', 'science fair')


def write(report):
    temp = ROOT / 'shadow-comparison.next'
    temp.write_text(json.dumps(report, allow_nan=False), encoding='utf-8')
    temp.replace(ROOT / 'shadow-comparison.json')


def summary(rows):
    def p95(values):
        return round(sorted(values)[math.ceil(len(values)*.95)-1], 3) if values else None
    valid = [row for row in rows if row]
    generated = [x for x in valid if x.get('decode_seconds') is not None and x['output_tokens']>1]
    error = [x for x in valid if x.get('finish_reason')=='stop' and x.get('raw_prediction') is not None]
    true_total = sum(x['output_tokens'] for x in error)
    return {'completed': len(valid), 'attempted': len(rows),
            'ttft_p95': p95([x['first_seconds'] for x in valid if x['first_seconds'] is not None]),
            'first_over_2s': sum(x['first_seconds']>2 for x in valid if x['first_seconds'] is not None),
            'completion_p95': p95([x['completion_seconds'] for x in valid]),
            'completion_mean': round(statistics.mean(x['completion_seconds'] for x in valid), 3) if valid else None,
            'router_wait_p95': p95([x['queue_seconds'] for x in valid]),
            'mean_output_tokens': round(statistics.mean(x['output_tokens'] for x in valid), 1) if valid else None,
            'decode_ms_per_token':round(1000*sum(x['decode_seconds'] for x in generated)/sum(x['output_tokens']-1 for x in generated),3) if generated else None,
            'natural_count':len(error),
            'token_wape':round(100*sum(abs(x['raw_prediction']-x['output_tokens']) for x in error)/true_total,3) if true_total else None,
            'fixed_wape':round(100*sum(abs(400-x['output_tokens']) for x in error)/true_total,3) if true_total else None}


def workloads_for(scenario, repetition):
    n = 19 if scenario=='burst19' else 21
    for i in range(n):
        topic = TOPICS[(i+repetition) % len(TOPICS)]
        short = (scenario=='mixed' and i>=12) or (scenario!='mixed' and i%3==0)
        task = (f'In one sentence, give a reason to visit a {topic}.' if short else
                f'Write a detailed handbook for a {topic}. Include at least 20 numbered recommendations with examples.')
        # Distinct references across rounds, identical inside each paired policy round.
        notes = ' '.join(f'Reference {j}: review staffing, accessibility and public feedback.' for j in range(24 if not short and i%2==0 else 0))
        delay = .6+.23*(i-12) if scenario=='mixed' and short else 0
        yield {'prompt':f'QLess holdout {scenario}/{repetition}/{i}. {task} {notes}',
               'max_tokens':96 if short else 640, 'delay':delay}


def savings(saved, comparison):
    matches = []
    for (scenario, policy), rows in saved.items():
        if policy!=comparison:
            continue
        baseline = saved.get((scenario,'production_eval'), [])
        matches.extend((a,b) for a,b in zip(baseline,rows) if a and b and a['output_tokens']==b['output_tokens']
                       and a.get('slot_seconds') is not None and b.get('slot_seconds') is not None)
    return {'policy':comparison,'matched':len(matches),
            'total_seconds_saved':round(sum(a['completion_seconds']-b['completion_seconds'] for a,b in matches),3) if matches else None,
            'slot_seconds_saved':round(sum(a['slot_seconds']-b['slot_seconds'] for a,b in matches),3) if matches else None}


async def main(checkpoint=None):
    history_path = ROOT / 'shadow-comparison-history.json'
    history = json.loads(history_path.read_text()) if history_path.exists() else []
    report = {'version':2,'status':'running','started_at':time.time(),'rounds':[], 'results':[],
              'checkpoint':checkpoint,'trend':history, 'planned_rounds':len(POLICIES)*len(SCENARIOS)*2}
    write(report)
    saved = {}
    try:
        async with httpx.AsyncClient(timeout=120, trust_env=False) as client, httpx.AsyncClient(timeout=4, trust_env=False) as watcher:
            async def status():
                response = await watcher.get(ROUTER+'/status')
                response.raise_for_status()
                return response.json()
            initial = await status()
            if not initial.get('workload_experiments') or sum(initial.get('shadow_linucb', {}).values()) < 30:
                raise RuntimeError('not_enough_training')
            report['training_samples'] = initial['shadow_linucb']
            for scenario in SCENARIOS:
                for repetition in range(2):
                    workloads = list(workloads_for(scenario,repetition))
                    n = len(workloads)
                    order = POLICIES if repetition == 0 else POLICIES[::-1]
                    for policy in order:
                        previous = await status()
                        if len([s for s in previous['servers'].values() if s.get('healthy')]) != 3 or any(previous['inflight'].values()) or previous['queue']['waiting'] or previous['training_waiting']:
                            raise RuntimeError('servers_not_idle')
                        current = {'policy':policy,'load':n,'scenario':scenario,'repetition':repetition+1,'completed':0,
                                   'attempted':0,'peak_router_wait':0,'peak_engine_queues':0}
                        report['current'] = current
                        write(report)
                        async def submit(row):
                            await asyncio.sleep(row['delay'])
                            return await asyncio.wait_for(generate(client,row,policy),timeout=120)
                        tasks = {asyncio.create_task(submit(row)):i for i,row in enumerate(workloads)}
                        pending = set(tasks)
                        finished = [None]*n
                        done_count = 0
                        try:
                            while pending:
                                done,pending = await asyncio.wait(pending, timeout=.45)
                                for task in done:
                                    try:
                                        finished[tasks[task]] = task.result()
                                    except (httpx.HTTPError,ValueError,KeyError,TimeoutError):
                                        pass
                                    done_count += 1
                                snapshot = await status()
                                if (snapshot['queue']['waiting'] or sum(snapshot['inflight'].values()) > snapshot['training_inflight']
                                    or not all(s.get('healthy') for s in snapshot['servers'].values())):
                                    raise RuntimeError('foreground_or_unhealthy')
                                current.update(summary(finished))
                                current['attempted'] = done_count
                                current['peak_router_wait'] = max(current['peak_router_wait'], snapshot['training_waiting'])
                                current['peak_engine_queues'] = max(current['peak_engine_queues'], sum(
                                    (x.get('vllm') or {}).get('num_requests_waiting',0)>0 for x in snapshot['servers'].values()))
                                report['recent'] = [dict(request_index=i,source='comparison',policy=policy,
                                    server=x['server'],input_tokens=x['input_tokens'],predicted_tokens=x['predicted_tokens'],
                                    output_tokens=x['output_tokens'],queue_seconds=x['queue_seconds'],
                                    first_seconds=x['first_seconds'],completion_seconds=x['completion_seconds'],outcome='complete')
                                    for i,x in enumerate(finished) if x][-12:]
                                write(report)
                        finally:
                            for task in pending:
                                task.cancel()
                            await asyncio.gather(*pending, return_exceptions=True)
                        saved.setdefault((scenario,policy),[]).extend(finished)
                        report['rounds'].append(dict(current))
                        write(report)
                        await asyncio.sleep(3)
            report['results'] = [dict(summary(rows),scenario=scenario,load=19 if scenario=='burst19' else 21,policy=policy)
                                 for (scenario,policy),rows in saved.items()]
            report['savings'] = [savings(saved,policy) for policy in POLICIES[1:]]
            report['samples'] = {f'{scenario}:{policy}': [
                {k:v for k,v in row.items() if k!='candidates'} if row else None for row in rows]
                for (scenario,policy),rows in saved.items()}
            report['status'] = 'completed'
    except Exception as exc:
        report['status'] = 'interrupted'
        report['reason'] = str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__
        raise
    finally:
        report.pop('current',None)
        report['finished_at'] = time.time()
        (ROOT / f"shadow-comparison-{int(report['started_at'])}.json").write_text(json.dumps(report),encoding='utf-8')
        report.pop('samples',None)
        if report['status']=='completed':
            point = {k:report[k] for k in ('started_at','checkpoint','training_samples','results','savings')}
            point['results'] = [
                {k:row.get(k) for k in ('scenario','policy','token_wape','fixed_wape','ttft_p95')}
                for row in report['results'] if row['scenario'] == 'mixed']
            if checkpoint is not None:
                history = [row for row in history if row.get('checkpoint') != checkpoint]
            history.append(point)
            report['trend'] = history
            staging = history_path.with_suffix('.next')
            staging.write_text(json.dumps(history))
            staging.replace(history_path)
        write(report)
        print(json.dumps(report),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkpoint', type=int)
    args = parser.parse_args()
    if args.checkpoint is not None and (args.checkpoint <= 0 or args.checkpoint % CHECKPOINT_INTERVAL):
        parser.error('--checkpoint must be a positive multiple of 1000')
    asyncio.run(main(args.checkpoint))
