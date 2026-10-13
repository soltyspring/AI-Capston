import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from backend.core import Predictor, Simulator, load_trace

ROOT = Path(__file__).resolve().parents[1]
trace_path = Path(os.environ.get('AICAP_TRACE', ROOT / 'data/demo.jsonl'))
rows = load_trace(trace_path)
predictor = Predictor()
if os.environ.get('AICAP_TRAIN') == '1':
    if len(rows) < 20:
        raise ValueError('at least 20 requests required for chronological training split')
    predictor.fit(rows[:int(len(rows) * .7)])
    rows = rows[int(len(rows) * .85):]
simulator = Simulator(rows, predictor)
running = False
lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app):
    async def clock():
        global running
        while True:
            await asyncio.sleep(0.25)
            async with lock:
                if running:
                    simulator.advance(0.25)
                    if simulator.snapshot()["finished"]:
                        running = False
    task = asyncio.create_task(clock())
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


class Step(BaseModel):
    seconds: float = Field(default=1, gt=0, le=60)


@app.get("/api/state")
async def state():
    async with lock:
        return {**simulator.snapshot(), "running": running, "source": trace_path.name}


@app.post("/api/control")
async def control(body: Control):
    global simulator, running
    if body.action not in ("start", "pause", "reset") or body.policy not in ("cost", "round_robin"):
        raise HTTPException(400, "invalid action or policy")
    async with lock:
        if body.action == "reset":
            simulator = Simulator(rows, predictor, policy=body.policy)
            running = False
        else:
            running = body.action == "start"
        return {**simulator.snapshot(), "running": running}


@app.post("/api/step")
async def step(body: Step):
    async with lock:
        if running:
            raise HTTPException(409, "pause before stepping")
        simulator.advance(body.seconds)
        return {**simulator.snapshot(), "running": running}
