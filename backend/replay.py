"""Explicit arrival pacing, separate from server processing and playback speed."""
import math
from dataclasses import replace


def calibrate_pacing(training_rows, target_utilization=.65):
    """Use historical training labels only, never evaluation outputs, to size load."""
    if not 0 < target_utilization < 1:
        raise ValueError('target utilization must be between zero and one')
    duration = (training_rows[-1].timestamp - training_rows[0].timestamp) / 1000 if training_rows else 0
    accepted = [row for row in training_rows if row.input_length + 2048 <= 32768]
    if duration <= 0 or not accepted:
        return {'interval_scale': 9, 'target_utilization': target_utilization,
                'basis': 'fallback configuration', 'training_requests': len(training_rows)}
    service = sum(row.input_length / 4000 + row.output_length / 100 for row in accepted) / len(accepted)
    rate = len(accepted) / duration
    scale = max(1, math.ceil(rate * service / (3 * target_utilization)))
    return {'interval_scale': scale, 'target_utilization': target_utilization,
            'basis': 'historical training interval', 'training_requests': len(training_rows),
            'historical_eligible_arrival_rate': rate, 'historical_mean_service_seconds': service}


def paced_trace(rows, interval_scale):
    if not isinstance(interval_scale, int) or interval_scale < 1:
        raise ValueError('interval scale must be a positive integer')
    base = rows[0].timestamp
    return [replace(row, timestamp=base + (row.timestamp - base) * interval_scale) for row in rows]
