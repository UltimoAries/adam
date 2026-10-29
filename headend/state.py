"""Local account, device identities and durable, expiring page deliveries."""
from contextlib import contextmanager
import hashlib
import hmac
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import uuid


class State:
    def __init__(self, directory, clock=time.time):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'fleet.sqlite3'
        self.clock = clock
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY, username TEXT, salt BLOB, digest BLOB);
                CREATE TABLE IF NOT EXISTS logins (token TEXT PRIMARY KEY, expires REAL);
                CREATE TABLE IF NOT EXISTS units (id TEXT PRIMARY KEY, name TEXT NOT NULL, token TEXT NOT NULL,
                    seen REAL DEFAULT 0, firmware TEXT DEFAULT '', wake_model TEXT DEFAULT 'unavailable');
                CREATE TABLE IF NOT EXISTS pages (id TEXT PRIMARY KEY, unit_id TEXT NOT NULL, kind TEXT NOT NULL,
                    text TEXT NOT NULL, pcm BLOB, created REAL, expires REAL, state TEXT, lease REAL DEFAULT 0,
                    detail TEXT DEFAULT '');
                CREATE INDEX IF NOT EXISTS pages_unit ON pages(unit_id,state,created);
            ''')

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def configured(self):
        with self.db() as db:
            return db.execute('SELECT 1 FROM account').fetchone() is not None

    def setup(self, username, password):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,40}', username):
            raise ValueError('Use 3–40 letters, numbers, dots, underscores or hyphens for the username')
        if not 12 <= len(password) <= 128:
            raise ValueError('Use a password of 12–128 characters')
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        with self.db() as db:
            if db.execute('SELECT 1 FROM account').fetchone():
                raise ValueError('An administrator account already exists')
            db.execute('INSERT INTO account VALUES (1,?,?,?)', (username,salt,digest))

    def login(self, username, password):
        with self.db() as db:
            account = db.execute('SELECT * FROM account').fetchone()
        salt = account['salt'] if account else b'0'*16
        candidate = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        if not account or not hmac.compare_digest(candidate,account['digest']) or username != account['username']:
            return None
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('DELETE FROM logins WHERE expires<=?',(self.clock(),))
            db.execute('INSERT INTO logins VALUES (?,?)',(hashlib.sha256(token.encode()).hexdigest(),self.clock()+43200))
        return token

    def authenticated(self, token):
        if not token:
            return False
        with self.db() as db:
            row = db.execute('SELECT expires FROM logins WHERE token=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return bool(row and row[0]>self.clock())

    def logout(self, token):
        with self.db() as db:
            db.execute('DELETE FROM logins WHERE token=?',(hashlib.sha256((token or '').encode()).hexdigest(),))

    def add_unit(self, name):
        name = name.strip()
        if not 1 <= len(name) <= 48:
            raise ValueError('Device name must contain 1–48 characters')
        token, unit_id = secrets.token_urlsafe(32), uuid.uuid4().hex[:16]
        with self.db() as db:
            if db.execute('SELECT count(*) FROM units').fetchone()[0] >= 32:
                raise ValueError('This prototype supports up to 32 registered units')
            db.execute('INSERT INTO units (id,name,token) VALUES (?,?,?)',
                       (unit_id,name,hashlib.sha256(token.encode()).hexdigest()))
        return dict(id=unit_id,name=name,token=token)

    def unit_auth(self, unit_id, token):
        with self.db() as db:
            row = db.execute('SELECT token FROM units WHERE id=?',(unit_id,)).fetchone()
        return bool(row and hmac.compare_digest(row[0],hashlib.sha256(token.encode()).hexdigest()))

    def units(self):
        with self.db() as db:
            rows = db.execute('SELECT id,name,seen,firmware,wake_model FROM units ORDER BY name').fetchall()
        return [dict(row,online=row['seen']>self.clock()-30) for row in rows]

    def rename(self, unit_id, name):
        if not 1 <= len(name.strip()) <= 48:
            raise ValueError('Device name must contain 1–48 characters')
        with self.db() as db:
            if not db.execute('UPDATE units SET name=? WHERE id=?',(name.strip(),unit_id)).rowcount:
                raise KeyError('Device not found')

    def heartbeat(self, unit_id, firmware, wake_model):
        with self.db() as db:
            db.execute('UPDATE units SET seen=?,firmware=?,wake_model=? WHERE id=?',
                       (self.clock(),firmware[:40],wake_model[:96],unit_id))

    def _expire(self, db):
        now = self.clock()
        db.execute("UPDATE pages SET state='expired',pcm=NULL WHERE state IN ('queued','offered') AND expires<=?",(now,))
        db.execute("UPDATE pages SET state='queued' WHERE state='offered' AND lease<? AND expires>?",(now,now))
        db.execute("UPDATE pages SET state='failed',detail='Playback confirmation timed out',pcm=NULL WHERE state='playing' AND lease<?",(now,))
        # Keep a bounded history without retaining terminal audio blobs.
        db.execute("DELETE FROM pages WHERE id IN (SELECT id FROM pages WHERE state IN ('delivered','expired','failed','cancelled') ORDER BY created DESC LIMIT -1 OFFSET 200)")

    def enqueue(self, unit_ids, pcm, kind, text=''):
        if kind not in ('text','voice') or not 0 < len(pcm) <= 480000 or len(pcm)%2:
            raise ValueError('Page audio must be 16 kHz mono PCM, up to 15 seconds')
        unit_ids = list(dict.fromkeys(unit_ids))
        if not 1 <= len(unit_ids) <= 32:
            raise ValueError('Select at least one device')
        result, now = [], self.clock()
        with self.db() as db:
            self._expire(db)
            total = db.execute('SELECT coalesce(sum(length(pcm)),0) FROM pages').fetchone()[0]
            if total+len(pcm)*len(unit_ids) > 16*1024*1024:
                raise ValueError('Paging queue is full; wait for pending messages to finish')
            for unit_id in unit_ids:
                if not db.execute('SELECT 1 FROM units WHERE id=?',(unit_id,)).fetchone():
                    raise ValueError('An unknown device was selected')
                if db.execute("SELECT count(*) FROM pages WHERE unit_id=? AND state IN ('queued','offered','playing')",(unit_id,)).fetchone()[0] >= 16:
                    raise ValueError('A selected device already has 16 pending pages')
                page_id = uuid.uuid4().hex
                db.execute('INSERT INTO pages (id,unit_id,kind,text,pcm,created,expires,state) VALUES (?,?,?,?,?,?,?,?)',
                           (page_id,unit_id,kind,text,pcm,now,now+300,'queued'))
                result.append(dict(id=page_id,unit_id=unit_id,state='queued',expires=now+300))
        return result

    def pages(self):
        with self.db() as db:
            self._expire(db)
            return [dict(row) for row in db.execute('SELECT p.id,p.unit_id,u.name,p.kind,p.text,p.created,p.expires,p.state,p.detail FROM pages p JOIN units u ON u.id=p.unit_id ORDER BY p.created DESC LIMIT 100')]

    def offer(self, unit_id):
        with self.db() as db:
            self._expire(db)
            if db.execute("SELECT 1 FROM pages WHERE unit_id=? AND state IN ('offered','playing')",(unit_id,)).fetchone():
                return None
            row = db.execute("SELECT id,length(pcm) AS bytes,expires FROM pages WHERE unit_id=? AND state='queued' ORDER BY created LIMIT 1",(unit_id,)).fetchone()
            if row:
                db.execute("UPDATE pages SET state='offered',lease=? WHERE id=?",(self.clock()+45,row['id']))
                return dict(row,type='page')

    def page_audio(self, unit_id, page_id):
        with self.db() as db:
            self._expire(db)
            row = db.execute("SELECT pcm FROM pages WHERE id=? AND unit_id=? AND state='offered'",(page_id,unit_id)).fetchone()
        if not row or not row[0]:
            raise KeyError('Page unavailable or expired')
        return bytes(row[0])

    def ack(self, unit_id, page_id, status, detail=''):
        with self.db() as db:
            self._expire(db)
            row = db.execute('SELECT state FROM pages WHERE id=? AND unit_id=?',(page_id,unit_id)).fetchone()
            if not row:
                raise KeyError('Page not found')
            if row[0] == status:
                return
            if (row[0],status) not in {('offered','playing'),('playing','delivered'),('playing','failed'),('offered','failed')}:
                raise ValueError('Invalid page state transition')
            db.execute('UPDATE pages SET state=?,detail=?,lease=?,pcm=CASE WHEN ? THEN NULL ELSE pcm END WHERE id=?',
                       (status,detail[:160],self.clock()+30,status in ('delivered','failed'),page_id))

    def cancel_page(self, page_id):
        with self.db() as db:
            if not db.execute("UPDATE pages SET state='cancelled',pcm=NULL WHERE id=? AND state IN ('queued','offered')",(page_id,)).rowcount:
                raise ValueError('Only a queued page can be cancelled')
