"""Download a pinned trace, verify integrity, never fall back to mock data."""
import hashlib
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen
from backend.core import load_trace

REVISION = '4a760599edaa5ddf4d64c71b24bdf1760b4a2001'
URL = f'https://raw.githubusercontent.com/kvcache-ai/Mooncake/{REVISION}/FAST25-release/traces/conversation_trace.jsonl'
SHA256 = 'b8cbb061a85206d729d91cdc2981f43c9e0d99209dce588d3af5f7934408b9df'
DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'data/conversation_trace.jsonl'


def ensure_mooncake(path=DEFAULT_PATH):
    path = Path(path)
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != SHA256:
            raise ValueError(f'Mooncake checksum mismatch: {path}; existing file was not overwritten')
        load_trace(path)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with urlopen(URL, timeout=30) as response:
            content = response.read(10_000_001)
        if len(content) > 10_000_000 or hashlib.sha256(content).hexdigest() != SHA256:
            raise ValueError('Downloaded trace failed size/checksum validation')
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.download', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        load_trace(temporary)
        os.replace(temporary, path)
        return path
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    result = ensure_mooncake()
    print(f'Mooncake ready: {result} ({len(load_trace(result))} requests, SHA256 {SHA256})')
