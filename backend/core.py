"""Deterministic single-worker-per-replica simulator, not GPU measurements."""
import json
import math
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Request:
    timestamp: int
    input_length: int
    output_length: int
    hash_ids: tuple


def load_trace(path):
    rows = []
    previous = -1
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        for name in ("timestamp", "input_length", "output_length"):
            value = row[name]
            if type(value) is not int or value < (0 if name == "timestamp" else 1):
                raise ValueError(f"line {line_number}: invalid {name}")
        ids = row["hash_ids"]
        if not isinstance(ids, list) or any(type(x) is not int for x in ids):
            raise ValueError(f"line {line_number}: invalid hash_ids")
        if len(ids) != math.ceil(row["input_length"] / 512):
            raise ValueError(f"line {line_number}: block count mismatch")
        if row["timestamp"] < previous:
            raise ValueError("timestamps must be nondecreasing; simultaneous arrivals are preserved")
        previous = row["timestamp"]
        rows.append(Request(row["timestamp"], row["input_length"], row["output_length"], tuple(ids)))
    if not rows:
        raise ValueError("empty trace")
    return rows


class Predictor:
    """Fixed baseline; optional supervised bin means fitted on training rows only."""
    def __init__(self, default=400):
        self.default = default
        self.means = {}

    def fit(self, training_rows):
        bins = {}
        for row in training_rows:
            bins.setdefault(row.input_length // 4096, []).append(row.output_length)
        self.means = {key: sum(values) / len(values) for key, values in bins.items()}

    def predict(self, input_length):
        return max(1, min(2048, round(self.means.get(input_length // 4096, self.default))))


@dataclass
class Server:
    id: str
    capacity: int = 32768
    prefill_rate: int = 4000
    decode_rate: int = 100
    jobs: list = field(default_factory=list)
    cache: OrderedDict = field(default_factory=OrderedDict)
    completed: int = 0
    latencies: list = field(default_factory=list)

    def trim_cache(self, now):
        active = next((j for j in self.jobs if j['start'] <= now < j['finish']), None)
        if active:
            reserve = active['request'].input_length + 2048
            shared = active['cached_tokens']
            protected = {active['request'].hash_ids[:i + 1] for i in range(shared // 512)}
            budget = self.capacity - reserve + shared
        else:
            protected, budget = set(), self.capacity
        for key in list(self.cache):
            if len(self.cache) * 512 <= budget:
                break
            if key not in protected:
                del self.cache[key]

    def cached_tokens(self, request):
        count = 0
        # Prefix keys include every preceding block; partial final blocks are not reused.
        for i in range(request.input_length // 512):
            if request.hash_ids[:i + 1] not in self.cache:
                break
            count += 512
        return count

    def finish(self, now):
        while self.jobs and self.jobs[0]["finish"] <= now:
            job = self.jobs.pop(0)
            row = job["request"]
            for i in range(row.input_length // 512):
                key = row.hash_ids[:i + 1]
                self.cache[key] = True
                self.cache.move_to_end(key)
            while len(self.cache) * 512 > self.capacity:
                self.cache.popitem(last=False)
            self.completed += 1
            self.latencies.append(job['finish'] - row.timestamp / 1000)
        self.trim_cache(now)


class Simulator:
    def __init__(self, rows, predictor=None, policy="cost"):
        self.rows = rows
        self.predictor = predictor or Predictor()
        if policy not in ("cost", "round_robin"):
            raise ValueError("unknown policy")
        self.policy = policy
        self.servers = [Server(x) for x in "ABC"]
        self.now = 0.0
        self.cursor = 0
        self.rejected = 0
        self.decisions = []
        self.latencies = []

    def advance(self, seconds):
        end = self.now + seconds
        while self.cursor < len(self.rows) and self.rows[self.cursor].timestamp / 1000 <= end:
            row = self.rows[self.cursor]
            self.now = row.timestamp / 1000
            for server in self.servers:
                server.finish(self.now)
            self.route(row)
            self.cursor += 1
        self.now = end
        for server in self.servers:
            server.finish(end)

    def route(self, row):
        predicted = self.predictor.predict(row.input_length)
        candidates = []
        for server in self.servers:
            # Configured output cap, never the future actual length, controls eligibility.
            eligible = row.input_length + 2048 <= server.capacity
            # Conservative: queued requests do not claim future/unpinned cache hits.
            cached = server.cached_tokens(row) if not server.jobs else 0
            wait = max(0, server.jobs[-1]["estimated_finish"] - self.now) if server.jobs else 0
            prefill = (row.input_length - cached) / server.prefill_rate
            decode = predicted / server.decode_rate
            candidates.append(dict(server=server.id, eligible=eligible, wait=wait,
                                   prefill=prefill, decode=decode, total=wait + prefill + decode,
                                   cached_tokens=cached))
        valid = [x for x in candidates if x["eligible"]]
        if not valid:
            self.rejected += 1
            selected = None
        else:
            selected = min(valid, key=lambda x: (x["total"], x["server"])) if self.policy == "cost" else valid[self.cursor % len(valid)]
            server = next(s for s in self.servers if s.id == selected["server"])
            # One active request per replica. Queued jobs reserve no KV until execution.
            start = max(self.now, server.jobs[-1]["finish"]) if server.jobs else self.now
            duration = selected["prefill"] + row.output_length / server.decode_rate
            finish = start + duration
            server.jobs.append(dict(request=row, start=start, finish=finish,
                                    estimated_finish=self.now + selected["total"],
                                    cached_tokens=selected['cached_tokens']))
            server.trim_cache(self.now)
            self.latencies.append(finish - self.now)
        self.decisions.append(dict(id=self.cursor, time=self.now, input_tokens=row.input_length,
                                   predicted_output_tokens=predicted,
                                   selected=selected["server"] if selected else None,
                                   candidates=candidates))
        self.decisions = self.decisions[-100:]

    def snapshot(self):
        servers = []
        for s in self.servers:
            active = next((j for j in s.jobs if j["start"] <= self.now < j["finish"]), None)
            context = active["request"].input_length if active else 0
            # Idle cache is evictable; report modeled resident tokens, not real VRAM.
            resident = (context + 2048 - active['cached_tokens'] if active else 0) + len(s.cache) * 512
            servers.append(dict(id=s.id, name=f"GPU Server {s.id}", gpu=100 if active else 0,
                                memory=round(resident / s.capacity * 100), queue=sum(j["start"] > self.now for j in s.jobs),
                                context_tokens=context, cache_tokens=len(s.cache) * 512,
                                queued_input_tokens=sum(j["request"].input_length for j in s.jobs if j["start"] > self.now),
                                completed=s.completed, requests=sum(d["selected"] == s.id for d in self.decisions),
                                latency=0, tokens=s.decode_rate if active else 0, status="Simulated"))
        return dict(time=self.now, processed=self.cursor, total=len(self.rows), rejected=self.rejected,
                    completed=sum(s.completed for s in self.servers),
                    finished=self.cursor == len(self.rows) and not any(s.jobs for s in self.servers),
                    servers=servers, decisions=self.decisions[-20:], policy=self.policy,
                    predictor="trained-bin-mean" if self.predictor.means else "fixed-400",
                    avg_latency=(sum(sum(s.latencies) for s in self.servers) /
                                 sum(s.completed for s in self.servers)) if any(s.completed for s in self.servers) else 0)
