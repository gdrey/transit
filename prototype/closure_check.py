#!/usr/bin/env python3
"""Check whether a route's scheduled stop records omit a station over service dates."""
import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
import workbench

def check(route_id, stop_ids, start, end, source):
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if last < first or (last-first).days > 366: raise ValueError('End date must be on or after start date, within 366 days.')
    with workbench.connect() as db:
        route = db.execute('select * from routes where route_id=?', (route_id,)).fetchone()
        if not route: raise ValueError('Route ID not found.')
        stops = [dict(s) for s in db.execute('select * from stops where stop_id in ('+','.join('?' for _ in stop_ids)+')', stop_ids)]
        if len(stops) != len(set(stop_ids)): raise ValueError('One or more stop IDs were not found.')
        if not db.execute('select 1 from trips t join stop_times st on st.trip_id=t.trip_id where t.route_id=? and st.stop_id in ('+','.join('?' for _ in stop_ids)+') limit 1', [route_id]+stop_ids).fetchone():
            raise ValueError('No route trips reference these stops in the feed; verify the route and stop IDs before assessing.')
        days=[]
        for offset in range((last-first).days+1):
            day=first+dt.timedelta(days=offset); key=day.strftime('%Y%m%d')
            weekday=('monday','tuesday','wednesday','thursday','friday','saturday','sunday')[day.weekday()]
            where='''t.route_id=? and ((exists(select 1 from calendar c where c.service_id=t.service_id and c.start_date<=? and c.end_date>=? and c."'''+weekday+'''"='1') and not exists(select 1 from calendar_dates e where e.service_id=t.service_id and e.date=? and e.exception_type='2')) or exists(select 1 from calendar_dates e where e.service_id=t.service_id and e.date=? and e.exception_type='1'))'''
            args=[route_id,key,key,key,key]
            total=db.execute('select count(*) from trips t where '+where,args).fetchone()[0]
            hit=db.execute('select count(*) from trips t where '+where+' and exists(select 1 from stop_times st where st.trip_id=t.trip_id and st.stop_id in ('+','.join('?' for _ in stop_ids)+'))',args+stop_ids).fetchone()[0]
            days.append({'service_date':day.isoformat(),'route_trip_records':total,'trips_referencing_stop':hit})
        source_info=dict(db.execute('select * from metadata').fetchone())
    # No active trips may indicate expired or uncovered service, rather than a closure.
    if any(x['trips_referencing_stop'] for x in days): status='not_modeled'
    elif any(x['route_trip_records']==0 for x in days): status='inconclusive'
    else: status='modeled_for_selected_stop_records'
    feed_path=source_info['source']; digest=hashlib.sha256()
    if os.path.isfile(feed_path):
        with open(feed_path,'rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''): digest.update(chunk)
        feed_hash=digest.hexdigest()
    else: feed_hash=None
    return {'check':'scheduled_station_omission','status':status,'route':dict(route),'stops':stops,'dates':{'start':start,'end_inclusive':end},'claim_source':source,'feed':{**source_info,'zip_sha256':feed_hash},'daily_evidence':days,'limitations':['Checks stop-time record presence, not alerts, pickup/drop-off restrictions, actual operations, or the route of vehicles.', 'Uses GTFS service dates, which may differ from the civil date of stops after midnight.', 'Trip records may include frequency-based templates; counts are not necessarily departures.', 'Only the selected route and stop IDs were assessed.'], 'activity':[{'step':1,'action':'resolve route and stop IDs','status':'completed'},{'step':2,'action':'evaluate scheduled service on each selected service date','status':'completed'},{'step':3,'action':'count trips referencing selected stops','status':'completed'},{'step':4,'action':'classify evidence','status':status}]}

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--route-id',required=True); p.add_argument('--stop-id',action='append',required=True); p.add_argument('--start',required=True); p.add_argument('--end',required=True); p.add_argument('--source',default=''); p.add_argument('--output')
    a=p.parse_args()
    try:
        result=check(a.route_id,a.stop_id,a.start,a.end,a.source)
        text=json.dumps(result,indent=2,ensure_ascii=False)
        if a.output:
            with open(a.output,'w',encoding='utf-8') as f: f.write(text+'\n')
        print(text)
    except (ValueError,sqlite3.Error,OSError) as e:
        print('Error: '+str(e),file=sys.stderr); sys.exit(1)
if __name__=='__main__': main()
