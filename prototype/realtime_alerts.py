"""Decode GTFS Realtime alerts, including the MTA Mercury alert extension."""
import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '.deps'))
try:
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
    from google.transit import gtfs_realtime_pb2 as gtfs
except ImportError as exc:
    raise ImportError('Install dependencies with: python3 -m pip install --target .deps -r requirements.txt') from exc

# MTA's Mercury schema extends the standard Alert at field 1001. Fields we do
# not interpret remain available in the original protobuf snapshot.
proto = descriptor_pb2.FileDescriptorProto(name='workbench_mercury.proto', package='transit_realtime', syntax='proto2')
proto.dependency.append(gtfs.DESCRIPTOR.name)
message = proto.message_type.add(name='WorkbenchMercuryAlert')
for name, number, field_type, type_name in [
    ('created_at', 1, 4, ''), ('updated_at', 2, 4, ''), ('alert_type', 3, 9, ''),
    ('human_readable_active_period', 8, 11, '.transit_realtime.TranslatedString'),
]: 
    field = message.field.add(name=name, number=number, label=1, type=field_type)
    if type_name: field.type_name = type_name
extension = proto.extension.add(name='workbench_mercury_alert', number=1001, label=1, type=11,
                                extendee='.transit_realtime.Alert', type_name='.transit_realtime.WorkbenchMercuryAlert')
pool = descriptor_pool.Default()
try: pool.AddSerializedFile(proto.SerializeToString())
except TypeError: pass
MERCURY = pool.FindExtensionByName('transit_realtime.workbench_mercury_alert')
message_factory.MessageFactory(pool).GetPrototype(pool.FindMessageTypeByName('transit_realtime.WorkbenchMercuryAlert'))
gtfs.Alert.RegisterExtension(MERCURY)


def english(value):
    translations = list(value.translation)
    selected = next((t.text for t in translations if t.language.lower() == 'en'), None)
    return selected if selected is not None else (translations[0].text if translations else '')


def classify(text):
    """Only obvious, exclusively facility notices get an automatic no-model result."""
    first_line = text.split('\n', 1)[0].lower()
    text = re.sub(r'\s+', ' ', text.lower())
    facility = re.search(r'\b(elevator|escalator|entrance|staircase|stairs|fare gate)\b', text.split(' what', 1)[0])
    impact = re.search(r'\b(board|platform|transfer|trip|service runs|runs in|no \[[^]]+\]|skip(?:s|ped|ping)?|rerout\w*|suspend\w*|delay\w*|no (?:train|bus|rail|subway|service)|service (?:is |will be )?(?:suspended|reduced|changed|disrupted)|trains? (?:will |are |is )?(?:not |no longer |skip|run|stop|bypass)|bypass\w*)\b', text)
    impact = impact or re.search(r'\b(trains?|buses?|subway service|rail service)\b', text)
    impact = impact or re.search(r'\bno\s+\[[^]]+\](?:\s+train)?\s+(?:service|between|at|to|from)\b', text)
    facility_title = re.search(r'\b(elevator|escalator|entrance|staircase|stairs|fare gate)\b', first_line) or re.match(r'^enter .*station at ', first_line)
    closure = re.search(r'\b(closed|out of service|not working|unavailable|exit only|replac\w*|repair\w*)\b', text)
    return 'no_schedule_modeling_needed' if facility and facility_title and closure and not impact else 'needs_review'


def decode(raw, source):
    feed = gtfs.FeedMessage()
    try: feed.ParseFromString(raw)
    except Exception as exc: raise ValueError('The URL did not return a valid GTFS Realtime protobuf feed.') from exc
    if not feed.IsInitialized(): raise ValueError('The GTFS Realtime feed is missing required fields.')
    # Incremental feeds cannot safely replace a full snapshot without applying deletions.
    if feed.header.incrementality != gtfs.FeedHeader.FULL_DATASET:
        raise ValueError('Incremental GTFS Realtime feeds are not supported; a full dataset is required.')
    items=[]
    for entity in feed.entity:
        if not entity.HasField('alert') or entity.is_deleted: continue
        alert=entity.alert
        title=english(alert.header_text); description=english(alert.description_text)
        mercury=alert.Extensions[MERCURY] if alert.HasExtension(MERCURY) else None
        period=english(mercury.human_readable_active_period) if mercury and mercury.HasField('human_readable_active_period') else ''
        text='\n'.join(part for part in (title,description) if part)
        if not text: text='Alert '+entity.id+' has no readable text.'
        selectors=[]
        for selector in alert.informed_entity:
            selectors.append({field.name: value for field,value in selector.ListFields() if field.name in ('agency_id','route_id','route_type','stop_id','direction_id')})
        item={'id':entity.id,'text':text,'title':title,'source':source,'status':classify(text),
              'alert_type':mercury.alert_type if mercury and mercury.HasField('alert_type') else '',
              'human_period':period,'active_periods':[{'start':p.start if p.HasField('start') else None,'end':p.end if p.HasField('end') else None} for p in alert.active_period],
              'informed_entities':selectors,
              'updated_at':mercury.updated_at if mercury and mercury.HasField('updated_at') else None}
        items.append(item)
    if len({x['id'] for x in items}) != len(items): raise ValueError('Feed contains duplicate alert IDs.')
    return items, feed.header.timestamp if feed.header.HasField('timestamp') else None


def store(raw, source, items, timestamp):
    path=os.path.join(os.environ.get('GTFS_DATA_DIR', os.path.join(os.path.dirname(__file__), '.data')), 'alerts.sqlite')
    os.makedirs(os.path.dirname(path),exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS alert_snapshots (id INTEGER PRIMARY KEY, source TEXT NOT NULL, fetched_at TEXT NOT NULL, feed_timestamp INTEGER, sha256 TEXT NOT NULL, protobuf BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS alert_observations (snapshot_id INTEGER NOT NULL, entity_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(snapshot_id, entity_id), FOREIGN KEY(snapshot_id) REFERENCES alert_snapshots(id));''')
        cursor=db.execute('INSERT INTO alert_snapshots(source,fetched_at,feed_timestamp,sha256,protobuf) VALUES (?,?,?,?,?)', (source,datetime.datetime.now(datetime.timezone.utc).isoformat(),timestamp,hashlib.sha256(raw).hexdigest(),raw))
        db.executemany('INSERT INTO alert_observations VALUES (?,?,?)',[(cursor.lastrowid,x['id'],json.dumps(x,ensure_ascii=False)) for x in items])
        return cursor.lastrowid
