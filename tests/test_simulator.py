import unittest
from backend.core import Predictor, Request, Simulator, load_trace


class SimulatorTests(unittest.TestCase):
    def test_exact_ties_rotate_without_overriding_lower_cost(self):
        # No full cache blocks, and enough idle time for three genuine ties.
        sim = Simulator([Request(i * 10000, 100, 1, (i,)) for i in range(4)])
        sim.advance(40)
        self.assertEqual([d['selected'] for d in sim.decisions], ['A','B','C','A'])
        self.assertTrue(all(d['selection_reason'] == 'tie_round_robin' for d in sim.decisions))
        faster = Simulator([Request(0, 100, 1, (0,))])
        faster.servers[2].prefill_rate = 8000
        faster.advance(0)
        self.assertEqual(faster.decisions[0]['selected'], 'C')
        self.assertEqual(faster.decisions[0]['tie_servers'], [])

    def test_arrival_batch_and_queue_identities(self):
        sim = Simulator([Request(0, 100, 100, (i,)) for i in range(8)])
        sim.advance(0)
        state = sim.snapshot()
        self.assertEqual(state['latest_arrival'], dict(time=0,arrived=8,immediate=3,queued=5,rejected=0))
        self.assertEqual(sum(s['queue'] for s in state['servers']), 5)
        self.assertEqual(sum(len(s['queued_requests']) for s in state['servers']), 5)
        queued = [j['id'] for s in state['servers'] for j in s['queued_requests']]
        self.assertEqual(len(set(queued)),5)
        sim.advance(1.1)
        self.assertLess(sum(s['queue'] for s in sim.snapshot()['servers']),5)

    def test_simultaneous_requests_have_no_instant_cache(self):
        sim = Simulator([Request(0, 1024, 100, (0, 1)), Request(0, 1024, 100, (0, 1))])
        sim.advance(0)
        self.assertEqual(sim.cursor, 2)
        self.assertTrue(all(c['cached_tokens'] == 0 for d in sim.decisions for c in d['candidates']))
        self.assertEqual([d['selected'] for d in sim.decisions], ['A', 'B'])

    def test_completed_prefix_is_reused(self):
        sim = Simulator([Request(0, 1024, 10, (0, 1)), Request(5000, 1024, 10, (0, 1))])
        sim.advance(5)
        self.assertEqual(sim.decisions[-1]['selected'], 'A')
        self.assertEqual(sim.decisions[-1]['candidates'][0]['cached_tokens'], 1024)

    def test_future_output_does_not_change_routing(self):
        decisions = []
        for output in (1, 2000):
            sim = Simulator([Request(0, 1024, output, (0, 1))])
            sim.advance(0)
            decisions.append(sim.decisions)
        self.assertEqual(*decisions)

    def test_context_limit_uses_configured_cap(self):
        sim = Simulator([Request(0, 32000, 1, tuple(range(63)))])
        sim.advance(0)
        self.assertEqual(sim.rejected, 1)

    def test_partial_blocks_not_reused(self):
        sim = Simulator([Request(0, 700, 10, (0, 1))])
        sim.advance(5)
        self.assertEqual(sim.servers[0].cached_tokens(sim.rows[0]), 512)

    def test_cache_respects_capacity(self):
        sim = Simulator([Request(i * 5000, 1024, 10, (i, i)) for i in range(10)])
        sim.servers[0].capacity = 4096
        sim.advance(60)
        self.assertLessEqual(len(sim.servers[0].cache) * 512, 4096)

    def test_demo_drains(self):
        sim = Simulator(load_trace('data/demo.jsonl'))
        sim.advance(120)
        state = sim.snapshot()
        self.assertTrue(state['finished'])
        self.assertEqual(state['completed'], 7)
        self.assertEqual(state['rejected'], 1)

    def test_training_is_explicit(self):
        model = Predictor()
        self.assertEqual(model.predict(1024), 400)
        model.fit([Request(0, 1024, 100, (0, 1))])
        self.assertEqual(model.predict(1024), 100)

    def test_active_plus_idle_cache_stays_within_capacity(self):
        sim = Simulator(load_trace('data/demo.jsonl'))
        for _ in range(120):
            sim.advance(.25)
            self.assertTrue(all(s['memory'] <= 100 for s in sim.snapshot()['servers']))

    def test_latency_only_counts_completed_requests(self):
        sim = Simulator([Request(0, 1024, 100, (0, 1))])
        sim.advance(0)
        self.assertEqual(sim.snapshot()['avg_latency'], 0)
        sim.advance(2)
        self.assertGreater(sim.snapshot()['avg_latency'], 0)

    def test_playback_step_size_does_not_change_demo_decisions(self):
        rows = load_trace('data/demo.jsonl')
        normal, fast = Simulator(rows), Simulator(rows)
        for _ in range(480):
            normal.advance(.25)
        fast.advance(120)
        self.assertEqual(normal.decisions, fast.decisions)
        self.assertEqual(normal.snapshot(), fast.snapshot())

    def test_active_phase_and_completion_event(self):
        sim = Simulator([Request(0, 4000, 100, tuple(range(8)))])
        sim.advance(.5)
        active = sim.snapshot()['servers'][0]['activity']
        self.assertEqual(active['phase'], 'prefill')
        self.assertEqual(active['generated_tokens'], 0)
        self.assertEqual(sim.snapshot()['completions'], [])
        sim.advance(1)
        self.assertEqual(sim.snapshot()['servers'][0]['activity']['phase'], 'decode')
        self.assertEqual(sim.snapshot()['servers'][0]['activity']['generated_tokens'], 50)
        sim.advance(1)
        state = sim.snapshot()
        self.assertIsNone(state['servers'][0]['activity'])
        self.assertEqual(state['completions'][0]['output_tokens'], 100)
        self.assertEqual(state['completions'][0]['time'], 2)

    def test_cumulative_assigned_survives_log_truncation(self):
        rows = [Request(i * 2000, 512, 1, (i,)) for i in range(150)]
        sim = Simulator(rows)
        sim.advance(1000)
        state = sim.snapshot()
        self.assertEqual(sum(s['assigned'] for s in state['servers']), 150)
        self.assertLess(len(state['decisions']), 150)
        self.assertEqual(sum(s['completed'] for s in state['servers']), 150)

    def test_decision_factors_are_captured_before_assignment(self):
        sim = Simulator([Request(0, 1024, 100, (0, 1)), Request(0, 1024, 100, (0, 1))])
        sim.advance(0)
        first, second = sim.decisions
        self.assertEqual(first['candidates'][0]['observation']['context_tokens'], 0)
        self.assertEqual(second['candidates'][0]['observation']['context_tokens'], 1024)
        self.assertEqual(first['candidates'][0]['required_capacity_tokens'], 3072)
        self.assertIsNone(sim.snapshot()['servers'][0]['factors']['gpu_utilization'])
        sim.advance(2)
        self.assertEqual(second['candidates'][0]['observation']['context_tokens'], 1024)

    def test_factor_totals_and_cache_reuse(self):
        sim = Simulator([Request(0, 1024, 10, (0, 1)), Request(5000, 1024, 10, (0, 1))])
        sim.advance(5)
        snapshot = sim.snapshot()
        self.assertEqual(snapshot['saved_tokens'], 1024)
        self.assertEqual(snapshot['cache_hit_requests'], 1)
        candidate = sim.decisions[-1]['candidates'][0]
        self.assertEqual(candidate['new_input_tokens'], 0)
        self.assertEqual(candidate['cache_reason'], 'hit')
        for s in snapshot['servers']:
            f = s['factors']
            self.assertEqual(f['kv_free_tokens'] + f['kv_used_tokens'], f['kv_capacity_tokens'])
            self.assertEqual(f['queue'], s['queue'])
            self.assertEqual(f['queued_input_tokens'], s['queued_input_tokens'])


if __name__ == '__main__':
    unittest.main()
