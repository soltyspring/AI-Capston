"""Supervised output-length regression using arrival-visible features only."""
from collections import Counter
import math
from sklearn.ensemble import RandomForestRegressor
from backend.core import Predictor


class LearnedPredictor(Predictor):
    name = 'random-forest-output-length'
    feature_names = ['input_tokens', 'log_input_tokens', 'input_blocks', 'training_prefix_frequency']

    def __init__(self):
        super().__init__()
        self.prefix_counts = Counter()
        self.model = None

    def features(self, input_length, hash_ids=()):
        frequency = self.prefix_counts.get(hash_ids[0], 0) if hash_ids else 0
        return [input_length, math.log1p(input_length), math.ceil(input_length / 512), frequency]

    def fit(self, training_rows):
        if len(training_rows) < 20:
            raise ValueError('at least 20 training requests required')
        self.prediction_cache = {}
        self.prefix_counts = Counter(r.hash_ids[0] for r in training_rows if r.hash_ids)
        self.default = round(sum(r.output_length for r in training_rows) / len(training_rows))
        self.model = RandomForestRegressor(n_estimators=80, max_depth=8, min_samples_leaf=20,
                                           random_state=42, n_jobs=1)
        self.model.fit([self.features(r.input_length, r.hash_ids) for r in training_rows],
                       [r.output_length for r in training_rows])

    def predict_many(self, requests):
        if self.model is None:
            raise RuntimeError('model must be trained before prediction')
        values = self.model.predict([self.features(r.input_length, r.hash_ids) for r in requests])
        return [max(1, min(2048, round(float(v)))) for v in values]

    def predict(self, input_length, hash_ids=()):
        if self.model is None:
            raise RuntimeError('model must be trained before prediction')
        key = (input_length, tuple(hash_ids))
        if key in getattr(self, 'prediction_cache', {}):
            return self.prediction_cache[key]
        value = self.model.predict([self.features(input_length, hash_ids)])[0]
        return max(1, min(2048, round(float(value))))


def metrics(rows, predictions):
    errors = [prediction - row.output_length for row, prediction in zip(rows, predictions)]
    return {'requests': len(rows), 'mae_tokens': sum(abs(e) for e in errors) / len(errors),
            'rmse_tokens': math.sqrt(sum(e * e for e in errors) / len(errors))}


def train_and_split(rows):
    if len(rows) < 100:
        raise ValueError('at least 100 requests required for 70/15/15 training split')
    train_end, validation_end = int(len(rows) * .7), int(len(rows) * .85)
    train, validation, test = rows[:train_end], rows[train_end:validation_end], rows[validation_end:]
    model = LearnedPredictor()
    model.fit(train)
    report = {'name': model.name, 'algorithm': 'RandomForestRegressor', 'seed': 42,
              'features': model.feature_names, 'train_requests': len(train),
              'validation_requests': len(validation), 'test_requests': len(test),
              'train_last_timestamp_ms': train[-1].timestamp,
              'test_first_timestamp_ms': test[0].timestamp,
              'validation': metrics(validation, model.predict_many(validation)),
              'validation_fixed400': metrics(validation, [400] * len(validation)),
              'validation_training_mean': metrics(validation, [model.default] * len(validation)),
              'training_mean_tokens': model.default}
    # Cache predictions by input features, never by the unknown output label.
    predictions = model.predict_many(test)
    model.prediction_cache = {(r.input_length, r.hash_ids): value for r, value in zip(test, predictions)}
    return model, test, report


if __name__ == '__main__':
    import argparse
    import json
    from backend.core import load_trace
    parser = argparse.ArgumentParser()
    parser.add_argument('trace')
    arguments = parser.parse_args()
    model, test, report = train_and_split(load_trace(arguments.trace))
    report['test'] = metrics(test, model.predict_many(test))
    report['test_fixed400'] = metrics(test, [400] * len(test))
    report['test_training_mean'] = metrics(test, [model.default] * len(test))
    print(json.dumps(report, indent=2))
