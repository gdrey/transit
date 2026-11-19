#!/usr/bin/env python3
"""Small read-only GTFS Schedule explorer; no model or dependencies required."""
import argparse
import csv
import datetime as dt
import io
import json
import os
import sqlite3
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get('GTFS_DB', os.path.join(ROOT, '.data', 'feed.sqlite'))
TABLES = {
    'agency': ('agency_id', 'agency_name', 'agency_timezone'),
    'routes': ('route_id', 'route_short_name', 'route_long_name', 'agency_id', 'route_type', 'route_color', 'route_text_color'),
    'stops': ('stop_id', 'stop_name', 'parent_station'),
    'trips': ('trip_id', 'route_id', 'service_id', 'trip_headsign', 'direction_id'),
    'stop_times': ('trip_id', 'stop_id', 'stop_sequence', 'arrival_time', 'departure_time'),
    'calendar': ('service_id', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday', 'start_date', 'end_date'),
    'calendar_dates': ('service_id', 'date', 'exception_type'),
}

def connect(db_path=None):
    db_path = db_path or DB
    if not os.path.isfile(db_path):
        raise ValueError('No feed imported. Run: python3 workbench.py import /path/to/feed.zip')
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    return db

def import_feed(path, db_path=None, source_url=None):
    db_path = db_path or DB
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    temp = db_path + '.tmp'
    if os.path.exists(temp): os.remove(temp)
    try:
        with zipfile.ZipFile(path) as z, sqlite3.connect(temp) as db:
            members = {}
            for info in z.infolist():
                if '/' in info.filename or '\\' in info.filename: continue
                name = info.filename.lower()
                if name in members: raise ValueError('Duplicate file: ' + name)
                members[name] = info
            for name in ('agency', 'routes', 'stops', 'trips', 'stop_times'):
                if name + '.txt' not in members: raise ValueError('Missing ' + name + '.txt')
            if not any(n in members for n in ('calendar.txt', 'calendar_dates.txt')):
                raise ValueError('Missing calendar.txt and calendar_dates.txt')
            for name, cols in TABLES.items():
                db.execute('CREATE TABLE ' + name + ' (' + ','.join('"'+c+'" TEXT' for c in cols) + ')')
                if name + '.txt' not in members: continue
                with z.open(members[name + '.txt']) as raw:
                    reader = csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8-sig', newline=''))
                    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                        raise ValueError('Missing or duplicate headers in ' + name + '.txt')
                    required = {'routes': ('route_id',), 'trips': ('trip_id','route_id','service_id'), 'stop_times': ('trip_id','stop_id','stop_sequence'), 'stops': ('stop_id',), 'calendar': ('service_id','start_date','end_date','monday','tuesday','wednesday','thursday','friday','saturday','sunday'), 'calendar_dates': ('service_id','date','exception_type')}.get(name, ())
                    if any(c not in reader.fieldnames for c in required): raise ValueError('Missing required columns in ' + name + '.txt')
                    sql = 'INSERT INTO '+name+' VALUES ('+','.join('?' for _ in cols)+')'
                    db.executemany(sql, ([row.get(c) or '' for c in cols] for row in reader))
            for table, col in [('trips','route_id'), ('trips','service_id'), ('stop_times','trip_id'), ('calendar_dates','service_id')]:
                db.execute('CREATE INDEX idx_'+table+'_'+col+' ON '+table+'('+col+')')
            db.execute('CREATE TABLE metadata (source TEXT, imported_at TEXT, source_url TEXT)')
            db.execute('INSERT INTO metadata VALUES (?,?,?)', (os.path.abspath(path), dt.datetime.now(dt.timezone.utc).isoformat(), source_url or ''))
        os.replace(temp, db_path)
    finally:
        if os.path.exists(temp): os.remove(temp)
    return summary(db_path)

def summary(db_path=None):
    with connect(db_path) as db:
        counts = {name: db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in TABLES}
        return {'source': dict(db.execute('SELECT * FROM metadata').fetchone()), 'agencies': [dict(r) for r in db.execute('SELECT * FROM agency LIMIT 20')], 'counts': counts, 'note': 'GTFS Schedule only; no real-time or operational confirmation.'}

def search_routes(query='', limit=25):
    limit = max(1, min(int(limit), 100))
    with connect() as db:
        q = '%'+query+'%'
        rows = db.execute('SELECT * FROM routes WHERE route_id LIKE ? OR route_short_name LIKE ? OR route_long_name LIKE ? ORDER BY route_short_name,route_id LIMIT ?', (q,q,q,limit+1)).fetchall()
        return {'matches': [dict(r) for r in rows[:limit]], 'truncated': len(rows)>limit}

def search_stops(query='', limit=25):
    limit = max(1, min(int(limit), 100))
    with connect() as db:
        q = '%'+query+'%'
        rows = db.execute('SELECT * FROM stops WHERE stop_id LIKE ? OR stop_name LIKE ? ORDER BY stop_name,stop_id LIMIT ?', (q,q,limit+1)).fetchall()
        return {'matches': [dict(r) for r in rows[:limit]], 'truncated': len(rows)>limit}

def trips_on_date(route_id, date, limit=25):
    day = dt.date.fromisoformat(date)
    datekey = day.strftime('%Y%m%d')
    weekday = ('monday','tuesday','wednesday','thursday','friday','saturday','sunday')[day.weekday()]
    limit = max(1, min(int(limit), 100))
    with connect() as db:
        route = db.execute('SELECT * FROM routes WHERE route_id=?', (route_id,)).fetchone()
        if not route: raise ValueError('Route ID not found; use search_routes to find its exact ID.')
        sql = '''SELECT t.trip_id,t.service_id,t.trip_headsign,t.direction_id,
          (SELECT s.departure_time FROM stop_times s WHERE s.trip_id=t.trip_id ORDER BY CAST(s.stop_sequence AS INTEGER) LIMIT 1) AS first_departure
          FROM trips t WHERE t.route_id=? AND
          ((EXISTS (SELECT 1 FROM calendar c WHERE c.service_id=t.service_id AND c.start_date<=? AND c.end_date>=? AND c."'''+weekday+'''"='1')
            AND NOT EXISTS (SELECT 1 FROM calendar_dates x WHERE x.service_id=t.service_id AND x.date=? AND x.exception_type='2'))
           OR EXISTS (SELECT 1 FROM calendar_dates x WHERE x.service_id=t.service_id AND x.date=? AND x.exception_type='1'))'''
        args = (route_id,datekey,datekey,datekey,datekey)
        count = db.execute('SELECT count(*) FROM ('+sql+')', args).fetchone()[0]
        rows = db.execute(sql+' ORDER BY first_departure, t.trip_id LIMIT ?', args+(limit,)).fetchall()
        return {'route': dict(route), 'service_date': date, 'trip_count': count, 'trips': [dict(r) for r in rows], 'truncated': count>limit, 'note': 'Service date, not necessarily the civil date of every stop. Times may exceed 24:00. Frequency-based trips are counted as trip templates, not actual departures. Schedule is not live status.'}

def call_structured(name, args):
    import structured_checks
    return getattr(structured_checks,name)(**args)

def call_reroute(args):
    import reroute_checks
    return reroute_checks.check(**args)

def call_platform(args):
    import platform_checks
    return platform_checks.check(**args)

def call_suspension(args):
    import segment_checks
    return segment_checks.check(**args)

TOOLS = {
 'get_feed_summary': ('Get the imported feed summary, agency timezones and table counts.', {}, [], lambda a: summary()),
 'search_routes': ('Find route IDs by name or ID; use actual IDs in subsequent calls.', {'query': {'type':'string'}, 'limit': {'type':'integer'}}, [], lambda a: search_routes(**a)),
 'search_stops': ('Find stop IDs by name or ID.', {'query': {'type':'string'}, 'limit': {'type':'integer'}}, [], lambda a: search_stops(**a)),
 'get_route_service': ('Find scheduled trip templates on one GTFS service date, accounting for calendar exceptions. This is not live service.', {'route_id': {'type':'string'}, 'date': {'type':'string','description':'Service date in YYYY-MM-DD'}, 'limit': {'type':'integer'}}, ['route_id','date'], lambda a: trips_on_date(**a)),
 'get_route_stops': ('List exact stop IDs and headsigns for a route and direction to resolve an alert. Direction IDs are feed-specific.', {'route_id':{'type':'string'},'direction_id':{'type':'string','enum':['0','1']}}, ['route_id','direction_id'], lambda a: call_structured('route_stops',a)),
 'check_skipped_stops': ('Compare a resolved skipped-stops alert against scheduled stop-time records. Supply exact feed IDs and explicit sorted local civil-time windows. End times are exclusive. Results include evidence; absence of conflicts is inconclusive.', {'change_type':{'type':'string','enum':['stops_skipped']},'route_id':{'type':'string'},'direction_id':{'type':'string','enum':['0','1']},'stop_ids':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':20},'timezone':{'type':'string'},'windows':{'type':'array','minItems':1,'maxItems':20,'items':{'type':'object','properties':{'start':{'type':'string'},'end':{'type':'string'}},'required':['start','end'],'additionalProperties':False}}}, ['change_type','route_id','direction_id','stop_ids','timezone','windows'], lambda a: call_structured('check_skips',a)),
 'check_platform_change': ('Compare resolved station platform pairs against scheduled trips for explicit local windows. IDs must share a parent station and target platforms must be used by the specified route and target direction. No edit is made.', {'route_id':{'type':'string'},'direction_id':{'type':'string','enum':['0','1']},'stop_ids':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':20},'target_direction_id':{'type':'string','enum':['0','1']},'target_stop_ids':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':20},'timezone':{'type':'string'},'windows':{'type':'array','minItems':1,'maxItems':20,'items':{'type':'object','properties':{'start':{'type':'string'},'end':{'type':'string'}},'required':['start','end'],'additionalProperties':False}}}, ['route_id','direction_id','stop_ids','target_direction_id','target_stop_ids','timezone','windows'], lambda a: call_platform(a)),
 'check_reference_reroute': ('Compare scheduled segments of a target route against a reference route between two station names in explicit local time windows. The assumption that the reroute follows the reference stop sequence requires review.', {'route_id':{'type':'string'},'reference_route_id':{'type':'string'},'endpoint_names':{'type':'array','items':{'type':'string'},'minItems':2,'maxItems':2},'timezone':{'type':'string'},'direction_id':{'type':'string','enum':['0','1']},'windows':{'type':'array','minItems':1,'maxItems':20,'items':{'type':'object','properties':{'start':{'type':'string'},'end':{'type':'string'}},'required':['start','end'],'additionalProperties':False}}}, ['route_id','reference_route_id','endpoint_names','timezone','windows'], lambda a: call_reroute(a)),
 'check_segment_suspension': ('Find scheduled trip occurrences that traverse both named endpoints while overlapping explicit local time windows. Absence of matching trips is inconclusive; do not infer endpoints are closed.', {'route_id':{'type':'string'},'endpoint_names':{'type':'array','items':{'type':'string'},'minItems':2,'maxItems':2},'timezone':{'type':'string'},'direction_id':{'type':'string','enum':['0','1']},'windows':{'type':'array','minItems':1,'maxItems':20,'items':{'type':'object','properties':{'start':{'type':'string'},'end':{'type':'string'}},'required':['start','end'],'additionalProperties':False}}}, ['route_id','endpoint_names','timezone','windows'], lambda a: call_suspension(a)),
}

def serve():
    for line in sys.stdin:
        try:
            request=json.loads(line); method=request.get('method'); ident=request.get('id')
            if method == 'initialize': result={'protocolVersion':request.get('params',{}).get('protocolVersion','2024-11-05'),'capabilities':{'tools':{}},'serverInfo':{'name':'gtfs-workbench-prototype','version':'0.1.0'}}
            elif method == 'ping': result={}
            elif method == 'tools/list': result={'tools':[{'name':n,'description':v[0],'inputSchema':{'type':'object','properties':v[1],'required':v[2],'additionalProperties':False}} for n,v in TOOLS.items()]}
            elif method == 'tools/call':
                params=request.get('params',{}); name=params.get('name')
                try:
                    if name not in TOOLS: raise ValueError('Unknown tool: '+str(name))
                    output=TOOLS[name][3](params.get('arguments',{}))
                    result={'content':[{'type':'text','text':json.dumps(output,ensure_ascii=False)}]}
                except (ValueError, TypeError, sqlite3.Error) as e: result={'isError':True,'content':[{'type':'text','text':str(e)}]}
            elif ident is None: continue
            else:
                print(json.dumps({'jsonrpc':'2.0','id':ident,'error':{'code':-32601,'message':'Method not found'}}),flush=True); continue
            if ident is not None: print(json.dumps({'jsonrpc':'2.0','id':ident,'result':result},ensure_ascii=False),flush=True)
        except (ValueError, TypeError) as e:
            print(json.dumps({'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':str(e)}}),flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='command',required=True)
    imp=sub.add_parser('import'); imp.add_argument('zipfile')
    sub.add_parser('summary'); sub.add_parser('serve')
    route=sub.add_parser('routes'); route.add_argument('query',nargs='?',default='')
    service=sub.add_parser('service'); service.add_argument('route_id'); service.add_argument('date')
    args=p.parse_args()
    try:
        if args.command=='serve': serve(); return
        result={'import':lambda:import_feed(args.zipfile),'summary':summary,'routes':lambda:search_routes(args.query),'service':lambda:trips_on_date(args.route_id,args.date)}[args.command]()
        print(json.dumps(result,indent=2,ensure_ascii=False))
    except (ValueError, OSError, sqlite3.Error, zipfile.BadZipFile) as e:
        print('Error: '+str(e),file=sys.stderr); sys.exit(1)

if __name__ == '__main__': main()
