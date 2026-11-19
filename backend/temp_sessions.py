"""Postgres storage for private, expiring workbench sessions."""
import hashlib
import os
import re
import secrets
import uuid

import psycopg

COOKIE = 'gtfs_session'
SESSION_HOURS = 2
MAX_SESSIONS = 6
MAX_BYTES = 100 * 1024 * 1024
PART_BYTES = 3 * 1024 * 1024
MAX_STATE = 70 * 1024 * 1024
CREDENTIAL = re.compile(r'([0-9a-f]{32})\.([A-Za-z0-9_-]{43})')

def connect():
    connection = psycopg.connect(os.environ['GTFS_SESSION_DATABASE_URL'],connect_timeout=8,prepare_threshold=None)
    try:
        connection.execute("SET LOCAL statement_timeout = '25s'")
        connection.execute("SET LOCAL lock_timeout = '5s'")
    except Exception:
        connection.close()
        raise
    return connection

def digest(value):
    return hashlib.sha256(value.encode('ascii')).digest()

def clean(conn):
    conn.execute("""
      DELETE FROM transient_gtfs.sessions WHERE id IN (
        SELECT id FROM transient_gtfs.sessions WHERE expires_at <= now() FOR UPDATE SKIP LOCKED
      )
    """)

def start(conn):
    conn.execute('SELECT pg_advisory_xact_lock(17500838)')
    clean(conn)
    count = conn.execute('SELECT count(*) FROM transient_gtfs.sessions').fetchone()[0]
    if count >= MAX_SESSIONS:
        raise ValueError('The workbench is at capacity. Try again after a session ends.')
    key = uuid.uuid4()
    secret = secrets.token_urlsafe(32)
    expires, = conn.execute("""
      INSERT INTO transient_gtfs.sessions VALUES (%s,%s,now()+make_interval(hours=>%s))
      RETURNING expires_at
    """,(key,digest(secret),SESSION_HOURS)).fetchone()
    return f'{key.hex}.{secret}', expires.isoformat()

def authorize(conn, credential, lock=False):
    match = CREDENTIAL.fullmatch(credential or '')
    if match is None:
        return None
    suffix = ' FOR UPDATE' if lock else ''
    row = conn.execute("""
      SELECT id, expires_at FROM transient_gtfs.sessions
      WHERE id=%s AND token_hash=%s AND expires_at > clock_timestamp()
    """+suffix,(uuid.UUID(hex=match[1]),digest(match[2]))).fetchone()
    return row

def files(conn, session):
    return conn.execute("""
      SELECT name,content FROM transient_gtfs.files
      WHERE session_id=%s AND name NOT LIKE '@%%'
    """,(session,))

def put(conn, session, name, data):
    conn.execute("""
      INSERT INTO transient_gtfs.files VALUES (%s,%s,%s,%s)
      ON CONFLICT (session_id,name) DO UPDATE SET content=excluded.content,size=excluded.size
    """,(session,name,data,len(data)))

def discard(conn, session, name):
    conn.execute('DELETE FROM transient_gtfs.files WHERE session_id=%s AND name=%s',(session,name))

def check_size(conn, session):
    size, = conn.execute('SELECT coalesce(sum(size),0) FROM transient_gtfs.files WHERE session_id=%s',(session,)).fetchone()
    if size > MAX_STATE:
        raise ValueError('This feed and its drafts exceed the temporary storage limit. Try a smaller feed.')
