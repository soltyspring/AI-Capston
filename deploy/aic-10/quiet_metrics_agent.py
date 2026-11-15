"""Local host metrics with one in-process sample every three seconds."""
import asyncio
from contextlib import asynccontextmanager, suppress
import ctypes as c
from hmac import compare_digest
import os
import subprocess
import time

from fastapi import FastAPI, Header, HTTPException, Request
import psutil


class Utilization(c.Structure):
    _fields_ = [('gpu', c.c_uint), ('memory', c.c_uint)]


class Memory(c.Structure):
    _fields_ = [(field, c.c_ulonglong) for field in ('total', 'free', 'used')]


class GPUCollector:
    def __init__(self):
        self.lib = None
        self.handles = []
        self.retry_at = 0

    def direct(self):
        if self.lib is None:
            lib = c.CDLL('libnvidia-ml.so.1')
            if lib.nvmlInit_v2():
                raise OSError('NVML initialization failed')
            try:
                count = c.c_uint()
                if lib.nvmlDeviceGetCount_v2(c.byref(count)) or not count.value:
                    raise OSError('no GPU handles')
                handles = []
                for index in range(count.value):
                    handle = c.c_void_p()
                    if lib.nvmlDeviceGetHandleByIndex_v2(index, c.byref(handle)):
                        raise OSError('GPU handle unavailable')
                    handles.append(handle)
                self.lib, self.handles = lib, handles
            except OSError:
                lib.nvmlShutdown()
                raise
        rows = []
        for handle in self.handles:
            utilization, memory = Utilization(), Memory()
            if (self.lib.nvmlDeviceGetUtilizationRates(handle, c.byref(utilization))
                    or self.lib.nvmlDeviceGetMemoryInfo(handle, c.byref(memory))):
                raise OSError('GPU sample unavailable')
            rows.append({'utilization_pct': utilization.gpu, 'memory_used_mib': memory.used // 1048576,
                         'memory_total_mib': memory.total // 1048576})
        return rows

    def sample(self):
        if time.monotonic() >= self.retry_at:
            try:
                return self.direct(), 'nvml'
            except OSError:
                self.close()
                self.retry_at = time.monotonic() + 60
        try:
            result = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used,memory.total',
                                     '--format=csv,noheader,nounits'], capture_output=True, text=True,
                                    timeout=2, check=True)
            rows = []
            for line in result.stdout.splitlines():
                utilization, used, total = map(float, line.split(','))
                rows.append({'utilization_pct': utilization, 'memory_used_mib': used, 'memory_total_mib': total})
            return rows, 'nvidia-smi'
        except (OSError, subprocess.SubprocessError, ValueError):
            return [], 'unavailable'

    def close(self):
        if self.lib is not None:
            self.lib.nvmlShutdown()
            self.lib = None
            self.handles = []


@asynccontextmanager
async def lifespan(app):
    gpu = GPUCollector()
    app.state.snapshot = None

    def collect():
        cpu, memory = psutil.cpu_percent(interval=None), psutil.virtual_memory()
        devices, source = gpu.sample()
        return {'timestamp': time.time(), 'cpu_pct': cpu, 'memory_pct': memory.percent,
                'memory_available_bytes': memory.available, 'gpus': devices, 'gpu_source': source}

    # Keep one sampling thread; psutil's CPU interval is per thread.
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_running_loop()

    async def sample():
        while True:
            started = loop.time()
            app.state.snapshot = await loop.run_in_executor(pool, collect)
            await asyncio.sleep(max(0, 3 - (loop.time() - started)))

    task = asyncio.create_task(sample())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        pool.shutdown(wait=True)
        gpu.close()


app = FastAPI(lifespan=lifespan)


@app.get('/metrics')
async def metrics(request: Request, authorization: str | None = Header(default=None)):
    token = os.environ.get('METRICS_TOKEN')
    if token and not compare_digest(authorization or '', 'Bearer ' + token):
        raise HTTPException(401, 'Unauthorized')
    row = request.app.state.snapshot
    if row is None or time.time() - row['timestamp'] > 9:
        raise HTTPException(503, 'Host metrics unavailable')
    return row
