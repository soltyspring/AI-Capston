#!/usr/bin/env python3
"""Add experiment-only v4 routing policies to the private router.

Production requests keep the existing predictor and LinUCB. Only benchmark probes
with probe_model v4_eval or v4_sched use the text length model and the learned
completion-time model. v4_sched also holds work in the router once each server's
engine slots are busy, releases the shortest predicted request first, and keeps
one engine slot per server for requests predicted to be short.
"""
import argparse
from pathlib import Path


def replace(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError(f'Unexpected router source: {old[:80]!r}')
    return source.replace(old, new, 1)


def patch_dispatcher(source):
    source = replace(source, 'frozen_bandit=None):', 'frozen_bandit=None, route_model=None, schedule=False):')
    source = replace(source, '        self.benchmark_cursors = {}\n',
        '        self.benchmark_cursors = {}\n        self.priority = {}\n')
    source = replace(source, '    async def reserve(self',
        '    def head(self, queue):\n'
        '        # Experiment-only shortest-predicted-first; waiting time ages long requests forward.\n'
        '        if len(queue) < 2 or not all(m in self.priority for m in queue):\n'
        '            return queue[0]\n'
        '        now = time.monotonic()\n'
        '        return min(queue, key=lambda m: self.priority[m][1] / 70 - (now - self.priority[m][0]))\n\n'
        '    async def reserve(self')
    source = replace(source, "            capacity = 21 if training_probe else",
        "            capacity = (64 if benchmark else 21) if training_probe else")
    source = replace(source, '            queue.append(marker)\n',
        '            queue.append(marker)\n'
        '            if schedule:\n'
        '                self.priority[marker] = (time.monotonic(), predicted)\n')
    source = replace(source, '                    if queue[0] is marker:\n', '                    if self.head(queue) is marker:\n')
    source = replace(source, "                            can_send = state.active[sid] < server.get('max_inflight', 1)\n",
        "                            can_send = state.active[sid] < server.get('max_inflight', 1)\n"
        "                            if schedule:\n"
        "                                # Queue in the router, where order can change, instead of inside vLLM,\n"
        "                                # and leave one engine slot for short work.\n"
        "                                slots = server.get('engine_slots', 4)\n"
        "                                long_now = sum(w['predicted_output_tokens'] >= 600 for w in state.active_work[sid].values())\n"
        "                                can_send = can_send and state.active[sid] < slots and (predicted < 600 or long_now < slots - 1)\n")
    source = replace(source, "                        policy = policy_override or state.config.get('policy', 'linucb')\n",
        "                        policy = 'v4_route' if route_model is not None else policy_override or state.config.get('policy', 'linucb')\n")
    source = replace(source, "                        elif policy == 'shortest_predicted':\n",
        "                        elif policy == 'v4_route':\n"
        "                            def route_row(item):\n"
        "                                engine = state.status.get(item[1]['id'], {}).get('vllm', {})\n"
        "                                return [input_tokens, predicted, state.active[item[1]['id']],\n"
        "                                        engine.get('num_requests_running') or 0, engine.get('num_requests_waiting') or 0,\n"
        "                                        engine.get('kv_cache_usage_perc') or 0]\n"
        "                            times = route_model.predict([route_row(item) for item in eligible])\n"
        "                            for row in details:\n"
        "                                for item, value in zip(eligible, times):\n"
        "                                    if row['server_id'] == item[1]['id']:\n"
        "                                        row['predicted_seconds'] = round(float(value), 3)\n"
        "                            best = min(range(len(eligible)), key=lambda i: (times[i], eligible[i][1]['id']))\n"
        "                            _, server, x, _, own = eligible[best]\n"
        "                        elif policy == 'shortest_predicted':\n")
    source = replace(source, '                        queue.popleft()\n', '                        queue.remove(marker)\n')
    source = replace(source, '            finally:\n                if marker in queue:\n',
        '            finally:\n                self.priority.pop(marker, None)\n                if marker in queue:\n')
    return source


def patch_balancer(source):
    source = replace(source, "pattern='^(production_eval|shadow_train|shadow_eval|work_eval|shortest_eval)$'",
        "pattern='^(production_eval|shadow_train|shadow_eval|work_eval|shortest_eval|v4_eval|v4_sched)$'")
    source = replace(source, "@app.get('/status')\n",
        "V4_ROOT = '/home/ubuntu/.local/share/qless-training/models-v4'\n"
        "V4_LOCK = __import__('threading').Lock()\n\n\n"
        "def v4_models():\n"
        "    with V4_LOCK:\n"
        "        if getattr(app.state, 'v4', None) is None:\n"
        "            import length_v4  # noqa: F401  (needed to unpickle the length model)\n"
        "            app.state.v4 = (joblib.load(V4_ROOT + '/length_v4.joblib'), joblib.load(V4_ROOT + '/route_v1.joblib'))\n"
        "        return app.state.v4\n\n\n"
        "@app.get('/status')\n")
    source = replace(source, "                'training_burst_supported': True,\n",
        "                'training_burst_supported': True,\n                'v4_policies': True,\n")
    source = replace(source, "        'work_eval': 'work_linucb', 'shortest_eval': 'shortest_predicted',\n",
        "        'work_eval': 'work_linucb', 'shortest_eval': 'shortest_predicted',\n"
        "        'v4_eval': 'v4_route', 'v4_sched': 'v4_sched',\n")
    source = replace(source, '    raw_predicted = predicted\n',
        '    raw_predicted = predicted\n'
        '    route_model = None\n'
        "    if model in ('v4_eval', 'v4_sched'):\n"
        '        if not request.training_probe or not request.benchmark:\n'
        "            raise HTTPException(422, 'v4 policies are experiment-only')\n"
        "        text = next(m['content'] for m in reversed(request.messages) if m.get('role') == 'user')\n"
        '        length_model, route_model = await asyncio.to_thread(v4_models)\n'
        '        guess = await asyncio.to_thread(length_model.predict, [text], [count], [raw_predicted])\n'
        '        predicted = max(1, round(float(guess[0])))\n')
    source = replace(source, "work_aware=model == 'work_eval', frozen_bandit=frozen_bandit))",
        "work_aware=model == 'work_eval', frozen_bandit=frozen_bandit, route_model=route_model, schedule=model == 'v4_sched'))")
    return source


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('router', type=Path)
    cli.add_argument('target', type=Path)
    args = cli.parse_args()
    pairs = [('dispatcher.py', patch_dispatcher), ('vllm_balancer.py', patch_balancer)]
    sources = [(name, fn((args.router/name).read_text())) for name, fn in pairs]
    for name, text in sources:
        compile(text, name, 'exec')
    for name, text in sources:
        (args.target/name).write_text(text)


if __name__ == '__main__':
    main()
