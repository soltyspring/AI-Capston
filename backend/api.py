import asyncio
import hashlib
import os
import secrets
import time
import httpx
import json
from collections import deque
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
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


@asynccontextmanager
async def lifespan(app):
    async def cleanup():
        while True:
            await asyncio.sleep(60)
            async with lock:
                now = time.monotonic()
                for key, visitor in list(sessions.items()):
                    if now - visitor.last_seen > SESSION_TTL:
                        del sessions[key]
    task = asyncio.create_task(cleanup())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="AICAP simulation API", lifespan=lifespan)


class Control(BaseModel):
    action: str
    policy: str = "cost"
    speed: int | None = None
    traffic_mode: str | None = None
    prompt: str | None = Field(default=None, max_length=1000)


class Step(BaseModel):
    seconds: float = Field(default=1, gt=0, le=60)


@app.get("/api/state")
async def state(request: Request, response: Response):
    async with lock:
        return snapshot(get_session(request, response))


@app.post("/api/control")
async def control(body: Control, request: Request, response: Response):
    global real_last_request, real_inflight
    if body.action == 'real_status':
        response.headers['Cache-Control'] = 'no-store'
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                result = await client.get('http://127.0.0.1:18823/status')
                result.raise_for_status()
                data = result.json()
            servers = {}
            for server_id in ('ai', 'ai1', 'ai2'):
                info = data.get('servers', {}).get(server_id) or {}
                gpu = ((info.get('metrics') or {}).get('gpus') or [{}])[0]
                checked_at = info.get('checked_at')
                age = max(0, time.time() - checked_at) if isinstance(checked_at, (int, float)) else None
                available = bool(info.get('healthy')) and age is not None and age <= 30
                perf = info.get('vllm_performance') or {}
                work = (data.get('estimated_work') or {}).get(server_id)
                servers[server_id] = {
                    'available': available,
                    'age_seconds': round(age, 1) if age is not None else None,
                    'gpu_utilization_pct': gpu.get('utilization_pct') if available else None,
                    'gpu_memory_used_mib': gpu.get('memory_used_mib') if available else None,
                    'gpu_memory_total_mib': gpu.get('memory_total_mib') if available else None,
                    'waiting_requests': (info.get('vllm') or {}).get('num_requests_waiting') if available else None,
                    'input_tokens_per_second': perf.get('uncached_input_tokens_per_second') if available else None,
                    'output_tokens_per_second': perf.get('output_tokens_per_second') if available else None,
                    'estimated_remaining_seconds': [w.get('estimated_remaining_seconds') for w in work] if available and isinstance(work, list) else None,
                }
            return {'servers': servers, 'router_waiting': (data.get('queue') or {}).get('waiting')}
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, IndexError) as exc:
            raise HTTPException(502, '서버 상태를 불러오지 못했습니다.') from exc
    if body.action in ('real_chat', 'real_stream'):
        prompt = (body.prompt or '').strip()
        if not prompt:
            raise HTTPException(422, '질문을 입력해 주세요.')
        async with lock:
            visitor = get_session(request, response)
            if real_inflight >= 3:
                raise HTTPException(429, '다른 실제 요청을 처리하고 있습니다. 잠시 후 다시 시도해 주세요.')
            while real_recent_requests and time.monotonic() - real_recent_requests[0] >= 3600:
                real_recent_requests.popleft()
            if len(real_recent_requests) >= REAL_HOURLY_LIMIT:
                raise HTTPException(429, f'전체 시간당 실제 요청 한도({REAL_HOURLY_LIMIT}건)에 도달했습니다. 잠시 후 다시 시도해 주세요.')
            real_inflight += 1
            visitor.real_last = time.monotonic()
            real_last_request = visitor.real_last
            real_recent_requests.append(real_last_request)
        if body.action == 'real_stream':
            async def events():
                try:
                    async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
                        async with client.stream('POST', 'http://127.0.0.1:18823/chat/stream', json={'messages':[{'role':'user','content':prompt}]}) as upstream:
                            upstream.raise_for_status()
                            async for line in upstream.aiter_lines():
                                if line:
                                    yield line + '\n'
                except (httpx.HTTPError, ValueError):
                    yield json.dumps({'event':'error','detail':'실제 로드밸런서에 연결하지 못했습니다.'}, ensure_ascii=False) + '\n'
                finally:
                    global real_inflight
                    real_inflight -= 1
            return StreamingResponse(events(), media_type='application/x-ndjson', headers={'Cache-Control':'no-store', 'X-Accel-Buffering':'no'})
        try:
            async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
                result = await client.post('http://127.0.0.1:18823/chat', json={'messages':[{'role':'user','content':prompt}]})
                result.raise_for_status()
                return result.json()
        except (httpx.HTTPError, ValueError) as exc:
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
