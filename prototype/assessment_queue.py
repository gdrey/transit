"""Persistent, serial assessment of alert snapshots against one schedule version."""
import datetime
import hashlib
import json
import os
import sqlite3
import threading
import traceback
import assistant_bridge
import alert_triage
import realtime_alerts
import workbench

DB=os.path.join(workbench.ROOT,'.data','alerts.sqlite')
LOCK=threading.Lock()
ASSISTANT_LOCK=threading.Lock()
SERVICES={}
SERVICE_LOCK=threading.Lock()


def connect():
    os.makedirs(os.path.dirname(DB),exist_ok=True)
    db=sqlite3.connect(DB,timeout=30)
    db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS alert_snapshots (id INTEGER PRIMARY KEY, source TEXT NOT NULL, fetched_at TEXT NOT NULL, feed_timestamp INTEGER, sha256 TEXT NOT NULL, protobuf BLOB NOT NULL);
    CREATE TABLE IF NOT EXISTS alert_observations (snapshot_id INTEGER NOT NULL, entity_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(snapshot_id, entity_id));
    CREATE TABLE IF NOT EXISTS change_ids (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, entity_id TEXT NOT NULL, UNIQUE(source,entity_id));
    CREATE TABLE IF NOT EXISTS assessment_batches (id INTEGER PRIMARY KEY, snapshot_id INTEGER NOT NULL, feed_sha256 TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS assessments (batch_id INTEGER NOT NULL, entity_id TEXT NOT NULL, state TEXT NOT NULL, result TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(batch_id,entity_id));
    CREATE TABLE IF NOT EXISTS alert_clarifications (id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL, entity_id TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL, previous_result TEXT);''')
    return db


def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()


def feed_hash():
    with workbench.connect() as db:
        row=db.execute('select source from metadata').fetchone()
        if not row: raise ValueError('No GTFS Schedule is loaded.')
        path=row[0]
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def create(snapshot_id):
    try: digest=feed_hash()
    except (ValueError,OSError): digest=None
    with connect() as db:
        batch=db.execute('INSERT INTO assessment_batches(snapshot_id,feed_sha256,created_at) VALUES (?,?,?)',(snapshot_id,digest,now())).lastrowid
        rows=db.execute('SELECT entity_id FROM alert_observations WHERE snapshot_id=? ORDER BY rowid',(snapshot_id,)).fetchall()
        source=db.execute('SELECT source FROM alert_snapshots WHERE id=?',(snapshot_id,)).fetchone()[0]
        db.executemany('INSERT OR IGNORE INTO change_ids(source,entity_id) VALUES (?,?)',[(source,r['entity_id']) for r in rows])
        db.executemany('INSERT INTO assessments VALUES (?,?,?,?,?)',[(batch,r['entity_id'],'not_checked',None,now()) for r in rows])
    return batch


def latest():
    with connect() as db:
        row=db.execute('SELECT b.id,b.snapshot_id,b.feed_sha256,b.created_at,s.source,s.fetched_at FROM assessment_batches b JOIN alert_snapshots s ON s.id=b.snapshot_id ORDER BY b.id DESC LIMIT 1').fetchone()
        if not row: return None
        result=dict(row)
        rows=db.execute('''SELECT o.data,a.state,a.result,c.id AS change_id FROM alert_observations o JOIN assessments a ON a.entity_id=o.entity_id AND a.batch_id=? JOIN alert_snapshots s ON s.id=o.snapshot_id LEFT JOIN change_ids c ON c.source=s.source AND c.entity_id=o.entity_id WHERE o.snapshot_id=? ORDER BY o.rowid''',(row['id'],row['snapshot_id'])).fetchall()
        result['items']=[{'item':json.loads(x['data']),'change_id':'Change ID '+str(x['change_id']),'retrieved_at':row['fetched_at'],'state':x['state'],'assessment':json.loads(x['result']) if x['result'] else None,'triage':(json.loads(x['result']).get('triage') if x['result'] else None) or alert_triage.classify(json.loads(x['data']))} for x in rows]
        clarifications={}
        for c in db.execute('SELECT entity_id,text,created_at FROM alert_clarifications WHERE batch_id=? ORDER BY id',(row['id'],)):
            clarifications.setdefault(c['entity_id'],[]).append({'text':c['text'],'created_at':c['created_at']})
        for item in result['items']:item['clarifications']=clarifications.get(item['item']['id'],[])
    try: result['current_feed_sha256']=feed_hash()
    except (ValueError,OSError): result['current_feed_sha256']=None
    result['stale']=result['feed_sha256']!=result['current_feed_sha256']
    return result


def recheck_previous_feed(service=None):
    previous=latest()
    if not previous or not previous['stale'] or not service:return 0
    batch=create(previous['snapshot_id'])
    ids=[x['item']['id'] for x in previous['items'] if x['state'] in ('completed','failed','queued','running')]
    with connect() as db:
        db.executemany("UPDATE assessments SET state='queued',updated_at=? WHERE batch_id=? AND entity_id=?",[(now(),batch,id) for id in ids])
        db.execute("INSERT INTO alert_clarifications(batch_id,entity_id,text,created_at,previous_result) SELECT ?,entity_id,text,created_at,previous_result FROM alert_clarifications WHERE batch_id=? ORDER BY id",(batch,previous['id']))
    if ids:
        with SERVICE_LOCK:SERVICES[batch]=service
        start()
    return len(ids)


def start():
    if not LOCK.acquire(blocking=False): return
    threading.Thread(target=worker,daemon=True,name='alert-assessments').start()


def worker():
    try:
        with connect() as db: db.execute("UPDATE assessments SET state='queued',updated_at=? WHERE state='running'",(now(),))
        while True:
            with connect() as db:
                row=db.execute('''SELECT a.batch_id,a.entity_id,o.data,b.feed_sha256 FROM assessments a JOIN assessment_batches b ON b.id=a.batch_id JOIN alert_observations o ON o.snapshot_id=b.snapshot_id AND o.entity_id=a.entity_id WHERE a.state='queued' AND a.batch_id=(SELECT MAX(id) FROM assessment_batches) ORDER BY a.rowid LIMIT 1''').fetchone()
                if not row: break
                db.execute("UPDATE assessments SET state='running',updated_at=? WHERE batch_id=? AND entity_id=?",(now(),row['batch_id'],row['entity_id']))
            item=json.loads(row['data'])
            with SERVICE_LOCK:service=SERVICES.get(row['batch_id'])
            with connect() as db:
                clarifications=[r['text'] for r in db.execute('SELECT text FROM alert_clarifications WHERE batch_id=? AND entity_id=? ORDER BY id',(row['batch_id'],row['entity_id']))]
            try:
                triage=alert_triage.classify(item)
                if realtime_alerts.classify(item['text'])=='no_schedule_modeling_needed':
                    result={'status':'no_schedule_modeling_needed','interpretation':{'reason':'The notice describes station facilities and does not describe a schedule change.'},'plan':[],'method':'Conservative facility notice classification'}
                elif triage['recommendation']=='not_recommended':
                    result={'status':'not_recommended_current_disruption','interpretation':{'reason':triage['reason']},'plan':[],'method':'Source-aware conservative timing triage'}
                elif not row['feed_sha256']:
                    result={'status':'needs_clarification','interpretation':{'reason':'No GTFS Schedule was loaded for this assessment. Import the matching schedule and reassess.'}}
                elif feed_hash()!=row['feed_sha256']:
                    result={'status':'needs_clarification','interpretation':{'reason':'The loaded GTFS Schedule changed during this batch. Reassess against the current schedule.'}}
                else:
                    with ASSISTANT_LOCK:
                        result=assistant_bridge.interpret(item['text'],item['source'],{**{k:item.get(k) for k in ('human_period','active_periods','informed_entities','alert_type')},'user_clarifications':clarifications},service=service)
                result['triage']=triage
                result['user_clarifications']=clarifications
                state='completed'
            except Exception as exc:
                state='failed';result={'status':'assessment_failed','interpretation':{'reason':str(exc) or type(exc).__name__}}
            with connect() as db:
                db.execute('UPDATE assessments SET state=?,result=?,updated_at=? WHERE batch_id=? AND entity_id=?',(state,json.dumps(result,ensure_ascii=False),now(),row['batch_id'],row['entity_id']))
    finally:
        LOCK.release()
        # A new batch can arrive just as the worker observes an empty queue.
        with connect() as db: remaining=db.execute("SELECT 1 FROM assessments WHERE state='queued' AND batch_id=(SELECT MAX(id) FROM assessment_batches) LIMIT 1").fetchone()
        with SERVICE_LOCK:
            if not remaining:SERVICES.clear()
        if remaining: start()


def resume_or_start_latest():
    with connect() as db:
        batch=db.execute('SELECT 1 FROM assessment_batches LIMIT 1').fetchone()
        snapshot=db.execute('SELECT id FROM alert_snapshots ORDER BY id DESC LIMIT 1').fetchone()
    if not batch and snapshot:create(snapshot['id'])
    with connect() as db:db.execute("UPDATE assessments SET state='queued',updated_at=? WHERE state='running'",(now(),))


def clarify(batch_id,entity_id,text,service=None):
    if not service:raise ValueError('Connect an AI service before reassessing this alert.')
    if not isinstance(text,str) or not text.strip() or len(text.strip())>4000:raise ValueError('Enter a clarification of up to 4,000 characters.')
    if not isinstance(batch_id,int) or not isinstance(entity_id,str):raise ValueError('Invalid alert selection.')
    current=latest()
    if not current or current['id']!=batch_id or current['stale']:raise ValueError('The alert or feed changed; refresh before adding clarification.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        newest=db.execute('SELECT MAX(id) FROM assessment_batches').fetchone()[0]
        row=db.execute('SELECT state,result FROM assessments WHERE batch_id=? AND entity_id=?',(batch_id,entity_id)).fetchone()
        if newest!=batch_id or not row:raise ValueError('Alert not found in the current batch.')
        if row['state'] in ('queued','running'):raise ValueError('This alert is already being assessed; wait for it to finish.')
        db.execute('INSERT INTO alert_clarifications(batch_id,entity_id,text,created_at,previous_result) VALUES (?,?,?,?,?)',(batch_id,entity_id,text.strip(),now(),row['result']))
        db.execute("UPDATE assessments SET state='queued',result=NULL,updated_at=? WHERE batch_id=? AND entity_id=?",(now(),batch_id,entity_id))
    with SERVICE_LOCK:SERVICES[batch_id]=service
    start()
    return {'state':'queued','message':'Clarification saved; reassessing this alert.'}


def trigger(limit,service=None):
    if not service:raise ValueError('Connect an AI service before assessing alerts.')
    if limit not in (10,20): raise ValueError('Choose 10 or 20 alerts.')
    data=latest()
    if not data: raise ValueError('Import alerts before starting an assessment.')
    if data['stale']: raise ValueError('The loaded schedule has changed; start a new assessment against the current feed.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        batch=data['id']
        active=db.execute("SELECT COUNT(*) FROM assessments WHERE batch_id=? AND state IN ('queued','running')",(batch,)).fetchone()[0]
        if active:
            with SERVICE_LOCK:SERVICES[batch]=service
            start()
            return {'queued':0,'active':active,'message':'Resumed or continued the current assessment.'}
        rows=db.execute("SELECT entity_id FROM assessments WHERE batch_id=? AND state='not_checked' ORDER BY rowid LIMIT ?",(batch,limit)).fetchall()
        db.executemany("UPDATE assessments SET state='queued',updated_at=? WHERE batch_id=? AND entity_id=? AND state='not_checked'",[(now(),batch,r['entity_id']) for r in rows])
    if rows:
        with SERVICE_LOCK:SERVICES[batch]=service
        start()
    return {'queued':len(rows),'active':len(rows),'message':('Started assessment of '+str(len(rows))+' alerts.' if rows else 'No unchecked alerts remain.') }
