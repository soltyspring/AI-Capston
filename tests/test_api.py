import unittest
from fastapi.testclient import TestClient
from backend.api import app


class ApiTests(unittest.TestCase):
    def test_controls(self):
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/control', json={'action': 'reset'}).status_code, 200)
            state = client.post('/api/step', json={'seconds': 1}).json()
            self.assertEqual(state['processed'], 4)
            self.assertEqual(client.get('/api/state').json()['source'], 'demo.jsonl')
            self.assertEqual(client.post('/api/control', json={'action': 'invalid'}).status_code, 400)
            self.assertEqual(client.post('/api/step', json={'seconds': -1}).status_code, 422)
            client.post('/api/control', json={'action': 'start'})
            self.assertEqual(client.post('/api/step', json={'seconds': 1}).status_code, 409)
            client.post('/api/control', json={'action': 'pause'})

