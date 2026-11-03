"""Run from repository root: python -m tools.audit_traffic_modes."""
import json
from backend.api import predictor, replays, pacing
from backend.core import Simulator


def audit():
    results = {}
    for mode, replay in replays.items():
        sim = Simulator(replay, predictor)
        sim.now = replay[0].timestamp / 1000
        peaks = [0, 0, 0]
        for timestamp in sorted({row.timestamp for row in replay}):
            sim.advance(timestamp / 1000 - sim.now)
            for index, server in enumerate(sim.servers):
                peaks[index] = max(peaks[index], sum(job['start'] > sim.now for job in server.jobs))
        end_queues = [sum(job['start'] > sim.now for job in server.jobs) for server in sim.servers]
        sim.advance(max(server.jobs[-1]['finish'] if server.jobs else sim.now for server in sim.servers) - sim.now + 1)
        final = sim.snapshot()
        results[mode] = {'interval_scale': pacing['interval_scale'] if mode == 'standard' else 1,
                         'peak_queues_ABC': peaks, 'arrival_end_queues_ABC': end_queues,
                         'completed': final['completed'], 'rejected': final['rejected'],
                         'finished': final['finished'], 'mean_latency_seconds': round(final['avg_latency'], 3)}
    return results


if __name__ == '__main__':
    print(json.dumps(audit(), indent=2))
