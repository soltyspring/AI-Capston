#!/usr/bin/env python3
"""Patch the deployed router's private probe path without changing production policy."""
import argparse
from pathlib import Path


def replace(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f'Expected one anchor: {old}')
    return source.replace(old,new,1)


def patch(dispatcher, router):
    for previous in (320,1024):
        router = router.replace(f'training_max_tokens: int = Field(default=128, ge=1, le={previous})',
                                'training_max_tokens: int = Field(default=128, ge=1, le=2000)')
    if 'training_max_tokens: int = Field(default=128, ge=1, le=2000)' not in router:
        raise ValueError('Unsupported experiment token field')
    if 'training_output_limit=max_output' not in router:
        router = replace(router, 'raw_predicted_output_tokens=raw_predicted, policy=policy_label)',
                         'raw_predicted_output_tokens=raw_predicted, policy=policy_label,\n'
                         '                        **({"training_output_limit": max_output} if request.training_probe else {}))')
    if '    evaluation_checkpoint:' in router:
        return dispatcher, router
    dispatcher = replace(dispatcher, 'shadow=False, work_aware=False):',
                         'shadow=False, work_aware=False, frozen_bandit=None):')
    dispatcher = replace(dispatcher, '(state.shadow_bandit if shadow else state.bandit).score',
                         '(frozen_bandit or (state.shadow_bandit if shadow else state.bandit)).score')
    router = replace(router, 'from category import classify\n',
                     'from category import classify\nfrom length_forecast import estimate as cycle_estimate\n'
                     'from qless_cycle_router import models as cycle_models, summary as cycle_summary\n')
    router = replace(router, '    training_max_tokens: int',
                     '    evaluation_checkpoint: int | None = Field(default=None, ge=1)\n    training_max_tokens: int')
    router = replace(router, "                'shadow_comparison': shadow_report(),\n",
                     "                'shadow_comparison': shadow_report(),\n                'evaluation_cycles': cycle_summary(),\n")
    router = replace(router, "    raw_predicted = predicted\n    if request.training_probe:\n", """    raw_predicted = predicted
    frozen_bandit = None
    if request.evaluation_checkpoint is not None:
        if not request.training_probe or not request.benchmark or not (
                model in ('shadow_eval','shortest_eval') or
                (model == 'production_eval' and probe_policy == 'round_robin')):
            raise HTTPException(422, 'Invalid checkpoint evaluation policy')
        try:
            forecast, frozen_bandit = cycle_models(request.evaluation_checkpoint, state.shadow_bandit.alpha)
        except (OSError, ValueError) as exc:
            raise HTTPException(503, 'Evaluation checkpoint unavailable') from exc
        user_text = next(m['content'] for m in reversed(request.messages) if m.get('role') == 'user')
        predicted = cycle_estimate(forecast, user_text, count, predicted)
    if request.training_probe:
""")
    router = replace(router, "work_aware=model == 'work_eval'))", "work_aware=model == 'work_eval', frozen_bandit=frozen_bandit))")
    return dispatcher, router


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('router', type=Path)
    cli.add_argument('target', type=Path)
    args = cli.parse_args()
    paths = [args.router/name for name in ('dispatcher.py','vllm_balancer.py')]
    texts = patch(*(p.read_text() for p in paths))
    for path, source in zip(paths,texts):
        compile(source, str(path), 'exec')
    for path, source in zip(paths,texts):
        (args.target/path.name).write_text(source)


if __name__=='__main__':
    main()
