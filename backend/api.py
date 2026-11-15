import asyncio
import hashlib
import math
import os
import secrets
import time
import httpx
import json
import sqlite3
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from backend.core import Predictor, Simulator, load_trace
from backend.dataset import ensure_mooncake, REVISION, SHA256
from backend.prediction import train_and_split
from backend.replay import calibrate_pacing, paced_trace

ROOT = Path(__file__).resolve().parents[1]
trace_path = Path(os.environ['AICAP_TRACE']) if os.environ.get('AICAP_TRACE') else ensure_mooncake()
rows = load_trace(trace_path)
original_rows = rows
trace_sha256 = hashlib.sha256(trace_path.read_bytes()).hexdigest()
is_mooncake = trace_sha256 == SHA256
predictor = Predictor()
model_report = None
if os.environ.get('AICAP_TRAIN', '1') == '1':
    predictor, rows, model_report = train_and_split(rows)
calibration_rows = original_rows[:model_report['train_requests']] if model_report else []
pacing = calibrate_pacing(calibration_rows)
replays = {'standard': paced_trace(rows, pacing['interval_scale']), 'original': rows}
# The trained model and immutable trace are shared; mutable simulation state is not.
COOKIE = 'aicap_session'
SESSION_TTL = 30 * 60
MAX_SESSIONS = 48
sessions = {}
lock = asyncio.Lock()
real_request_lock = asyncio.Lock()
real_last_request = 0.0
real_recent_requests = deque()
real_inflight = 0
REAL_HOURLY_LIMIT = max(1, int(os.environ.get('AICAP_REAL_HOURLY_LIMIT', '120')))


def new_simulator(policy='cost', traffic_mode='standard'):
    replay = replays[traffic_mode]
    simulator = Simulator(replay, predictor, policy=policy)
    simulator.now = replay[0].timestamp / 1000
    return simulator


@dataclass
class VisitorSession:
    simulator: Simulator = field(default_factory=new_simulator)
    running: bool = False
    speed: int = 1
    run_id: int = 0
    traffic_mode: str = 'standard'
    last_seen: float = field(default_factory=time.monotonic)
    tick: float = field(default_factory=time.monotonic)

    def advance_clock(self, now):
        if self.running:
            # Advance lazily on this visitor's requests, not in one global clock.
            self.simulator.advance(max(0, now - self.tick) * self.speed)
            if self.simulator.snapshot()['finished']:
                self.running = False
        self.tick = now


def get_session(request, response):
    now = time.monotonic()
    for key, value in list(sessions.items()):
        if now - value.last_seen > SESSION_TTL:
            del sessions[key]
    token = request.cookies.get(COOKIE)
    visitor = sessions.get(token)
    if visitor is None:
        if len(sessions) >= MAX_SESSIONS:
            raise HTTPException(503, '방문자가 많습니다. 잠시 후 다시 시도하세요.')
        token = secrets.token_urlsafe(32)
        visitor = VisitorSession(last_seen=now, tick=now)
        sessions[token] = visitor
    visitor.advance_clock(now)
    visitor.last_seen = now
    response.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True,
                        secure=request.url.scheme == 'https', samesite='lax', path='/')
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Vary'] = 'Cookie'
    return visitor


def snapshot(visitor):
    return {**visitor.simulator.snapshot(), 'running': visitor.running,
            'speed': visitor.speed, 'run_id': visitor.run_id,
            'session': {'isolated': True, 'idle_timeout_minutes': SESSION_TTL // 60},
            'traffic': {'mode': visitor.traffic_mode,
                        'label': '일반 보기' if visitor.traffic_mode == 'standard' else '원본 트래픽 · 과부하 실험',
                        'arrival_interval_scale': pacing['interval_scale'] if visitor.traffic_mode == 'standard' else 1,
                        'calibration': pacing,
                        'source_duration_seconds': (rows[-1].timestamp - rows[0].timestamp) / 1000,
                        'replay_duration_seconds': (visitor.simulator.rows[-1].timestamp - visitor.simulator.rows[0].timestamp) / 1000},
            'source': trace_path.name, 'model': model_report,
            'dataset': {'name': 'Mooncake FAST25 Conversation' if is_mooncake else trace_path.name,
                        'requests': len(original_rows), 'replay_requests': len(rows),
                        'duration_seconds': (original_rows[-1].timestamp - original_rows[0].timestamp) / 1000,
                        'first_timestamp_seconds': rows[0].timestamp / 1000,
                        'replay_end_seconds': visitor.simulator.rows[-1].timestamp / 1000,
                        'mode': '과거 70% 학습 · 15% 검증 · 마지막 15% 재생' if model_report else '전체 요청 재생 · 고정 출력 400토큰 (학습 비활성)',
                        'revision': REVISION if is_mooncake else None,
                        'sha256': trace_sha256}}



GPU_SERVER_IDS = ('ai', 'ai1', 'ai2')
BENCHMARK_POLICIES = ('linucb', 'round_robin', 'shortest_predicted')
BENCHMARK_CONCURRENCY = (3, 6, 9)
ROUTER_POLL_SECONDS = 3
MONITOR_ANALYSIS_CACHE_SECONDS = 30
monitor_analysis_cache = {'expires_at': 0.0, 'value': None}
monitor_analysis_lock = asyncio.Lock()
monitor_summary_cache = {'expires_at': 0.0, 'value': None}
monitor_summary_lock = asyncio.Lock()
MONITOR_DB = Path(os.environ.get(
    'AICAP_MONITOR_DB', str(Path.home() / '.local/share/aicap/data/monitor.sqlite3')
))
RESOURCE_RETENTION_DAYS = 45
INCIDENT_RULES = {
    'completion_over_30_seconds': '요청 완료 시간이 30초 초과',
    'queue_overflow': '로드밸런서 대기열 한도 초과로 거절',
    'gpu_memory_backlog': 'GPU 메모리 사용률 95% 이상이며 대기 요청이 존재',
    'first_token_over_2_seconds': '첫 토큰 응답 시간이 2초 초과',
}


@contextmanager
def monitor_db():
    MONITOR_DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(MONITOR_DB, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA busy_timeout=10000')
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_monitor_db():
    with monitor_db() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS real_requests (
                request_id TEXT PRIMARY KEY,
                received_at REAL NOT NULL,
                completed_at REAL,
                input_tokens INTEGER,
                predicted_output_tokens INTEGER,
                actual_input_tokens INTEGER,
                actual_output_tokens INTEGER,
                server_id TEXT,
                queue_wait_seconds REAL,
                first_response_seconds REAL,
                completion_seconds REAL,
                status TEXT NOT NULL,
                failure_reason TEXT,
                http_status INTEGER,
                policy TEXT,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS real_requests_received_idx ON real_requests(received_at DESC);
            CREATE TABLE IF NOT EXISTS resource_samples (
                sampled_at REAL NOT NULL,
                server_id TEXT NOT NULL,
                healthy INTEGER NOT NULL,
                cpu_pct REAL,
                memory_pct REAL,
                memory_available_bytes REAL,
                gpu_utilization_pct REAL,
                gpu_memory_used_mib REAL,
                gpu_memory_total_mib REAL,
                waiting_requests REAL,
                running_requests REAL,
                router_waiting REAL,
                PRIMARY KEY(sampled_at, server_id)
            );
            CREATE INDEX IF NOT EXISTS resource_samples_time_idx ON resource_samples(sampled_at DESC);
            CREATE TABLE IF NOT EXISTS resource_incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL
            );
            CREATE INDEX IF NOT EXISTS resource_incidents_started_idx ON resource_incidents(started_at DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS resource_incidents_open_idx ON resource_incidents(server_id)
                WHERE ended_at IS NULL;
            CREATE TABLE IF NOT EXISTS benchmark_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at REAL NOT NULL,
                completed_at REAL,
                status TEXT NOT NULL,
                question_count INTEGER NOT NULL,
                order_first TEXT,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS benchmark_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL UNIQUE,
                run_id INTEGER NOT NULL REFERENCES benchmark_runs(id),
                arrival_at REAL NOT NULL,
                completed_at REAL,
                policy TEXT NOT NULL,
                concurrency INTEGER NOT NULL,
                prompt_index INTEGER NOT NULL,
                server_id TEXT,
                input_tokens INTEGER,
                actual_input_tokens INTEGER,
                predicted_output_tokens INTEGER,
                actual_output_tokens INTEGER,
                queue_wait_seconds REAL,
                first_response_seconds REAL,
                completion_seconds REAL,
                failure_reason TEXT
            );
            CREATE INDEX IF NOT EXISTS benchmark_samples_run_idx ON benchmark_samples(run_id, policy, concurrency);
            CREATE TABLE IF NOT EXISTS benchmark_schedule (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                next_run_at REAL NOT NULL,
                last_error TEXT
            );
        ''')
        for table in ('real_requests', 'benchmark_samples'):
            columns = {column['name'] for column in db.execute(f'PRAGMA table_info({table})')}
            if 'routing_json' not in columns:
                db.execute(f'ALTER TABLE {table} ADD COLUMN routing_json TEXT')
        db.execute('''INSERT OR IGNORE INTO benchmark_schedule(id, next_run_at)
                      VALUES(1, ?)''', (time.time() + 7 * 24 * 3600,))


def routing_json(data):
    candidates = data.get('candidates')
    if not isinstance(candidates, list):
        return None
    rows = []
    for row in candidates:
        if not isinstance(row, dict) or row.get('server_id') not in GPU_SERVER_IDS:
            continue
        fields = {'server_id': row['server_id']}
        for key in ('healthy', 'eligible', 'balanced_preferred', 'selected'):
            if isinstance(row.get(key), bool):
                fields[key] = row[key]
        for key in ('inflight', 'running', 'waiting', 'kv_usage', 'estimated_seconds',
                    'estimated_service_seconds', 'estimated_queued_seconds', 'score', 'decision_at'):
            value = row.get(key)
            fields[key] = value if type(value) in (int, float) and math.isfinite(value) else None
        method = row.get('selection_method')
        if method in (*BENCHMARK_POLICIES, 'missing_estimate_round_robin'):
            fields['selection_method'] = method
        rows.append(fields)
    return json.dumps(rows, allow_nan=False) if rows else None


def first_content(data):
    if data.get('event') != 'chunk':
        return False
    return any((choice.get('delta') or {}).get('content') for choice in
               (data.get('vllm') or {}).get('choices', []))


def record_request(request_id, received_at, **fields):
    allowed = {
        'completed_at', 'input_tokens', 'predicted_output_tokens', 'actual_input_tokens',
        'actual_output_tokens', 'server_id', 'queue_wait_seconds', 'first_response_seconds',
        'completion_seconds', 'status', 'failure_reason', 'http_status', 'policy', 'routing_json',
    }
    values = {key: fields.get(key) for key in allowed}
    values['status'] = values['status'] or 'received'
    values['updated_at'] = time.time()
    columns = ['request_id', 'received_at', *values]
    params = [request_id, received_at, *values.values()]
    updates = ', '.join(
        f'{key}=COALESCE(excluded.{key}, real_requests.{key})'
        for key in values if key != 'status'
    ) + ', status=excluded.status'
    with monitor_db() as db:
        db.execute(
            f"INSERT INTO real_requests ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
            f'ON CONFLICT(request_id) DO UPDATE SET {updates}', params
        )


def save_resource_sample(sampled_at, servers, router_waiting, prune=False):
    rows = []
    for server_id in GPU_SERVER_IDS:
        server = servers.get(server_id) or {}
        rows.append((
            sampled_at, server_id, int(bool(server.get('available'))),
            server.get('cpu_pct'), server.get('memory_pct'), server.get('memory_available_bytes'),
            server.get('gpu_utilization_pct'), server.get('gpu_memory_used_mib'),
            server.get('gpu_memory_total_mib'), server.get('waiting_requests'),
            server.get('running_requests'), router_waiting,
        ))
    cutoff = sampled_at - RESOURCE_RETENTION_DAYS * 24 * 3600
    with monitor_db() as db:
        open_incidents = {r['server_id']: r['id'] for r in db.execute(
            'SELECT id, server_id FROM resource_incidents WHERE ended_at IS NULL')}
        for server_id in GPU_SERVER_IDS:
            server = servers.get(server_id) or {}
            total = server.get('gpu_memory_total_mib')
            used = server.get('gpu_memory_used_mib')
            waiting = server.get('waiting_requests') or 0
            pressure = bool(server.get('available')) and total is not None and total > 0 and used is not None and \
                       used / total >= .95 and waiting > 0
            previous_pressure = server_id in open_incidents
            if pressure and not previous_pressure:
                db.execute('INSERT INTO resource_incidents(server_id, started_at) VALUES (?, ?)',
                           (server_id, sampled_at))
            elif previous_pressure and not pressure:
                db.execute('UPDATE resource_incidents SET ended_at=? WHERE id=?',
                           (sampled_at, open_incidents[server_id]))
        db.executemany('''INSERT OR REPLACE INTO resource_samples(
            sampled_at, server_id, healthy, cpu_pct, memory_pct, memory_available_bytes,
            gpu_utilization_pct, gpu_memory_used_mib, gpu_memory_total_mib,
            waiting_requests, running_requests, router_waiting
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', rows)
        if prune:
            db.execute('DELETE FROM resource_samples WHERE sampled_at < ?', (cutoff,))
            db.execute('DELETE FROM resource_incidents WHERE ended_at IS NOT NULL AND ended_at < ?', (cutoff,))


def request_failure_reason(status, detail):
    text = str(detail or '').lower()
    if 'queue is full' in text or 'queue full' in text:
        return 'queue_overflow'
    if 'timed out waiting' in text or 'queue wait' in text:
        return 'queue_timeout'
    if status == 429:
        return 'request_limit'
    if status == 422:
        return 'invalid_or_over_limit_input'
    if status == 502:
        return 'upstream_error'
    if status == 503:
        return 'no_healthy_server'
    if status == 504:
        return 'request_timeout'
    return 'request_failed'


def recent_resource_samples(limit):
    with monitor_db() as db:
        rows = db.execute('''SELECT * FROM (
            SELECT * FROM resource_samples ORDER BY sampled_at DESC LIMIT ?
        ) ORDER BY sampled_at ASC, server_id ASC''', (limit * len(GPU_SERVER_IDS),)).fetchall()
    grouped = {}
    for row in rows:
        item = dict(row)
        point = grouped.setdefault(item['sampled_at'], {'time': round(item['sampled_at'] * 1000), 'servers': {}})
        point['servers'][item['server_id']] = {
            key: item[key] for key in (
                'healthy', 'cpu_pct', 'memory_pct', 'memory_available_bytes',
                'gpu_utilization_pct', 'gpu_memory_used_mib', 'gpu_memory_total_mib',
                'waiting_requests', 'running_requests', 'router_waiting'
            )
        }
    return list(grouped.values())


def recent_real_requests(limit):
    with monitor_db() as db:
        rows = db.execute('''SELECT request_id, received_at, completed_at, input_tokens,
            predicted_output_tokens, actual_input_tokens, actual_output_tokens, server_id,
            queue_wait_seconds, first_response_seconds, completion_seconds,
            status, failure_reason, http_status, policy, routing_json
            FROM real_requests ORDER BY received_at DESC LIMIT ?''', (limit,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        raw = item.pop('routing_json')
        item['routing_candidates'] = json.loads(raw) if raw else None
        result.append(item)
    return result


def monitor_summary(hours):
    checked_at = time.time()
    since = checked_at - hours * 3600
    with monitor_db() as db:
        rows = db.execute('''SELECT status, failure_reason, completion_seconds, first_response_seconds
                             FROM real_requests WHERE received_at >= ?''', (since,)).fetchall()
        resource_sample_count = db.execute('SELECT COUNT(*) FROM resource_samples WHERE sampled_at >= ?',
                                           (since,)).fetchone()[0]
        pressure_episodes = db.execute('SELECT COUNT(*) FROM resource_incidents WHERE started_at >= ?',
                                       (since,)).fetchone()[0]
    completed = [row for row in rows if row['status'] == 'completed']
    failed = [row for row in rows if row['status'] == 'failed']
    latencies = [row['completion_seconds'] for row in completed if row['completion_seconds'] is not None]
    late = sum(row['completion_seconds'] is not None and row['completion_seconds'] > 30 for row in completed)
    slow_first = sum(row['first_response_seconds'] is not None and row['first_response_seconds'] > 2 for row in rows)
    queue_overflow = sum(row['failure_reason'] == 'queue_overflow' for row in failed)
    queue_timeout = sum(row['failure_reason'] == 'queue_timeout' for row in failed)
    return {
        'hours': hours, 'checked_at': checked_at, 'sampled_requests': len(rows), 'total_requests': len(rows),
        'completed_requests': len(completed), 'failed_requests': len(failed),
        'average_latency_seconds': round(sum(latencies) / len(latencies), 3) if latencies else None,
        'failure_criteria': {
            'completion_over_30_seconds': late, 'queue_overflow': queue_overflow,
            'queue_timeout': queue_timeout, 'gpu_memory_backlog_episodes': pressure_episodes,
            'first_token_over_2_seconds': slow_first,
        },
        'incident_rules': INCIDENT_RULES,
        'resource_samples': resource_sample_count,
    }


def benchmark_report(limit=8):
    with monitor_db() as db:
        runs = db.execute('''SELECT id, started_at, completed_at, status, question_count,
                             order_first, error FROM benchmark_runs
                             ORDER BY started_at DESC LIMIT ?''', (limit,)).fetchall()
        schedule = db.execute('SELECT next_run_at, last_error FROM benchmark_schedule WHERE id=1').fetchone()
        result = []
        for run in runs:
            samples = db.execute('''SELECT policy, concurrency, prompt_index, server_id, input_tokens,
                    predicted_output_tokens, actual_output_tokens, queue_wait_seconds,
                    first_response_seconds, completion_seconds, failure_reason, routing_json
                FROM benchmark_samples WHERE run_id=? ORDER BY policy, concurrency, prompt_index''',
                (run['id'],)).fetchall()
            groups = {}
            for sample in samples:
                key = (sample['policy'], sample['concurrency'])
                groups.setdefault(key, []).append(dict(sample))
            grouped = []
            for (policy, concurrency), items in sorted(groups.items(), key=lambda pair:(pair[0][1],pair[0][0])):
                first = sorted(x['first_response_seconds'] for x in items if x['first_response_seconds'] is not None)
                complete = sorted(x['completion_seconds'] for x in items
                                  if x['completion_seconds'] is not None and not x['failure_reason'])
                p95 = first[max(0, int(len(first) * .95 + .999999) - 1)] if first else None
                p95_complete = complete[math.ceil(len(complete) * .95) - 1] if complete else None
                grouped.append({
                    'policy': policy, 'concurrency': concurrency, 'requests': len(items),
                    'completed': len(complete), 'failures': sum(bool(x['failure_reason']) for x in items),
                    'average_first_response_seconds': round(sum(first) / len(first), 3) if first else None,
                    'p95_first_response_seconds': round(p95, 3) if p95 is not None else None,
                    'average_completion_seconds': round(sum(complete) / len(complete), 3) if complete else None,
                    'p95_completion_seconds': round(p95_complete, 3) if p95_complete is not None else None,
                    'under_2s_count': sum(x <= 2 for x in first),
                    'fallback_count': (sum(any(row.get('selection_method') == 'missing_estimate_round_robin'
                                               for row in json.loads(x['routing_json'] or '[]')) for x in items)
                                       if policy == 'shortest_predicted' else None),
                })
            result.append({**dict(run), 'groups': grouped})
    return {
        'runs': result,
        'next_run_at': schedule['next_run_at'] if schedule else None,
        'last_error': schedule['last_error'] if schedule else None,
        'cadence_days': 7,
        'question_set_size': 9,
        'concurrency_levels': BENCHMARK_CONCURRENCY,
        'policies': BENCHMARK_POLICIES,
    }


BENCHMARK_QUESTIONS = [
    'GPU 서버는 AI 서비스에서 어떤 역할을 하나요?',
    '로드밸런서는 여러 서버에 요청을 어떻게 나누나요?',
    '입력 토큰과 출력 토큰의 차이를 설명해 주세요.',
    '서버 대기열이 길어지면 사용자는 어떤 영향을 받나요?',
    '첫 토큰 응답 시간은 무엇을 나타내나요?',
    'GPU 메모리 사용량을 모니터링해야 하는 이유는 무엇인가요?',
    'Round Robin 방식은 어떤 순서로 요청을 배정하나요?',
    'LinUCB는 서버 선택에서 어떤 정보를 활용하나요?',
    '서비스 운영자가 지연 시간을 줄이려면 어떤 지표를 살펴봐야 하나요?',
]


def create_benchmark_run(started_at, order_first):
    with monitor_db() as db:
        cursor = db.execute('''INSERT INTO benchmark_runs(started_at, status, question_count, order_first)
                               VALUES (?, 'running', ?, ?)''', (started_at, len(BENCHMARK_QUESTIONS), order_first))
        return cursor.lastrowid


def save_benchmark_sample(run_id, sample):
    with monitor_db() as db:
        db.execute('''INSERT INTO benchmark_samples(request_id, run_id, arrival_at, completed_at,
            policy, concurrency, prompt_index, server_id, input_tokens, actual_input_tokens,
            predicted_output_tokens, actual_output_tokens,
            queue_wait_seconds, first_response_seconds, completion_seconds, failure_reason, routing_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (
            sample['request_id'], run_id, sample['arrival_at'], sample.get('completed_at'),
            sample['policy'], sample['concurrency'], sample['prompt_index'],
            sample.get('server_id'), sample.get('input_tokens'), sample.get('actual_input_tokens'),
            sample.get('predicted_output_tokens'),
            sample.get('actual_output_tokens'), sample.get('queue_wait_seconds'),
            sample.get('first_response_seconds'), sample.get('completion_seconds'), sample.get('failure_reason'),
            sample.get('routing_json'),
        ))


def finish_benchmark_run(run_id, status, error=None):
    with monitor_db() as db:
        db.execute('''UPDATE benchmark_runs SET completed_at=?, status=?, error=? WHERE id=?''',
                   (time.time(), status, error, run_id))


def update_benchmark_schedule(next_run_at, error=None):
    with monitor_db() as db:
        db.execute('UPDATE benchmark_schedule SET next_run_at=?, last_error=? WHERE id=1',
                   (next_run_at, error))


def next_benchmark_slot(timestamp):
    """Use the same Sunday KST quiet window on all GPU hosts."""
    kst = timezone(timedelta(hours=9))
    now = datetime.fromtimestamp(timestamp, kst)
    start = (now + timedelta(days=(6 - now.weekday()) % 7)).replace(
        hour=7, minute=40, second=0, microsecond=0)
    if now < start:
        return start.timestamp()
    if now < start + timedelta(minutes=10):
        return timestamp
    return (start + timedelta(days=7)).timestamp()


async def run_policy_benchmark():
    started = time.time()
    offset = int(started // (7 * 24 * 3600)) % len(BENCHMARK_POLICIES)
    order = BENCHMARK_POLICIES[offset:] + BENCHMARK_POLICIES[:offset]
    run_id = await asyncio.to_thread(create_benchmark_run, started, order[0])
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            status_response = await client.get('http://127.0.0.1:18823/status')
            status_response.raise_for_status()
            initial = status_response.json()
            if not set(BENCHMARK_POLICIES).issubset(initial.get('benchmark_policies') or []):
                raise RuntimeError('라우터가 주간 비교에 필요한 세 정책을 지원하지 않습니다.')
            healthy = all((initial.get('servers') or {}).get(sid, {}).get('healthy') for sid in GPU_SERVER_IDS)
            idle = not any((initial.get('inflight') or {}).values()) and not (initial.get('queue') or {}).get('waiting')
            if not healthy or not idle:
                reason = '서버 3대가 모두 정상이고 유휴 상태일 때만 실행합니다.'
                await asyncio.to_thread(finish_benchmark_run, run_id, 'deferred', reason)
                await asyncio.to_thread(update_benchmark_schedule, next_benchmark_slot(time.time() + 180), reason)
                return

        async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
            async def one_request(policy, concurrency, prompt_index):
                prompt = BENCHMARK_QUESTIONS[prompt_index]
                sample = {'request_id': uuid.uuid4().hex, 'arrival_at': time.time(),
                          'policy': policy, 'concurrency': concurrency, 'prompt_index': prompt_index}
                request_started = time.monotonic()
                try:
                    async with client.stream('POST', 'http://127.0.0.1:18823/chat/stream', json={
                        'messages': [{'role': 'user', 'content': prompt}],
                        'policy_override': policy, 'benchmark': True,
                    }) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line:
                                continue
                            try:
                                event = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if event.get('event') == 'routed':
                                if event.get('policy') != policy:
                                    raise ValueError('라우터가 요청받은 비교 정책을 적용하지 않았습니다.')
                                sample.update(server_id=event.get('server_id'),
                                    input_tokens=event.get('input_tokens'),
                                    predicted_output_tokens=event.get('predicted_output_tokens'),
                                    queue_wait_seconds=event.get('queue_seconds'),
                                    routing_json=routing_json(event))
                            elif first_content(event) and 'first_response_seconds' not in sample:
                                sample['first_response_seconds'] = time.monotonic() - request_started
                            elif event.get('event') == 'complete':
                                sample.update(completed_at=time.time(),
                                    actual_input_tokens=event.get('actual_prompt_tokens'),
                                    actual_output_tokens=event.get('actual_output_tokens'),
                                    completion_seconds=time.monotonic() - request_started)
                            elif event.get('event') == 'error':
                                sample.update(completed_at=time.time(),
                                    completion_seconds=time.monotonic() - request_started,
                                    failure_reason=request_failure_reason(event.get('status'), event.get('detail')))
                except (httpx.HTTPError, ValueError, TypeError) as exc:
                    sample.update(completed_at=time.time(),
                        completion_seconds=time.monotonic() - request_started,
                        failure_reason=request_failure_reason(
                            exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else 502, str(exc)))
                if sample.get('actual_output_tokens') is None and not sample.get('failure_reason'):
                    sample.update(completed_at=time.time(),
                        failure_reason='stream_ended_without_completion',
                        completion_seconds=time.monotonic() - request_started)
                await asyncio.to_thread(save_benchmark_sample, run_id, sample)

            for policy in order:
                for concurrency in BENCHMARK_CONCURRENCY:
                    await asyncio.gather(*(one_request(policy, concurrency, i) for i in range(concurrency)))
                    # Keep test batches isolated and leave the live router idle before the next batch.
                    await asyncio.sleep(2)
        await asyncio.to_thread(finish_benchmark_run, run_id, 'completed')
        await asyncio.to_thread(update_benchmark_schedule, next_benchmark_slot(started + 86400))
    except Exception as exc:
        message = f'{type(exc).__name__}: {str(exc)[:300]}'
        await asyncio.to_thread(finish_benchmark_run, run_id, 'failed', message)
        await asyncio.to_thread(update_benchmark_schedule, next_benchmark_slot(time.time() + 180), message)


async def scheduled_benchmark():
    while True:
        await asyncio.sleep(60)
        with monitor_db() as db:
            row = db.execute('SELECT next_run_at FROM benchmark_schedule WHERE id=1').fetchone()
        now = time.time()
        if row and now >= row['next_run_at']:
            slot = next_benchmark_slot(now)
            if slot > now:
                await asyncio.to_thread(update_benchmark_schedule, slot)
            else:
                await run_policy_benchmark()


def replay_estimate(hours):
    since = time.time() - hours * 3600
    with monitor_db() as db:
        rows = db.execute('''SELECT received_at, input_tokens, actual_output_tokens, server_id,
                    queue_wait_seconds, first_response_seconds, completion_seconds
                FROM real_requests WHERE received_at >= ? AND status='completed'
                    AND input_tokens > 0 AND actual_output_tokens >= 0
                    AND server_id IS NOT NULL AND completion_seconds IS NOT NULL
                ORDER BY received_at''', (since,)).fetchall()
    requests = [dict(row) for row in rows]
    if len(requests) < 12:
        return {'available': False, 'request_count': len(requests),
                'reason': '실측 요청 12건 이상을 모으면 같은 요청 흐름으로 계산합니다.'}

    rates = {sid: {'prefill': [], 'decode': []} for sid in GPU_SERVER_IDS}
    for row in requests:
        sid = row['server_id']
        if sid not in rates or row['first_response_seconds'] is None:
            continue
        input_duration = max(.05, row['first_response_seconds'] - (row['queue_wait_seconds'] or 0))
        decode_duration = max(.05, row['completion_seconds'] - row['first_response_seconds'])
        rates[sid]['prefill'].append(row['input_tokens'] / input_duration)
        rates[sid]['decode'].append(row['actual_output_tokens'] / decode_duration)

    def median(values):
        values = sorted(value for value in values if value > 0)
        if not values:
            return None
        middle = len(values) // 2
        return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2

    speed = {}
    for sid, data in rates.items():
        prefill, decode = median(data['prefill']), median(data['decode'])
        if prefill and decode:
            speed[sid] = {'prefill': prefill, 'decode': decode, 'samples': len(data['prefill'])}
    if len(speed) < 3:
        return {'available': False, 'request_count': len(requests),
                'reason': '3개 서버 각각의 입력·생성 속도 표본을 모으는 중입니다.'}

    first_arrival = requests[0]['received_at']

    def simulate(server_count, policy):
        active_servers = list(GPU_SERVER_IDS[:server_count])
        available_at = {sid: first_arrival for sid in active_servers}
        next_rr = 0
        records = []
        for row in requests:
            arrival = row['received_at']
            service = {}
            prefill = {}
            for sid in active_servers:
                rates_for_server = speed[sid]
                p = row['input_tokens'] / rates_for_server['prefill']
                d = row['actual_output_tokens'] / rates_for_server['decode']
                prefill[sid] = p
                service[sid] = p + d
            if policy == 'round_robin':
                sid = active_servers[next_rr % len(active_servers)]
                next_rr += 1
            else:
                sid = min(active_servers, key=lambda key: (
                    max(0, available_at[key] - arrival) + service[key], key
                ))
            start = max(arrival, available_at[sid])
            first_response = start - arrival + prefill[sid]
            completion = start + service[sid] - arrival
            available_at[sid] = start + service[sid]
            records.append((first_response, completion))
        first_values = sorted(item[0] for item in records)
        complete_values = [item[1] for item in records]
        p95 = first_values[max(0, int(len(first_values) * .95 + .999999) - 1)]
        return {'p95_first_response_seconds': round(p95, 3),
                'average_completion_seconds': round(sum(complete_values) / len(complete_values), 3),
                'cumulative_completion_seconds': round(sum(complete_values), 3),
                'requests_over_2s_first_response': sum(value > 2 for value in first_values)}

    scenarios = {}
    required = {'round_robin': None, 'estimated_completion_cost': None}
    for policy in required:
        for count in (1, 2, 3):
            result = simulate(count, policy)
            scenarios[f'{policy}_{count}_servers'] = result
            if required[policy] is None and result['p95_first_response_seconds'] <= 2:
                required[policy] = count
    rr = scenarios['round_robin_3_servers']
    cost = scenarios['estimated_completion_cost_3_servers']
    return {
        'available': True, 'request_count': len(requests),
        'first_arrival_at': requests[0]['received_at'], 'last_arrival_at': requests[-1]['received_at'],
        'measured_rates_tokens_per_second': speed,
        'scenarios': scenarios, 'required_servers_for_p95_2s': required,
        'estimated_cumulative_delay_reduction_seconds': round(
            rr['cumulative_completion_seconds'] - cost['cumulative_completion_seconds'], 3),
        'estimated_first_token_violations_avoided': max(0,
            rr['requests_over_2s_first_response'] - cost['requests_over_2s_first_response']),
        'method': '실제 기록의 입력·출력 길이와 서버별 측정 처리 속도로 재생한 추정값. LinUCB 재현이나 A/B 실측이 아닙니다.',
    }


async def live_analysis_summary():
    if time.monotonic() < monitor_summary_cache['expires_at']:
        return monitor_summary_cache['value']
    async with monitor_summary_lock:
        if time.monotonic() >= monitor_summary_cache['expires_at']:
            monitor_summary_cache['value'] = await asyncio.to_thread(monitor_summary, 720)
            monitor_summary_cache['expires_at'] = time.monotonic() + ROUTER_POLL_SECONDS
        return monitor_summary_cache['value']


async def cached_monitor_analysis(hours, limit):
    now = time.monotonic()
    cached = monitor_analysis_cache['value']
    if cached is not None and now < monitor_analysis_cache['expires_at']:
        return cached
    async with monitor_analysis_lock:
        now = time.monotonic()
        cached = monitor_analysis_cache['value']
        if cached is not None and now < monitor_analysis_cache['expires_at']:
            return cached
        replay, benchmarks = await asyncio.gather(
            asyncio.to_thread(replay_estimate, hours),
            asyncio.to_thread(benchmark_report, limit),
        )
        value = {'replay': replay, 'benchmarks': benchmarks}
        monitor_analysis_cache['value'] = value
        monitor_analysis_cache['expires_at'] = time.monotonic() + MONITOR_ANALYSIS_CACHE_SECONDS
        return value


def parse_router_status(data, last_seen):
    servers = {}
    for server_id in ('ai', 'ai1', 'ai2'):
        info = data.get('servers', {}).get(server_id) or {}
        gpu = ((info.get('metrics') or {}).get('gpus') or [{}])[0]
        checked_at = info.get('checked_at')
        age = max(0, time.time() - checked_at) if isinstance(checked_at, (int, float)) else None
        available = bool(info.get('healthy')) and age is not None and age <= 30
        perf = info.get('vllm_performance') or {}
        work = (data.get('estimated_work') or {}).get(server_id)
        work = work if isinstance(work, list) else []
        progress = [min(99, max(0, round(100 * item['elapsed_seconds'] / item['estimated_service_seconds']))) for item in work
                    if isinstance(item.get('elapsed_seconds'), (int, float))
                    and isinstance(item.get('estimated_service_seconds'), (int, float))
                    and item['estimated_service_seconds'] > 0]
        if available:
            last_seen[server_id] = checked_at
        servers[server_id] = {
            'available': available,
            'age_seconds': round(age, 1) if age is not None else None,
            'cpu_pct': (info.get('metrics') or {}).get('cpu_pct') if available else None,
            'memory_pct': (info.get('metrics') or {}).get('memory_pct') if available else None,
            'memory_available_bytes': (info.get('metrics') or {}).get('memory_available_bytes') if available else None,
            'gpu_utilization_pct': gpu.get('utilization_pct') if available else None,
            'gpu_memory_used_mib': gpu.get('memory_used_mib') if available else None,
            'gpu_memory_total_mib': gpu.get('memory_total_mib') if available else None,
            'waiting_requests': (info.get('vllm') or {}).get('num_requests_waiting') if available else None,
            'running_requests': (info.get('vllm') or {}).get('num_requests_running') if available else None,
            'input_tokens_per_second': perf.get('uncached_input_tokens_per_second') if available else None,
            'output_tokens_per_second': perf.get('output_tokens_per_second') if available else None,
            'estimated_remaining_seconds': [w.get('estimated_remaining_seconds') for w in work] if available else None,
            'active_routed': len(work) if available else 0,
            'work_progress_pct': round(sum(progress)/len(progress)) if available and progress else None,
        }
    return {'servers': servers, 'router_waiting': (data.get('queue') or {}).get('waiting'),
            'shadow_linucb': data.get('shadow_linucb'), 'shadow_comparison': data.get('shadow_comparison'),
            'recent_experiments': data.get('recent_experiments') or [], 'gpu_activity': data.get('gpu_activity'),
            'prediction_error': data.get('prediction_error'), 'prediction_candidate': data.get('prediction_candidate'),
            'evaluation_cycles': data.get('evaluation_cycles')}


async def monitor_router(app):
    last_seen = {}
    history = deque(maxlen=60)
    prune_at = 0.0
    async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
        while True:
            started = asyncio.get_running_loop().time()
            try:
                result = await client.get('http://127.0.0.1:18823/status')
                result.raise_for_status()
                status = parse_router_status(result.json(), last_seen)
            except (httpx.HTTPError, ValueError, TypeError, AttributeError, IndexError):
                status = {'servers': {}, 'router_waiting': None}
            now = time.time()
            servers = status['servers']
            for sid in GPU_SERVER_IDS:
                row = servers.setdefault(sid, {'available': False})
                row['last_seen_at'] = last_seen.get(sid)
            history.append({'time': round(now * 1000), 'values': [
                servers[sid].get('gpu_utilization_pct') for sid in GPU_SERVER_IDS
            ]})
            app.state.router_status = {**status, 'sampled_at': now, 'history': list(history)}
            prune = started >= prune_at
            await asyncio.to_thread(save_resource_sample, now, servers, status.get('router_waiting'), prune)
            if prune:
                prune_at = started + 3600
            await asyncio.sleep(max(0, ROUTER_POLL_SECONDS - (asyncio.get_running_loop().time() - started)))


@asynccontextmanager
async def lifespan(app):
    await asyncio.to_thread(init_monitor_db)
    async def cleanup():
        while True:
            await asyncio.sleep(60)
            async with lock:
                now = time.monotonic()
                for key, visitor in list(sessions.items()):
                    if now - visitor.last_seen > SESSION_TTL:
                        del sessions[key]
    app.state.router_status = {'servers': {}, 'router_waiting': None, 'history': []}
    tasks = [asyncio.create_task(cleanup()), asyncio.create_task(monitor_router(app)),
             asyncio.create_task(scheduled_benchmark())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="AICAP simulation API", lifespan=lifespan)


class Control(BaseModel):
    action: str
    policy: str = "cost"
    speed: int | None = None
    traffic_mode: str | None = None
    prompt: str | None = Field(default=None, max_length=1000)
    hours: float | None = Field(default=None, ge=1, le=4320)
    limit: int | None = Field(default=None, ge=1, le=1000)


class Step(BaseModel):
    seconds: float = Field(default=1, gt=0, le=60)


@app.get("/api/state")
async def state(request: Request, response: Response):
    async with lock:
        return snapshot(get_session(request, response))


@app.post("/api/control")
async def control(body: Control, request: Request, response: Response):
    global real_last_request, real_inflight
    if body.action == 'monitor_dashboard':
        hours = min(24, body.hours or 24)
        limit = min(1000, body.limit or 60)
        result = await asyncio.gather(
            asyncio.to_thread(monitor_summary, hours),
            asyncio.to_thread(recent_resource_samples, limit),
            asyncio.to_thread(recent_real_requests, 12),
            cached_monitor_analysis(720, 8),
            live_analysis_summary(),
        )
        response.headers['Cache-Control'] = 'no-store'
        router = request.app.state.router_status
        comparison = router.get('shadow_comparison')
        recent = router.get('recent_experiments', [])
        if comparison and comparison.get('status') == 'running' and comparison.get('recent'):
            recent = comparison['recent']
        return {'summary': result[0], 'resources': result[1], 'requests': result[2],
                'analysis': {**result[3], 'summary': result[4]}, 'sample_interval_seconds': ROUTER_POLL_SECONDS,
                'shadow_linucb': request.app.state.router_status.get('shadow_linucb'),
                'shadow_comparison': request.app.state.router_status.get('shadow_comparison'),
                'recent_experiments': recent,
                'gpu_activity': router.get('gpu_activity'), 'prediction_error': router.get('prediction_error'),
                'prediction_candidate': router.get('prediction_candidate'), 'evaluation_cycles': router.get('evaluation_cycles')}
    if body.action == 'real_status':
        response.headers['Cache-Control'] = 'no-store'
        return request.app.state.router_status
    if body.action in ('real_chat', 'real_stream'):
        prompt = (body.prompt or '').strip()
        request_id = uuid.uuid4().hex
        received_at = time.time()
        started_at = time.monotonic()
        await asyncio.to_thread(record_request, request_id, received_at, status='received')
        if not prompt:
            await asyncio.to_thread(record_request, request_id, received_at, status='failed',
                                    failure_reason='empty_prompt', http_status=422,
                                    completion_seconds=time.monotonic() - started_at)
            raise HTTPException(422, '질문을 입력해 주세요.')
        async with lock:
            visitor = get_session(request, response)
            if real_inflight >= 3:
                await asyncio.to_thread(record_request, request_id, received_at, status='failed',
                                        failure_reason='request_limit', http_status=429,
                                        completion_seconds=time.monotonic() - started_at)
                raise HTTPException(429, '다른 실제 요청을 처리하고 있습니다. 잠시 후 다시 시도해 주세요.')
            while real_recent_requests and time.monotonic() - real_recent_requests[0] >= 3600:
                real_recent_requests.popleft()
            if len(real_recent_requests) >= REAL_HOURLY_LIMIT:
                await asyncio.to_thread(record_request, request_id, received_at, status='failed',
                                        failure_reason='hourly_request_limit', http_status=429,
                                        completion_seconds=time.monotonic() - started_at)
                raise HTTPException(429, f'전체 시간당 실제 요청 한도({REAL_HOURLY_LIMIT}건)에 도달했습니다. 잠시 후 다시 시도해 주세요.')
            real_inflight += 1
            visitor.real_last = time.monotonic()
            real_last_request = visitor.real_last
            real_recent_requests.append(real_last_request)
        if body.action == 'real_stream':
            async def events():
                terminal = False
                first_seen = False

                async def save_event(data):
                    nonlocal terminal, first_seen
                    kind = data.get('event')
                    fields = {}
                    if kind in ('queued', 'waiting'):
                        fields['status'] = 'queued' if kind == 'queued' else 'waiting'
                        if isinstance(data.get('input_tokens'), int):
                            fields['input_tokens'] = data['input_tokens']
                        if isinstance(data.get('predicted_output_tokens'), int):
                            fields['predicted_output_tokens'] = data['predicted_output_tokens']
                    elif kind == 'routed':
                        fields.update(status='routed', server_id=data.get('server_id'),
                                       policy=data.get('policy'), routing_json=routing_json(data))
                        for source, target in (('input_tokens', 'input_tokens'),
                                               ('predicted_output_tokens', 'predicted_output_tokens'),
                                               ('queue_seconds', 'queue_wait_seconds')):
                            if isinstance(data.get(source), (int, float)):
                                fields[target] = data[source]
                    elif not first_seen and first_content(data):
                        first_seen = True
                        fields.update(status='routed', first_response_seconds=time.monotonic() - started_at)
                    elif kind == 'complete':
                        fields.update(status='completed', completed_at=time.time(),
                                      completion_seconds=time.monotonic() - started_at)
                        for source, target in (('server_id', 'server_id'),
                                               ('actual_prompt_tokens', 'actual_input_tokens'),
                                               ('actual_output_tokens', 'actual_output_tokens'),
                                               ('queue_seconds', 'queue_wait_seconds')):
                            if isinstance(data.get(source), (int, float, str)):
                                fields[target] = data[source]
                    elif kind == 'error':
                        fields.update(status='failed', completed_at=time.time(),
                                      completion_seconds=time.monotonic() - started_at,
                                      http_status=data.get('status'),
                                      failure_reason=request_failure_reason(data.get('status'), data.get('detail')))
                    if fields:
                        await asyncio.to_thread(record_request, request_id, received_at, **fields)
                        terminal = kind in ('complete', 'error')

                try:
                    async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
                        async with client.stream('POST', 'http://127.0.0.1:18823/chat/stream', json={'messages':[{'role':'user','content':prompt}]}) as upstream:
                            upstream.raise_for_status()
                            async for line in upstream.aiter_lines():
                                if line:
                                    try:
                                        await save_event(json.loads(line))
                                    except (json.JSONDecodeError, TypeError, AttributeError):
                                        pass
                                    yield line + '\n'
                    if not terminal:
                        await asyncio.to_thread(record_request, request_id, received_at,
                            status='failed', failure_reason='stream_ended_without_completion',
                            completed_at=time.time(), completion_seconds=time.monotonic() - started_at)
                except asyncio.CancelledError:
                    if not terminal:
                        await asyncio.to_thread(record_request, request_id, received_at,
                            status='failed', failure_reason='client_disconnected',
                            completed_at=time.time(), completion_seconds=time.monotonic() - started_at)
                    raise
                except (httpx.HTTPError, ValueError) as exc:
                    status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else 502
                    detail = str(exc)
                    if not terminal:
                        await asyncio.to_thread(record_request, request_id, received_at,
                            status='failed', failure_reason=request_failure_reason(status_code, detail),
                            http_status=status_code, completed_at=time.time(),
                            completion_seconds=time.monotonic() - started_at)
                        terminal = True
                    yield json.dumps({'event':'error','detail':'실제 로드밸런서에 연결하지 못했습니다.'}, ensure_ascii=False) + '\n'
                finally:
                    global real_inflight
                    real_inflight -= 1
            return StreamingResponse(events(), media_type='application/x-ndjson', headers={
                'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no', 'X-Request-ID': request_id})
        try:
            async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
                result = await client.post('http://127.0.0.1:18823/chat', json={'messages':[{'role':'user','content':prompt}]})
                result.raise_for_status()
                data = result.json()
                usage = ((data.get('vllm_response') or {}).get('usage') or {})
                await asyncio.to_thread(record_request, request_id, received_at,
                    status='completed', completed_at=time.time(),
                    completion_seconds=time.monotonic() - started_at,
                    input_tokens=data.get('input_tokens'),
                    predicted_output_tokens=data.get('predicted_output_tokens'),
                    actual_input_tokens=data.get('actual_prompt_tokens'),
                    actual_output_tokens=usage.get('completion_tokens'),
                    server_id=data.get('server_id'), queue_wait_seconds=data.get('queue_seconds'),
                    policy=data.get('policy'), routing_json=routing_json(data))
                response.headers['X-Request-ID'] = request_id
                return data
        except (httpx.HTTPError, ValueError) as exc:
            status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else 502
            await asyncio.to_thread(record_request, request_id, received_at, status='failed',
                failure_reason=request_failure_reason(status_code, str(exc)), http_status=status_code,
                completed_at=time.time(), completion_seconds=time.monotonic() - started_at)
            raise HTTPException(502, '실제 로드밸런서에 연결하지 못했습니다.') from exc
        finally:
            real_inflight -= 1
    if body.action not in ("start", "pause", "reset", "speed") or body.policy not in ("cost", "round_robin"):
        raise HTTPException(400, "invalid action or policy")
    if body.speed is not None and body.speed not in (1, 10, 30, 60):
        raise HTTPException(400, 'speed must be 1, 10, 30 or 60')
    if body.traffic_mode not in (None, 'standard', 'original') or (body.traffic_mode is not None and body.action != 'reset'):
        raise HTTPException(400, 'traffic_mode requires reset and must be standard or original')
    async with lock:
        visitor = get_session(request, response)
        if body.speed is not None:
            visitor.speed = body.speed
        if body.action == "reset":
            visitor.run_id += 1
            visitor.traffic_mode = body.traffic_mode or visitor.traffic_mode
            visitor.simulator = new_simulator(body.policy, visitor.traffic_mode)
            visitor.running = False
        elif body.action != 'speed':
            visitor.running = body.action == "start"
        return snapshot(visitor)


@app.post("/api/step")
async def step(body: Step, request: Request, response: Response):
    async with lock:
        visitor = get_session(request, response)
        if visitor.running:
            raise HTTPException(409, "pause before stepping")
        visitor.simulator.advance(body.seconds)
        return snapshot(visitor)
