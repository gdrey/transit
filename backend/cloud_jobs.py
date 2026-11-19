"""Transactional hosted job queue primitives; no credentials in job payloads."""
import json,uuid
from psycopg.types.json import Jsonb

ALLOWED_KINDS={'import_feed','apply_changes'}
# Never accept arbitrary dictionaries: LLM credentials belong only in request memory.
FIELDS={'import_feed':{'agency_preset_id','object_key','source_name'},'apply_changes':{'feed_id','change_ids'}}

def enqueue(conn,workspace,kind,payload):
    if kind not in ALLOWED_KINDS or not isinstance(payload,dict) or set(payload)-FIELDS[kind]:
        raise ValueError('Invalid job payload')
    if len(json.dumps(payload))>16384: raise ValueError('Job payload too large')
    job=uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO workbench.jobs(workspace_id,id,kind,status,payload) VALUES (%s,%s,%s,'queued',%s)",(workspace,job,kind,Jsonb(payload)))
    return str(job)

def claim(conn,kind):
    if kind not in ALLOWED_KINDS: raise ValueError('Unsupported job kind')
    with conn.cursor() as cur:
        cur.execute("""UPDATE workbench.jobs SET status='running',updated_at=now() WHERE (workspace_id,id)=(SELECT workspace_id,id FROM workbench.jobs WHERE status='queued' AND kind=%s ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING workspace_id,id,payload""",(kind,))
        return cur.fetchone()

def event(conn,workspace,job,message):
    if not isinstance(message,str) or not message.strip() or len(message)>500: raise ValueError('Invalid event')
    with conn.cursor() as cur:
        cur.execute('INSERT INTO workbench.job_events(workspace_id,job_id,message) VALUES (%s,%s,%s)',(workspace,job,message))

def finish(conn,workspace,job,success,error=None):
    if error is not None and (not isinstance(error,str) or len(error)>500): raise ValueError('Invalid job error')
    with conn.cursor() as cur:
        cur.execute("UPDATE workbench.jobs SET status=%s,error=%s,updated_at=now() WHERE workspace_id=%s AND id=%s AND status='running'",('completed' if success else 'failed',error,workspace,job))
        if cur.rowcount != 1: raise ValueError('Job is not running')
