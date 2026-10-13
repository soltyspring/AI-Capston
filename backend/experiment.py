"""Chronological split: fit on first 70%, compare policies on last 15%."""
import argparse
import json
from backend.core import Predictor, Simulator, load_trace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trace")
    args = parser.parse_args()
    rows = load_trace(args.trace)
    if len(rows) < 20:
        raise ValueError('at least 20 requests required for chronological training split')
    predictor = Predictor()
    predictor.fit(rows[:int(len(rows) * .7)])
    test = rows[int(len(rows) * .85):]
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
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
