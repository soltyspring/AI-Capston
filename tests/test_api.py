import unittest
import os
from unittest.mock import patch
from types import SimpleNamespace
from fastapi.testclient import TestClient
with patch.dict(os.environ, {'AICAP_TRACE': 'data/demo.jsonl', 'AICAP_TRAIN': '0'}):
    from backend.api import app
from backend import api


class ApiTests(unittest.TestCase):
    def setUp(self):
        api.sessions.clear()

    def tearDown(self):
        api.sessions.clear()

    def test_controls(self):
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/control', json={'action': 'reset', 'traffic_mode':'original'}).status_code, 200)
            state = client.post('/api/step', json={'seconds': 1}).json()
            self.assertEqual(state['processed'], 4)
            self.assertEqual(client.get('/api/state').json()['source'], 'demo.jsonl')
            self.assertEqual(client.post('/api/control', json={'action': 'invalid'}).status_code, 400)
            self.assertEqual(client.post('/api/step', json={'seconds': -1}).status_code, 422)
            client.post('/api/control', json={'action': 'start'})
            self.assertEqual(client.post('/api/step', json={'seconds': 1}).status_code, 409)
            client.post('/api/control', json={'action': 'pause'})
            state = client.post('/api/control', json={'action': 'speed', 'speed': 30}).json()
            self.assertEqual(state['speed'], 30)
            self.assertEqual(state['dataset']['requests'], 8)
            self.assertEqual(client.post('/api/control', json={'action': 'speed', 'speed': 1000}).status_code, 400)

    def test_visitors_do_not_share_controls_or_state(self):
        now = [1000.0]
        with patch.object(api, 'time', SimpleNamespace(monotonic=lambda: now[0])):
            with TestClient(app) as alice, TestClient(app) as bob:
                a0 = alice.get('/api/state')
                b0 = bob.get('/api/state')
                self.assertNotEqual(alice.cookies.get(api.COOKIE), bob.cookies.get(api.COOKIE))
                self.assertIn('HttpOnly', a0.headers['set-cookie'])
                self.assertEqual(a0.headers['cache-control'], 'private, no-store')
                self.assertTrue(b0.json()['session']['isolated'])
                alice.post('/api/control', json={'action': 'speed', 'speed': 30})
                alice.post('/api/control', json={'action': 'start'})
                now[0] += .01
                a1 = alice.get('/api/state').json()
                b1 = bob.get('/api/state').json()
                self.assertTrue(a1['running'])
                self.assertAlmostEqual(a1['time'], a0.json()['time'] + .3)
                self.assertFalse(b1['running'])
                self.assertEqual(b1['speed'], 1)
                self.assertEqual(b1['processed'], 0)
                bob.post('/api/control', json={'action': 'reset', 'policy': 'round_robin'})
                self.assertEqual(alice.get('/api/state').json()['run_id'], 0)
                self.assertEqual(alice.get('/api/state').json()['policy'], 'cost')
                self.assertEqual(bob.get('/api/state').json()['policy'], 'round_robin')
                alice.post('/api/control', json={'action': 'pause'})
                paused = alice.get('/api/state').json()
                bob.post('/api/step', json={'seconds': 1})
                self.assertEqual(alice.get('/api/state').json()['time'], paused['time'])

    def test_expiry_capacity_and_untrusted_cookie(self):
        with TestClient(app) as alice, TestClient(app) as bob:
            alice.get('/api/state')
            token = alice.cookies.get(api.COOKIE)
            with patch.object(api, 'MAX_SESSIONS', 1):
                self.assertEqual(bob.get('/api/state').status_code, 503)
                self.assertEqual(alice.get('/api/state').status_code, 200)
            api.sessions[token].last_seen -= api.SESSION_TTL + 1
            response = alice.get('/api/state')
            self.assertEqual(response.json()['processed'], 0)
            self.assertNotEqual(token, alice.cookies.get(api.COOKIE))
            bob.cookies.set(api.COOKIE, 'attacker-chosen-token')
            bob.get('/api/state')
            self.assertNotIn('attacker-chosen-token', api.sessions)

    def test_https_session_cookie_is_secure(self):
        with TestClient(app, base_url='https://testserver') as client:
            response = client.get('/api/state')
            self.assertIn('Secure', response.headers['set-cookie'])

    def test_traffic_modes_and_metadata_are_isolated(self):
        with TestClient(app) as alice, TestClient(app) as bob:
            a0 = alice.get('/api/state').json()
            self.assertEqual(a0['traffic']['mode'], 'standard')
            self.assertGreater(a0['traffic']['arrival_interval_scale'], 1)
            self.assertEqual(a0['dataset']['replay_end_seconds'], api.replays['standard'][-1].timestamp / 1000)
            changed = alice.post('/api/control', json={'action':'reset','traffic_mode':'original'}).json()
            self.assertEqual(changed['traffic']['arrival_interval_scale'], 1)
            self.assertEqual(changed['run_id'], 1)
            self.assertEqual(bob.get('/api/state').json()['traffic']['mode'], 'standard')
            self.assertEqual(alice.post('/api/control',json={'action':'start','traffic_mode':'original'}).status_code,400)
            self.assertEqual(alice.post('/api/control',json={'action':'reset','traffic_mode':'bad'}).status_code,400)
            restored = alice.post('/api/control',json={'action':'reset','traffic_mode':'standard'}).json()
            self.assertEqual(restored['processed'], 0)
            self.assertEqual(restored['traffic']['mode'], 'standard')

