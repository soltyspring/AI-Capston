import asyncio
import hashlib
import os
import secrets
import time
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response
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


class Step(BaseModel):
    seconds: float = Field(default=1, gt=0, le=60)


@app.get("/api/state")
async def state(request: Request, response: Response):
    async with lock:
        return snapshot(get_session(request, response))


@app.post("/api/control")
async def control(body: Control, request: Request, response: Response):
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
