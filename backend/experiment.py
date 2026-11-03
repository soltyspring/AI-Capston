"""Chronological split: fit on first 70%, compare policies on last 15%."""
import argparse
import json
from backend.core import Predictor, Simulator, load_trace
from backend.prediction import train_and_split


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trace")
    args = parser.parse_args()
    rows = load_trace(args.trace)
    predictor, test, model_report = train_and_split(rows)
    if not test:
        raise ValueError("trace too small for test split")
    results = []
    for policy in ("round_robin", "cost"):
        sim = Simulator(test, predictor, policy)
        sim.advance(test[-1].timestamp / 1000 + 1)
        while not sim.snapshot()["finished"]:
            sim.advance(60)
        snapshot = sim.snapshot()
        results.append({k: snapshot[k] for k in ("policy", "processed", "completed", "rejected", "avg_latency")})
    print(json.dumps({'prediction_model': model_report, 'routing_results': results}, indent=2))


if __name__ == "__main__":
    main()
