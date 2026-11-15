"""Union of intervals in which the router had work at a GPU server."""
import sqlite3
import time
from pathlib import Path


class GpuActivity:
    def __init__(self, path):
        self.db = sqlite3.connect(Path(path), timeout=1)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS busy (id INTEGER PRIMARY KEY, started REAL, observed REAL)')
        self.db.execute('CREATE INDEX IF NOT EXISTS busy_observed ON busy(observed)')
        self.count = 0
        self.interval = None
        self.last_write = 0

    def begin(self):
        now = time.time()
        if not self.count:
            with self.db:
                self.interval = self.db.execute('INSERT INTO busy(started, observed) VALUES (?,?)', (now,now)).lastrowid
            self.last_write = now
        self.count += 1

    def end(self):
        self.count -= 1
        if not self.count and self.interval is not None:
            with self.db:
                self.db.execute('UPDATE busy SET observed=? WHERE id=?', (time.time(),self.interval))
            self.interval = None

    def report(self):
        now = time.time()
        if self.interval is not None and now-self.last_write>=3:
            with self.db:
                self.db.execute('UPDATE busy SET observed=? WHERE id=?', (now,self.interval))
            self.last_write = now
        values = self.db.execute('''SELECT MIN(started),COALESCE(SUM(observed-started),0),
             COALESCE(SUM(CASE WHEN observed > ? THEN observed-MAX(started,?) ELSE 0 END),0)
             FROM busy''',(now-86400,now-86400)).fetchone()
        tail = max(0, now-self.last_write) if self.interval is not None else 0
        return {'since':values[0], 'total_seconds':round(values[1]+tail,2),
                'last_24h_seconds':round(values[2]+tail,2),'active':self.count>0}
