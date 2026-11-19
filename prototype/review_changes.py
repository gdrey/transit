"""Persistent choices on assessed changes."""
import json
import hashlib
import assessment_queue


def connect():
    db=assessment_queue.connect()
    db.execute('''CREATE TABLE IF NOT EXISTS review_decisions (batch_id INTEGER NOT NULL, entity_id TEXT NOT NULL, decision TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(batch_id,entity_id))''')
    return db


def latest():
    result=assessment_queue.latest()
    if not result: return None
    for row in result['items']:
        # Assessments drive the pipeline; old yes/no review choices no longer
        # override the result or accidentally include a current disruption.
        recommended=row['state']=='completed' and (row.get('assessment') or {}).get('status') in ('not_modeled','likely_not_modeled')
        row['decision']='yes' if recommended else None
        row['decision_source']='automatic' if recommended else None
    return result


def set_decision(batch_id,entity_id,decision):
    if decision not in ('yes','no'): raise ValueError('Choose yes model or no model.')
    data=assessment_queue.latest()
    if not data or data['id']!=batch_id or data['stale']: raise ValueError('The assessment or feed changed; reload before selecting changes.')
    row=next((x for x in data['items'] if x['item']['id']==entity_id),None)
    if not row or row['state']!='completed':
        raise ValueError('This alert does not have a completed assessment.')
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO review_decisions VALUES (?,?,?,?)',(batch_id,entity_id,decision,assessment_queue.now()))
    return {'decision':decision}


def selection_signature(data,selected):
    payload={'batch_id':data['id'],'feed_sha256':data['feed_sha256'],'selected':[{'id':r['item']['id'],'change_id':r['change_id'],'item':r['item'],'assessment':r.get('assessment')} for r in selected]}
    raw=json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    return hashlib.sha256(raw).hexdigest()
