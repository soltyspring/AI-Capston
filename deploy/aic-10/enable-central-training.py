#!/usr/bin/env python3
"""Add low-priority probes to the existing private router stream."""
from datetime import datetime, timezone
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path('/home/ubuntu/round')
spec = spec_from_file_location('patch_helpers', ROOT / 'enable-shortest-predicted-benchmark.py')
helpers = module_from_spec(spec)
spec.loader.exec_module(helpers)
replace = helpers.patch_once


def updated_sources(dispatcher, balancer):
    dispatcher = replace(dispatcher, 'policy_override=None, benchmark=False):',
                         'policy_override=None, benchmark=False, training_probe=False):')
    dispatcher = replace(dispatcher, '        async with self.condition:\n',
        '        async with self.condition:\n'
        '            if training_probe and (self.waiters or any(\n'
        "                    not work.get('training_probe') for jobs in state.active_work.values() for work in jobs.values())):\n"
        "                raise HTTPException(409, 'Foreground traffic has priority')\n")
    dispatcher = replace(dispatcher, "                            can_send = state.active[sid] < server.get('max_inflight', 1)\n",
        "                            can_send = state.active[sid] < server.get('max_inflight', 1)\n"
        "                            if training_probe:\n"
        "                                can_send = can_send and state.active[sid] == 0\n")
    dispatcher = replace(dispatcher, "'predicted_output_tokens': predicted, 'estimated_service_seconds': own}",
                         "'predicted_output_tokens': predicted, 'estimated_service_seconds': own, 'training_probe': training_probe}")
    dispatcher = replace(dispatcher, '                    remaining = deadline - time.monotonic()\n',
        "                    if training_probe:\n"
        "                        raise HTTPException(409, 'No foreground-free probe slot')\n"
        '                    remaining = deadline - time.monotonic()\n')
    balancer = replace(balancer, '    benchmark: bool = False\n',
        '    benchmark: bool = False\n    training_probe: bool = False\n'
        '    training_max_tokens: int = Field(default=128, ge=1, le=320)\n')
    balancer = replace(balancer, "                'servers': state.status, 'inflight': state.active,\n",
        "                'servers': state.status, 'inflight': state.active,\n"
        "                'training_probe_supported': True,\n"
        "                'training_inflight': sum(bool(w.get('training_probe')) for works in state.active_work.values() for w in works.values()),\n")
    anchor = '    state, accepted = app.state, time.monotonic()\n    count, category, classification, predicted, max_output = await prepare(request)\n'
    balancer = replace(balancer, anchor, anchor +
        '    if request.training_probe:\n'
        '        max_output = min(max_output, request.training_max_tokens)\n'
        '        predicted = min(predicted, max_output)\n')
    balancer = replace(balancer, 'benchmark=request.benchmark))',
                       'benchmark=request.benchmark, training_probe=request.training_probe))')
    balancer = replace(balancer, '                    if not cancelled:\n',
                       '                    if not cancelled and not request.training_probe:\n')
    return dispatcher, balancer


def burst_sources(dispatcher, balancer):
    dispatcher = replace(dispatcher, '        self.waiters = deque()\n',
                         '        self.waiters = deque()\n        self.training_waiters = deque()\n')
    start = dispatcher.index('        async with self.condition:\n')
    end = dispatcher.index('            self.waiters.append(marker)', start)
    dispatcher = dispatcher[:start] + (
        '        def foreground_busy():\n'
        "            return self.waiters or any(not work.get('training_probe')\n"
        '                for jobs in state.active_work.values() for work in jobs.values())\n'
        '        queue = self.training_waiters if training_probe else self.waiters\n'
        '        async with self.condition:\n'
        '            if training_probe and foreground_busy():\n'
        "                raise HTTPException(409, 'Foreground traffic has priority')\n"
        '            capacity = 21 if training_probe else state.config.get(\'queue_max_size\', 100)\n'
        '            if len(queue) >= capacity:\n'
        '                if not training_probe:\n'
        '                    self.rejected += 1\n'
        "                raise HTTPException(409 if training_probe else 503, 'Request queue is full')\n"
    ) + dispatcher[end:]
    dispatcher = replace(dispatcher, '            self.waiters.append(marker)\n',
        '            queue.append(marker)\n'
        '            if not training_probe:\n'
        '                self.condition.notify_all()\n')
    dispatcher = replace(dispatcher, '                    if self.waiters[0] is marker:\n',
        '                    if training_probe and foreground_busy():\n'
        "                        raise HTTPException(409, 'Foreground traffic has priority')\n"
        '                    if queue[0] is marker:\n')
    dispatcher = replace(dispatcher, "                            if training_probe:\n"
        "                                can_send = can_send and state.active[sid] == 0\n", '')
    dispatcher = replace(dispatcher, '                        self.waiters.popleft()\n',
                         '                        queue.popleft()\n')
    dispatcher = replace(dispatcher, "                    if training_probe:\n"
        "                        raise HTTPException(409, 'No foreground-free probe slot')\n", '')
    for anchor in ('                    if remaining <= 0:\n', '                    except asyncio.TimeoutError:\n'):
        dispatcher = replace(dispatcher, anchor + '                        self.timed_out += 1\n'
            "                        raise HTTPException(503, 'Timed out waiting for a healthy server')\n",
            anchor + '                        if not training_probe:\n'
            '                            self.timed_out += 1\n'
            "                        raise HTTPException(409 if training_probe else 503, 'Timed out waiting for a healthy server')\n")
    dispatcher = replace(dispatcher, '                if marker in self.waiters:\n'
        '                    self.waiters.remove(marker)\n',
        '                if marker in queue:\n'
        '                    queue.remove(marker)\n')
    balancer = replace(balancer, "                'training_probe_supported': True,\n",
        "                'training_probe_supported': True,\n"
        "                'training_burst_supported': True,\n"
        "                'training_waiting': len(state.dispatcher.training_waiters),\n")
    return dispatcher, balancer


def shadow_sources(dispatcher, balancer):
    dispatcher = replace(dispatcher, 'benchmark=False, training_probe=False):',
                        'benchmark=False, training_probe=False, shadow=False):')
    dispatcher = replace(dispatcher, 'score = state.bandit.score(sid, x) if can_send else None',
                        'score = (state.shadow_bandit if shadow else state.bandit).score(sid, x) if can_send else None')
    balancer = replace(balancer, '    training_probe: bool = False\n',
        "    training_probe: bool = False\n    probe_model: str = Field(default='production_eval', pattern='^(production_eval|shadow_train|shadow_eval)$')\n")
    balancer = replace(balancer, "    state.lock = asyncio.Lock()\n",
        "    state.shadow_bandit = LinUCB('/home/ubuntu/.local/share/qless-training/shadow-linucb.sqlite3', alpha=state.bandit.alpha)\n"
        "    state.lock = asyncio.Lock()\n")
    balancer = replace(balancer, '        state.bandit.db.close()\n',
        '        state.bandit.db.close()\n        state.shadow_bandit.db.close()\n')
    balancer = replace(balancer, "                'training_burst_supported': True,\n",
        "                'training_burst_supported': True,\n"
        "                'shadow_linucb': {s['id']: state.shadow_bandit.state(s['id'])[2] for s in state.config['servers']},\n")
    balancer = replace(balancer, 'training_probe=request.training_probe))',
        "training_probe=request.training_probe, shadow=request.training_probe and request.probe_model != 'production_eval'))")
    balancer = replace(balancer, "policy=request.policy_override or state.config.get('policy', 'linucb'))",
        "policy='shadow_linucb' if request.training_probe and request.probe_model != 'production_eval' else request.policy_override or state.config.get('policy', 'linucb'))")
    balancer = replace(balancer, '                    if not cancelled and not request.training_probe:\n',
        "                    if not cancelled and request.training_probe and request.probe_model == 'shadow_train':\n"
        "                        state.shadow_bandit.update(sid, x, time.monotonic() - started + queue_seconds, success,\n"
        "                            count, category, predicted, actual, prompt_count=prompt, queue_seconds=queue_seconds,\n"
        "                            ttft_seconds=first - accepted if first else None)\n"
        "                    if not cancelled and not request.training_probe:\n")
    return dispatcher, balancer


def report_sources(balancer):
    anchor = "                'shadow_linucb':"
    if balancer.count(anchor) != 1:
        raise RuntimeError('Unexpected shadow status anchor')
    balancer = balancer.replace(anchor, "                'shadow_comparison': shadow_report(),\n" + anchor, 1)
    return replace(balancer, "@app.get('/status')\n",
        "def shadow_report():\n"
        "    try:\n"
        "        with open('/home/ubuntu/.local/share/qless-training/shadow-comparison.json') as report:\n"
        "            return json.load(report)\n"
        "    except (OSError, ValueError):\n"
        "        return None\n\n\n"
        "@app.get('/status')\n")


def workload_sources(dispatcher, balancer):
    dispatcher = replace(dispatcher, 'training_probe=False, shadow=False):',
                        'training_probe=False, shadow=False, work_aware=False):')
    anchor = "                        if policy == 'linucb' and state.config.get('balance_inflight', False):\n"
    dispatcher = replace(dispatcher, anchor,
        "                        known = sorted((item for item in eligible if item[3] is not None), key=lambda x: (x[3], x[1]['id']))\n"
        "                        if work_aware and known:\n"
        "                            fastest = known[0][3]\n"
        "                            eligible = [item for item in known[:2] if item[3] <= fastest + max(.25, fastest * .20)]\n"
        "                            preferred = {item[1]['id'] for item in eligible}\n"
        "                            for row in details:\n"
        "                                row['balanced_preferred'] = row['server_id'] in preferred\n"
        "                        elif policy == 'linucb' and state.config.get('balance_inflight', False):\n")
    balancer = replace(balancer, 'import math\n', 'import math\nimport sqlite3\nfrom functools import lru_cache\n')
    balancer = replace(balancer, "pattern='^(production_eval|shadow_train|shadow_eval)$'",
                       "pattern='^(production_eval|shadow_train|shadow_eval|work_eval|shortest_eval)$'")
    balancer = replace(balancer, 'le=320)', 'le=1024)')
    balancer = replace(balancer, "@app.get('/status')\n",
        """@lru_cache(maxsize=1)
def recent_experiments(_bucket):
    uri = 'file:/home/ubuntu/.local/share/qless-training/central-training.sqlite3?mode=ro'
    try:
        db = sqlite3.connect(uri, uri=True, timeout=.1)
        try:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT request_index, recorded, server, policy, input_tokens, predicted_tokens, output_tokens, first_seconds, completion_seconds, queue_seconds, outcome FROM samples ORDER BY request_index DESC LIMIT 12')
            return [dict(row) for row in rows]
        finally:
            db.close()
    except sqlite3.Error:
        return []


@app.get('/status')
""")
    balancer = replace(balancer, "                'shadow_comparison': shadow_report(),\n",
        "                'shadow_comparison': shadow_report(),\n"
        "                'workload_experiments': True,\n"
        "                'recent_experiments': recent_experiments(int(time.monotonic() // 3)),\n")
    balancer = replace(balancer, '    if request.training_probe:\n        max_output',
        """    model = request.probe_model if request.training_probe else 'production_eval'
    probe_policy = 'shortest_predicted' if model == 'shortest_eval' else request.policy_override
    policy_label = {
        'shadow_train': 'shadow_linucb', 'shadow_eval': 'shadow_linucb',
        'work_eval': 'work_linucb', 'shortest_eval': 'shortest_predicted',
    }.get(model, probe_policy or state.config.get('policy', 'linucb'))
    raw_predicted = predicted
    if request.training_probe:
        max_output""")
    balancer = replace(balancer, 'policy_override=request.policy_override, benchmark=request.benchmark, training_probe=request.training_probe, shadow=request.training_probe and request.probe_model != \'production_eval\'))',
        "policy_override=probe_policy, benchmark=request.benchmark, training_probe=request.training_probe, shadow=model in ('shadow_train','shadow_eval','work_eval'), work_aware=model == 'work_eval'))")
    balancer = replace(balancer, "policy='shadow_linucb' if request.training_probe and request.probe_model != 'production_eval' else request.policy_override or state.config.get('policy', 'linucb'))",
        'raw_predicted_output_tokens=raw_predicted, policy=policy_label)')
    balancer = replace(balancer, '        intervals = []\n', '        intervals = []\n        finish_reason = None\n')
    balancer = replace(balancer, "                    for choice in part.get('choices', []):\n",
        "                    for choice in part.get('choices', []):\n"
        "                        finish_reason = choice.get('finish_reason') or finish_reason\n")
    anchor = "                        queue_seconds=queue_seconds, ttft_seconds=first - accepted if first else None,\n"
    balancer = replace(balancer, anchor,
        anchor + "                        finish_reason=finish_reason, slot_seconds=time.monotonic() - started,\n"
        "                        last_output_seconds=last - accepted if last is not None else None,\n"
        )
    return dispatcher, balancer


def activity_sources(balancer):
    balancer = replace(balancer, 'from category import classify\n',
                       'from category import classify\nfrom gpu_activity import GpuActivity\n')
    balancer = replace(balancer, '    state.lock = asyncio.Lock()\n',
        "    state.activity = GpuActivity('/home/ubuntu/.local/share/qless-training/gpu-activity.sqlite3')\n"
        '    state.lock = asyncio.Lock()\n')
    balancer = replace(balancer, '        state.shadow_bandit.db.close()\n',
        '        state.shadow_bandit.db.close()\n        state.activity.db.close()\n')
    balancer = replace(balancer, "                'workload_experiments': True,\n",
        "                'workload_experiments': True,\n"
        "                'gpu_activity': state.activity.report(),\n")
    balancer = replace(balancer, "        response = await state.client.post(server['vllm_url'].rstrip('/') + '/v1/chat/completions', json=payload)\n",
        "        state.activity.begin()\n"
        "        try:\n"
        "            response = await state.client.post(server['vllm_url'].rstrip('/') + '/v1/chat/completions', json=payload)\n"
        "        finally:\n"
        "            state.activity.end()\n")
    start = balancer.index("            async with state.client.stream('POST', server['vllm_url']")
    end = balancer.index('            if not done or',start)
    original = balancer[start:end]
    return balancer[:start] + ('            state.activity.begin()\n            try:\n' +
        ''.join('    ' + line for line in original.splitlines(keepends=True)) +
        '            finally:\n                state.activity.end()\n') + balancer[end:]


def main():
    initial = helpers.router_status()
    if 'gpu_activity' in initial:
        print('Workload and GPU activity already enabled')
        return
    if any(initial['inflight'].values()) or initial['queue']['waiting']:
        raise RuntimeError('Wait for active router requests before deployment')
    paths = [ROOT / name for name in ('dispatcher.py', 'vllm_balancer.py')]
    sources = tuple(path.read_text(encoding='utf-8') for path in paths)
    if not initial.get('training_probe_supported'):
        sources = updated_sources(*sources)
    if not initial.get('training_burst_supported'):
        sources = burst_sources(*sources)
    if 'shadow_linucb' not in initial:
        sources = shadow_sources(*sources)
    if 'shadow_comparison' not in initial:
        sources = sources[0], report_sources(sources[1])
    if not initial.get('workload_experiments'):
        sources = workload_sources(*sources)
    updated = sources[0], activity_sources(sources[1])
    for path, source in zip(paths, updated):
        compile(source, str(path), 'exec')
    backup = ROOT / 'backups' / datetime.now(timezone.utc).strftime('central-probe-%Y%m%dT%H%M%SZ')
    backup.mkdir(parents=True, mode=0o700)
    for path in paths:
        shutil.copy2(path, backup / path.name)
    try:
        for path, source in zip(paths, updated):
            staged = path.with_suffix('.training.next')
            staged.write_text(source, encoding='utf-8')
            staged.chmod(path.stat().st_mode & 0o777)
            staged.replace(path)
        subprocess.run(['sudo', '-n', 'systemctl', 'restart', 'round-balancer.service'], check=True)
        for _ in range(30):
            try:
                status = helpers.router_status()
                if 'gpu_activity' in status and status['policy'] == initial['policy']:
                    print(f'Central probes enabled; previous source: {backup}')
                    return
            except (OSError, ValueError):
                pass
            time.sleep(1)
        raise RuntimeError('Router probe readiness check failed')
    except Exception:
        for path in paths:
            shutil.copy2(backup / path.name, path)
        subprocess.run(['sudo', '-n', 'systemctl', 'restart', 'round-balancer.service'], check=True)
        raise


if __name__ == '__main__':
    main()
