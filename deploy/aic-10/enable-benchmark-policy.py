#!/usr/bin/env python3
"""Safely add a loopback-only policy override for scheduled A/B measurements."""
from datetime import datetime, timezone
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import time

ROOT = Path('/home/ubuntu/round')
FILES = {
    'dispatcher.py': [
        ("async def reserve(self, input_tokens, predicted, include_details=False, on_waiting=None):",
         "async def reserve(self, input_tokens, predicted, include_details=False, on_waiting=None, policy_override=None):"),
        ("policy = state.config.get('policy', 'linucb')", "policy = policy_override or state.config.get('policy', 'linucb')"),
    ],
    'vllm_balancer.py': [
        ("    category: str | None = Field(default=None, min_length=1)\n",
         "    category: str | None = Field(default=None, min_length=1)\n"
         "    policy_override: str | None = Field(default=None, pattern='^(linucb|round_robin)$')\n"
         "    benchmark: bool = False\n"),
        ("state.dispatcher.reserve(count, predicted, include_details=True, on_waiting=waiting.set)",
         "state.dispatcher.reserve(count, predicted, include_details=True, on_waiting=waiting.set, policy_override=request.policy_override)"),
        ("candidates=candidates, policy=state.config.get('policy', 'linucb')",
         "candidates=candidates, policy=request.policy_override or state.config.get('policy', 'linucb')"),
        ("learn=state.config.get('training_enabled', True),",
         "learn=state.config.get('training_enabled', True) and not request.benchmark,"),
    ],
}


def main():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = ROOT / 'backups' / f'benchmark-policy-{stamp}'
    backup.mkdir(parents=True, exist_ok=False)
    originals = {}
    planned = {}
    for name, replacements in FILES.items():
        path = ROOT / name
        original = path.read_text(encoding='utf-8')
        updated = original
        for old, new in replacements:
            if old not in updated:
                raise RuntimeError(f'Expected source text missing: {name}: {old[:90]!r}')
            updated = updated.replace(old, new)
        originals[path] = original
        shutil.copy2(path, backup / name)
        planned[path] = updated
    for path, updated in planned.items():
        path.write_text(updated, encoding='utf-8')
    try:
        for name in FILES:
            py_compile.compile(str(ROOT / name), doraise=True)
        subprocess.run(['sudo', '-n', 'systemctl', 'restart', 'round-balancer.service'], check=True)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            healthy = subprocess.run(['curl', '--fail', '--silent', '--max-time', '3',
                                      'http://127.0.0.1:8008/status'],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if healthy.returncode == 0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError('router status did not recover within 30 seconds')
        print(f'benchmark policy route ready; backup={backup}')
    except Exception:
        for path, text in originals.items():
            path.write_text(text, encoding='utf-8')
        subprocess.run(['sudo', '-n', 'systemctl', 'restart', 'round-balancer.service'], check=False)
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'benchmark route installation failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1)
