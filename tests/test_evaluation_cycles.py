import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'deploy/aic-10'))
import evaluation_cycles as cycle
from length_forecast import estimate, features, extra_features, TOPICS


class EvaluationTests(unittest.TestCase):
    def test_boundaries_and_labels_start_at_saved_cursor(self):
        self.assertEqual(cycle.stage(12345,13344)['phase'],'training')
        self.assertEqual(cycle.stage(12345,13345),
            {'phase':'evaluation','done':0,'checkpoint':13345,'end':14345,'label':1000})
        self.assertEqual(cycle.stage(12345,15345)['label'],2000)

    def test_v3_excludes_topic_flags_even_when_they_differ(self):
        vocabulary = ['표로']
        n = len(features('',1,1)) + len(extra_features('',vocabulary))-len(TOPICS)
        model = {'version':3,'vocabulary':vocabulary,'center':[0]*n,'scale':[1]*n,
                 'weights':[0]*6+[.5]*(n-6),'intercept':4}
        self.assertEqual(estimate(model,'금융 표로',20,100),estimate(model,'의료 표로',20,100))
        self.assertGreater(estimate(model,'금융 표로',20,100), estimate(model,'금융 관련',20,100))

    def test_checkpoint_result_requires_three_complete_policy_trials(self):
        with TemporaryDirectory() as tmp, sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE progress (id INTEGER, next_index INTEGER,total INTEGER)')
            db.execute('INSERT INTO progress VALUES (1,500,2500)')
            experiment = cycle.EvaluationCycles(db,Path(tmp))
            response = {'first_seconds':1,'completion_seconds':3,'finish_reason':'stop',
                        'output_tokens':40,'raw_prediction':100,'cycle_prediction':60}
            for policy in cycle.POLICIES:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM cycle_results').fetchone()[0],0)
                db.executemany('INSERT INTO cycle_trials VALUES (?,?,?,?)',
                    ((i, policy,1500,json.dumps(response)) for i in range(1500,2500)))
                experiment.finish(1500)
            experiment.publish(2500)
            result = json.loads(experiment.report.read_text())['results'][0]
            self.assertEqual(result['checkpoint'],1000)
            self.assertEqual(result['prediction'],{'samples':1000,'original':60,'candidate':20})
            self.assertTrue(all(row['completion_p95']==3 for row in result['policies'].values()))


if __name__ == '__main__':
    unittest.main()
