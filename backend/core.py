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
        self.default = round(sum(r.output_length for r in training_rows) / len(training_rows))

    def predict(self, input_length, hash_ids=()):
        return max(1, min(2048, round(self.means.get(input_length // 4096, self.default))))

    @property
    def name(self):
        return 'trained-bin-mean' if self.means else 'fixed-400'


@dataclass
class Server:
    id: str
    capacity: int = 32768
    prefill_rate: int = 4000
    decode_rate: int = 100
    jobs: list = field(default_factory=list)
    cache: OrderedDict = field(default_factory=OrderedDict)
    completed: int = 0
    assigned: int = 0
    saved_tokens: int = 0
    cache_hit_requests: int = 0
    pending_input_tokens: int = 0
    prediction_absolute_error: float = 0
    latencies: list = field(default_factory=list)
    events: list = field(default_factory=list)

    def observation(self, now):
        first = self.jobs[0] if self.jobs else None
        active = first if first and first['start'] <= now < first['finish'] else None
        input_tokens = active['request'].input_length if active else 0
        generated = max(0, int((now - active['start'] - active['prefill_duration']) * self.decode_rate)) if active else 0
        used = (input_tokens + 2048 - active['cached_tokens'] if active else 0) + len(self.cache) * 512
        return dict(queue=len(self.jobs) - int(active is not None), queued_input_tokens=self.pending_input_tokens - input_tokens,
                    context_tokens=input_tokens, live_context_tokens=input_tokens + generated,
                    generated_tokens=generated, cache_tokens=len(self.cache) * 512,
                    kv_used_tokens=used, kv_free_tokens=self.capacity - used, kv_capacity_tokens=self.capacity,
                    output_reservation_tokens=2048 if active else 0,
                    gpu_utilization=None, prefill_rate=self.prefill_rate, decode_rate=self.decode_rate)

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
            self.pending_input_tokens -= row.input_length
            for i in range(row.input_length // 512):
                key = row.hash_ids[:i + 1]
                self.cache[key] = True
                self.cache.move_to_end(key)
            while len(self.cache) * 512 > self.capacity:
                self.cache.popitem(last=False)
            self.completed += 1
            self.latencies.append(job['finish'] - row.timestamp / 1000)
            self.events.append(dict(id=job['id'], server=self.id, time=job['finish'],
                                    input_tokens=row.input_length, output_tokens=row.output_length,
                                    predicted_output_tokens=job['predicted_output'],
                                    error_tokens=abs(row.output_length - job['predicted_output']),
                                    latency=job['finish'] - row.timestamp / 1000))
            self.prediction_absolute_error += abs(row.output_length - job['predicted_output'])
            self.events = self.events[-12:]
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
        self.tie_next = 0
        self.latest_arrival = None

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
        predicted = self.predictor.predict(row.input_length, row.hash_ids)
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
                                   cached_tokens=cached,
                                   matched_prefix_tokens=server.cached_tokens(row),
                                   new_input_tokens=row.input_length - cached,
                                   required_capacity_tokens=row.input_length + 2048,
                                   observation=server.observation(self.now),
                                   cache_reason='busy' if server.jobs else 'hit' if cached else 'miss'))
        valid = [x for x in candidates if x["eligible"]]
        tied = []
        if not valid:
            self.rejected += 1
            selected = None
        else:
            if self.policy == "cost":
                best = min(x['total'] for x in valid)
                tied = [x for x in valid if x['total'] == best]
                selected = min(tied, key=lambda x: ("ABC".index(x['server']) - self.tie_next) % 3)
                if len(tied) > 1:
                    self.tie_next = ("ABC".index(selected['server']) + 1) % 3
            else:
                selected = valid[self.cursor % len(valid)]
            server = next(s for s in self.servers if s.id == selected["server"])
            server.assigned += 1
            server.pending_input_tokens += row.input_length
            server.saved_tokens += selected['cached_tokens']
            server.cache_hit_requests += int(selected['cached_tokens'] > 0)
            # One active request per replica. Queued jobs reserve no KV until execution.
            start = max(self.now, server.jobs[-1]["finish"]) if server.jobs else self.now
            duration = selected["prefill"] + row.output_length / server.decode_rate
            finish = start + duration
            server.jobs.append(dict(id=self.cursor, request=row, start=start, finish=finish,
                                    estimated_finish=self.now + selected["total"],
                                    prefill_duration=selected['prefill'],
                                    predicted_output=predicted,
                                    cached_tokens=selected['cached_tokens']))
            server.trim_cache(self.now)
            self.latencies.append(finish - self.now)
        if self.latest_arrival is None or self.latest_arrival['time'] != self.now:
            self.latest_arrival = dict(time=self.now, arrived=0, immediate=0, queued=0, rejected=0)
        self.latest_arrival['arrived'] += 1
        self.latest_arrival['rejected' if selected is None else 'immediate' if start <= self.now else 'queued'] += 1
        self.decisions.append(dict(id=self.cursor, time=self.now, input_tokens=row.input_length,
                                   predicted_output_tokens=predicted,
                                   selected=selected["server"] if selected else None,
                                   tie_servers=[x['server'] for x in tied] if len(tied) > 1 else [],
                                   selection_reason='tie_round_robin' if len(tied) > 1 else 'minimum_cost' if self.policy == 'cost' else 'round_robin',
                                   candidates=candidates))
        self.decisions = self.decisions[-100:]

    def snapshot(self):
        servers = []
        for s in self.servers:
            active = next((j for j in s.jobs if j["start"] <= self.now < j["finish"]), None)
            context = active["request"].input_length if active else 0
            # Idle cache is evictable; report modeled resident tokens, not real VRAM.
            resident = (context + 2048 - active['cached_tokens'] if active else 0) + len(s.cache) * 512
            activity = None
            if active:
                elapsed = self.now - active['start']
                generating = elapsed >= active['prefill_duration']
                generated = max(0, int((elapsed - active['prefill_duration']) * s.decode_rate))
                expected_duration = active['prefill_duration'] + active['predicted_output'] / s.decode_rate
                activity = dict(id=active['id'], phase='decode' if generating else 'prefill',
                                elapsed=elapsed, generated_tokens=generated,
                                predicted_output_tokens=active['predicted_output'],
                                progress=min(99, round(elapsed / expected_duration * 100)),
                                cached_tokens=active['cached_tokens'])
            servers.append(dict(id=s.id, name=f"GPU Server {s.id}", gpu=100 if active else 0,
                                activity=activity, assigned=s.assigned,
                                factors=s.observation(self.now),
                                queued_requests=[dict(id=j['id'], input_tokens=j['request'].input_length) for j in s.jobs if j['start'] > self.now][:32],
                                saved_tokens=s.saved_tokens, cache_hit_requests=s.cache_hit_requests,
                                memory=round(resident / s.capacity * 100), queue=sum(j["start"] > self.now for j in s.jobs),
                                context_tokens=context, cache_tokens=len(s.cache) * 512,
                                queued_input_tokens=sum(j["request"].input_length for j in s.jobs if j["start"] > self.now),
                                completed=s.completed, requests=sum(d["selected"] == s.id for d in self.decisions),
                                latency=0, tokens=s.decode_rate if active else 0, status="Simulated"))
        return dict(time=self.now, processed=self.cursor, total=len(self.rows), rejected=self.rejected,
                    completed=sum(s.completed for s in self.servers),
                    finished=self.cursor == len(self.rows) and not any(s.jobs for s in self.servers),
                    servers=servers, decisions=self.decisions[-60:], policy=self.policy,
                    latest_arrival=dict(self.latest_arrival) if self.latest_arrival else None,
                    saved_tokens=sum(s.saved_tokens for s in self.servers),
                    cache_hit_requests=sum(s.cache_hit_requests for s in self.servers),
                    completions=sorted([e for s in self.servers for e in s.events], key=lambda e: (e['time'], e['id']))[-20:],
                    predictor=self.predictor.name,
                    prediction_mae_tokens=(sum(s.prediction_absolute_error for s in self.servers) /
                                           sum(s.completed for s in self.servers)) if any(s.completed for s in self.servers) else None,
                    avg_latency=(sum(sum(s.latencies) for s in self.servers) /
                                 sum(s.completed for s in self.servers)) if any(s.completed for s in self.servers) else 0)
