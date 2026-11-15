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
    def test_fixed_output_limit_retains_question_order_and_rehashes(self):
        path = Path(cycle.__file__).with_name('prepare-training-workload.py')
        spec = importlib.util.spec_from_file_location('prepare_workload',path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with TemporaryDirectory() as tmp:
            source = Path(tmp)/'source.jsonl'
            source.write_text('\n'.join(json.dumps({'prompt':word,'max_tokens':64}) for word in ('A','A','B')))
            dest = Path(tmp)/'prepared'
            report = module.prepare(source,dest,2000)
            self.assertEqual(report['decode_budgets'],{'2000':2})
            self.assertEqual([json.loads(line) for line in (dest/'requests.jsonl').read_text().splitlines()],
                             [{'prompt':'A','max_tokens':2000},{'prompt':'B','max_tokens':2000}])

    def test_boundaries_and_labels_start_at_saved_cursor(self):
        self.assertEqual(cycle.stage(12345,12444)['phase'],'training')
        self.assertEqual(cycle.stage(12345,12445),
            {'phase':'evaluation','done':0,'checkpoint':12445,'end':12545,'label':100})
        self.assertEqual(cycle.stage(12345,12545)['label'],200)

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
            db.execute('INSERT INTO progress VALUES (1,500,700)')
            experiment = cycle.EvaluationCycles(db,Path(tmp))
            response = {'first_seconds':1,'completion_seconds':3,'finish_reason':'stop',
                        'output_tokens':40,'raw_prediction':100,'cycle_prediction':60}
            for policy in cycle.POLICIES:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM cycle_results').fetchone()[0],0)
                db.executemany('INSERT INTO cycle_trials VALUES (?,?,?,?)',
                    ((i, policy,600,json.dumps(response)) for i in range(600,700)))
                experiment.finish(600)
            experiment.publish(700)
            result = json.loads(experiment.report.read_text())['results'][0]
            self.assertEqual(result['checkpoint'],100)
            self.assertEqual(result['prediction'],{'samples':100,'original':60,'candidate':20})
            self.assertTrue(all(row['completion_p95']==3 for row in result['policies'].values()))


if __name__ == '__main__':
    unittest.main()
