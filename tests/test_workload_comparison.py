from pathlib import Path
import sys
import unittest
from unittest.mock import patch

with patch.object(sys, 'path', [str(Path(__file__).resolve().parents[1] / 'deploy/aic-10'), *sys.path]):
    from compare_shadow import savings, summary, workloads_for


def response(tokens, finish, duration, slot, decode=1, prediction=8):
    return {'output_tokens':tokens,'finish_reason':finish,'completion_seconds':duration,
            'slot_seconds':slot,'decode_seconds':decode,'first_seconds':.4,
            'queue_seconds':.1,'raw_prediction':prediction}


class WorkloadComparisonTests(unittest.TestCase):
    def test_token_cost_uses_actual_output_and_error_ignores_truncation(self):
        measured = summary([response(11,'stop',2,1.5),response(21,'length',3,2.5,decode=2)])
        self.assertEqual(measured['decode_ms_per_token'],100)
        self.assertEqual(measured['natural_count'],1)
        self.assertEqual(measured['token_wape'],27.273)

    def test_saved_time_requires_matching_output(self):
        measured = savings({
            ('mixed','production_eval'):[response(10,'stop',4,3),response(11,'stop',4,3)],
            ('mixed','work_eval'):[response(10,'stop',3,2.5),response(2,'stop',1,.5)],
        },'work_eval')
        self.assertEqual(measured['matched'],1)
        self.assertEqual(measured['total_seconds_saved'],1)
        self.assertEqual(measured['slot_seconds_saved'],.5)

    def test_mixed_holdout_prefills_long_requests_before_short_arrivals(self):
        rows = list(workloads_for('mixed',0))
        self.assertTrue(all(r['delay']==0 and r['max_tokens']==640 for r in rows[:12]))
        self.assertTrue(all(r['delay']>0 and r['max_tokens']==96 for r in rows[12:]))
