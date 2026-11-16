"""Compare frozen routing policies under measured queue pressure."""
import argparse
import asyncio
import json
import time

import httpx
from compare_fixed import LONG, REPORT, SHORT, metrics, trial
from continuous_worker import ROUTER

POLICIES = ('round_robin', 'shortest_eval', 'shadow_eval')


async def run(checkpoint):
    report = dict(status='running', checkpoint=checkpoint, ramp=[], results=[], started_at=time.time())

    def save():
        data = json.loads(REPORT.read_text()) if REPORT.exists() else {}
        data['queued_trials'] = report
        tmp = REPORT.with_name('queued-trials.next')
        tmp.write_text(json.dumps(data, allow_nan=False))
        tmp.replace(REPORT)

    save()
    async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
        async def status():
            response = await client.get(ROUTER + '/status')
            response.raise_for_status()
            data = response.json()
            if len(data['servers']) != 3 or not all(s['healthy'] for s in data['servers'].values()):
                raise RuntimeError('three healthy servers required')
            if data['queue']['waiting'] or sum(data['inflight'].values()) > data['training_inflight']:
                raise RuntimeError('foreground traffic detected')
            return data

        async def arm(load, policy=None, require_engine=False):
            for _ in range(80):
                d = await status()
                if not d['training_inflight'] and not d['training_waiting']:
                    break
                await asyncio.sleep(.25)
            else:
                raise RuntimeError('queue did not drain')
            tasks = []
            try:
                started = time.time()
                for i in range(load):
                    tasks.append(asyncio.create_task(trial(client, LONG[i % len(LONG)], 'round_robin', checkpoint)))
                evidence = None
                peak = {}
                for _ in range(32):
                    d = await status()
                    if any(task.done() for task in tasks):
                        break
                    queue = {k: int((v.get('vllm') or {}).get('num_requests_waiting', 0))
                             for k,v in d['servers'].items()}
                    for k,v in queue.items():
                        peak[k] = max(peak.get(k, 0), v)
                    snapshot = dict(engine=queue, inflight=d['inflight'], router=d['training_waiting'])
                    fresh = all(v.get('checked_at', 0) >= started for v in d['servers'].values())
                    if fresh and all(queue.values()) and min(d['inflight'].values()) >= load // 3:
                        evidence = dict(snapshot, kind='engine')
                        break
                    if min(d['inflight'].values()) >= 6 and d['training_waiting'] > 0:
                        evidence = dict(snapshot, kind='router')
                        if policy and not require_engine:
                            break
                    await asyncio.sleep(.25)
                if policy is None:
                    return dict(load=load, evidence=evidence, peak=peak)
                if not evidence or (require_engine and evidence['kind'] != 'engine'):
                    raise RuntimeError('required queue was not reproduced')
                probes = [asyncio.create_task(trial(client, row, policy, checkpoint)) for row in SHORT]
                tasks.extend(probes)
                while not all(t.done() for t in probes):
                    await status()
                    for task in tasks:
                        if task.done():
                            task.result()
                    await asyncio.sleep(.2)
                return dict(metrics([task.result() for task in probes]), load=load, evidence=evidence)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        try:
            selected = None
            for load in (6, 12, 18, 21):
                row = await arm(load)
                report['ramp'].append(row)
                save()
                if row['evidence']:
                    selected = row
                if selected and selected['evidence']['kind'] == 'engine':
                    break
            if not selected:
                raise RuntimeError('no verified queue within experiment limit')
            report['kind'] = selected['evidence']['kind']
            for repeat in range(3):
                for policy in POLICIES[repeat:] + POLICIES[:repeat]:
                    row = await arm(selected['load'], policy, report['kind'] == 'engine')
                    report['results'].append(dict(row, policy=policy, repeat=repeat+1))
                    save()
            report['status'] = 'completed'
        except Exception as exc:
            report.update(status='interrupted', reason=str(exc)[:160])
            raise
        finally:
            report['finished_at'] = time.time()
            save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkpoint', type=int, required=True)
    asyncio.run(run(parser.parse_args().checkpoint))
