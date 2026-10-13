import unittest
from backend.core import Predictor, Request, Simulator, load_trace


class SimulatorTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
