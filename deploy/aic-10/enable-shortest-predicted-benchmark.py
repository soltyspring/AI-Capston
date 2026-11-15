#!/usr/bin/env python3
"""Extend the private vLLM router installed by enable-benchmark-policy.py."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen
import json
import shutil
import subprocess
import time

ROOT = Path('/home/ubuntu/round')
POLICIES = {'linucb', 'round_robin', 'shortest_predicted'}
STATUS = 'http://127.0.0.1:8008/status'


def router_status():
    with urlopen(STATUS, timeout=3) as response:
        return json.load(response)


def patch_once(source, before, after):
    if source.count(before) != 1:
        raise RuntimeError(f'Unexpected router source: {before[:90]!r}')
    return source.replace(before, after, 1)


def updated_sources(dispatcher, balancer):
    if "elif policy == 'shortest_predicted':" not in dispatcher:
        raise RuntimeError('The router does not implement shortest_predicted')
    dispatcher = patch_once(dispatcher, '        self.next_server = 0\n',
                            '        self.next_server = 0\n        self.benchmark_cursors = {}\n')
    dispatcher = patch_once(dispatcher, 'on_waiting=None, policy_override=None):',
                            'on_waiting=None, policy_override=None, benchmark=False):')
    dispatcher = patch_once(dispatcher,
        "                        if policy == 'round_robin' or (policy == 'shortest_predicted' and any(item[3] is None for item in eligible)):\n",
        "                        fallback = policy == 'shortest_predicted' and any(item[3] is None for item in eligible)\n"
        "                        if policy == 'round_robin' or fallback:\n")
    dispatcher = patch_once(dispatcher, '                            for offset in range(len(ordered)):\n'
        '                                index = (self.next_server + offset) % len(ordered)\n',
        '                            cursor = self.benchmark_cursors.get(policy, 0) if benchmark else self.next_server\n'
        '                            for offset in range(len(ordered)):\n'
        '                                index = (cursor + offset) % len(ordered)\n')
    dispatcher = patch_once(dispatcher,
        '                                    self.next_server = (index + 1) % len(ordered)\n',
        '                                    next_index = (index + 1) % len(ordered)\n'
        '                                    if benchmark:\n'
        '                                        self.benchmark_cursors[policy] = next_index\n'
        '                                    else:\n'
        '                                        self.next_server = next_index\n')
    dispatcher = patch_once(dispatcher, "                        state.active[server['id']] += 1\n",
        "                        decision_at = time.time()\n"
        "                        for row in details:\n"
        "                            row.update(decision_at=decision_at, selected=row['server_id'] == server['id'])\n"
        "                            if row['selected']:\n"
        "                                row['selection_method'] = 'missing_estimate_round_robin' if fallback else policy\n"
        "                        state.active[server['id']] += 1\n")
    balancer = patch_once(balancer, "pattern='^(linucb|round_robin)$'",
                          "pattern='^(linucb|round_robin|shortest_predicted)$'")
    balancer = patch_once(balancer, "                'training_enabled': state.config.get('training_enabled', True),\n",
        "                'training_enabled': state.config.get('training_enabled', True),\n"
        "                'benchmark_policies': ['linucb', 'round_robin', 'shortest_predicted'],\n")
    balancer = patch_once(balancer, 'on_waiting=waiting.set, policy_override=request.policy_override))',
        'on_waiting=waiting.set, policy_override=request.policy_override, benchmark=request.benchmark))')
    return dispatcher, balancer


def main():
    previous = router_status()
    if POLICIES.issubset(previous.get('benchmark_policies', [])):
        print('three router benchmark policies already available')
        return
    if any(previous['inflight'].values()) or previous['queue']['waiting']:
        raise RuntimeError('Wait for pending router requests before installing')
    paths = [ROOT / name for name in ('dispatcher.py', 'vllm_balancer.py')]
    updated = updated_sources(*(path.read_text(encoding='utf-8') for path in paths))
    for path, source in zip(paths, updated):
        compile(source, str(path), 'exec')
    backup = ROOT / 'backups' / datetime.now(timezone.utc).strftime('three-policy-%Y%m%dT%H%M%SZ')
    backup.mkdir(parents=True, mode=0o700)
    for path in paths:
        shutil.copy2(path, backup / path.name)
    try:
        for path, source in zip(paths, updated):
            staged = path.with_suffix('.benchmark.next')
            staged.write_text(source, encoding='utf-8')
            staged.chmod(path.stat().st_mode & 0o777)
            staged.replace(path)
        subprocess.run(['sudo', '-n', 'systemctl', 'restart', 'round-balancer.service'], check=True)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                status = router_status()
                if POLICIES.issubset(status.get('benchmark_policies', [])) and status['policy'] == previous['policy']:
                    print(f'three router benchmark policies available; backup={backup}')
                    return
            except (OSError, ValueError):
                pass
            time.sleep(.5)
        raise RuntimeError('Router failed the deployment health check')
    except Exception:
        for path in paths:
            shutil.copy2(backup / path.name, path)
        subprocess.run(['sudo', '-n', 'systemctl', 'restart', 'round-balancer.service'], check=True)
        raise


if __name__ == '__main__':
    main()
