import unittest
from pathlib import Path
from backend.core import Request, Predictor, Simulator, load_trace
from backend.replay import calibrate_pacing, paced_trace


class ReplayTests(unittest.TestCase):
    def test_only_arrival_intervals_change(self):
        rows = [Request(1000,512,20,(1,)), Request(1000,512,30,(1,)), Request(2500,512,40,(2,))]
        paced = paced_trace(rows, 9)
        self.assertEqual([r.timestamp for r in paced], [1000,1000,14500])
        self.assertEqual([r.timestamp for r in rows], [1000,1000,2500])
        self.assertEqual(paced_trace(rows,1), rows)
        for before, after in zip(rows,paced):
            self.assertEqual((before.input_length,before.output_length,before.hash_ids),
                             (after.input_length,after.output_length,after.hash_ids))
        with self.assertRaises(ValueError): paced_trace(rows,0)

    def test_calibration_uses_historical_service_capacity(self):
        rows=[Request(i*1000,4000,400,tuple(range(8))) for i in range(100)]
        pacing=calibrate_pacing(rows)
        self.assertEqual(pacing['interval_scale'],3)
        self.assertEqual(pacing['training_requests'],100)
        self.assertEqual(calibrate_pacing([])['interval_scale'],9)
        with self.assertRaises(ValueError): calibrate_pacing(rows,0)

    def test_mooncake_full_replay_queue_is_not_clamped(self):
        path=Path('data/conversation_trace.jsonl')
        if not path.exists(): self.skipTest('Mooncake file not present')
        rows=load_trace(path)
        training=rows[:int(len(rows)*.7)]
        test=rows[int(len(rows)*.85):]
        scale=calibrate_pacing(training)['interval_scale']
        self.assertEqual(scale,9)
        peaks=[]
        for factor in (scale,1):
            replay=paced_trace(test,factor)
            sim=Simulator(replay,Predictor()); sim.now=replay[0].timestamp/1000
            peak=0
            for timestamp in sorted({row.timestamp for row in replay}):
                sim.advance(timestamp/1000-sim.now)
                peak=max(peak,max(sum(job['start']>sim.now for job in server.jobs) for server in sim.servers))
            self.assertEqual(sim.cursor,len(test))
            peaks.append(peak)
            sim.advance(max(server.jobs[-1]['finish'] if server.jobs else sim.now for server in sim.servers)-sim.now+1)
            final=sim.snapshot()
            self.assertTrue(final['finished'])
            self.assertEqual(final['completed']+final['rejected'],len(test))
            self.assertTrue(all(server['queue']==0 for server in final['servers']))
        self.assertLess(peaks[0],20)
        self.assertGreater(peaks[1],200)
