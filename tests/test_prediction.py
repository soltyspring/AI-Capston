import unittest
from dataclasses import replace
from backend.core import Request, Simulator
from backend.prediction import LearnedPredictor, train_and_split


def training_rows():
    return [Request(i * 1000, 512 if i % 2 else 8192, 50 if i % 2 else 900,
                    (1,) if i % 2 else tuple(range(16))) for i in range(140)]


class PredictionTests(unittest.TestCase):
    def test_learns_nonconstant_output_lengths(self):
        model, test, report = train_and_split(training_rows())
        values = model.predict_many(test)
        self.assertGreater(len(set(values)), 1)
        self.assertNotEqual(model.predict(512, (1,)), 400)
        self.assertEqual(report['train_requests'], 98)
        self.assertEqual(report['test_requests'], len(test))

    def test_future_labels_do_not_change_predictions(self):
        original = training_rows()
        modified = [r if i < 98 else replace(r, output_length=2000) for i, r in enumerate(original)]
        first, test, _ = train_and_split(original)
        second, _, _ = train_and_split(modified)
        self.assertEqual(first.predict_many(test), second.predict_many(test))

    def test_prediction_changes_routing_cost_and_choice(self):
        choices = []
        for output in (50, 900):
            model = LearnedPredictor()
            model.fit([Request(0, 512, output, (0,))] * 40)
            sim = Simulator([Request(0, 512, 100, (0,))], model)
            sim.servers[0].prefill_rate, sim.servers[0].decode_rate = 100000, 50
            sim.servers[1].prefill_rate, sim.servers[1].decode_rate = 500, 500
            sim.servers[2].capacity = 100
            sim.advance(0)
            decision = sim.decisions[0]
            self.assertEqual(decision['predicted_output_tokens'], output)
            self.assertEqual(decision['candidates'][0]['decode'], output / 50)
            choices.append(decision['selected'])
        self.assertEqual(choices, ['A', 'B'])

    def test_error_is_only_recorded_after_completion(self):
        model = LearnedPredictor()
        model.fit([Request(0, 512, 50, (0,))] * 40)
        sim = Simulator([Request(0, 512, 100, (0,))], model)
        sim.advance(0)
        self.assertIsNone(sim.snapshot()['prediction_mae_tokens'])
        self.assertEqual(sim.snapshot()['completions'], [])
        sim.advance(5)
        self.assertEqual(sim.snapshot()['prediction_mae_tokens'], 50)
        self.assertEqual(sim.snapshot()['completions'][0]['predicted_output_tokens'], 50)
