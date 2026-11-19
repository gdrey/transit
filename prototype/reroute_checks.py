"""Compare a route's scheduled segments with a reference route's segments."""
import datetime as dt
import csv
import io
import re
import zipfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import workbench

def minute_time(value):
    if not value: return None
    m=re.fullmatch(r'\s*(\d+):(\d{2}):(\d{2})\s*',value)
    if not m or int(m[2])>59 or int(m[3])>59: raise ValueError('Invalid GTFS time.')
    seconds=int(m[1])*3600+int(m[2])*60+int(m[3])
    if seconds>=7*86400: raise ValueError('Times at least seven days after the service date are not supported.')
    return seconds

def norm(name): return ' '.join((name or '').casefold().split())

def check(route_id,reference_route_id,endpoint_names,timezone,windows,direction_id=None):
    if route_id==reference_route_id: raise ValueError('Target and reference route must differ.')
    if direction_id not in (None,'0','1'): raise ValueError('Direction must be 0 or 1, or omitted for both directions.')
    if not isinstance(endpoint_names,list) or len(endpoint_names)!=2 or not all(isinstance(x,str) and x.strip() for x in endpoint_names) or norm(endpoint_names[0])==norm(endpoint_names[1]): raise ValueError('Provide two distinct endpoint names.')
    if not isinstance(windows,list) or not 1<=len(windows)<=20: raise ValueError('Provide 1–20 explicit windows.')
    try: zone=ZoneInfo(timezone)
    except (ZoneInfoNotFoundError,ValueError): raise ValueError('Invalid timezone.')
    periods=[]
    for w in windows:
        if not isinstance(w,dict) or set(w)!={'start','end'}: raise ValueError('Each window requires start and end.')
        start=dt.datetime.fromisoformat(w['start']);end=dt.datetime.fromisoformat(w['end'])
        if start.tzinfo or end.tzinfo or end<=start or end-start>dt.timedelta(days=8): raise ValueError('Invalid local time window.')
        day=start.date()-dt.timedelta(days=7)
        while day<=end.date():
            if dt.datetime.combine(day,dt.time(),zone).utcoffset()!=dt.datetime.combine(day+dt.timedelta(days=1),dt.time(),zone).utcoffset(): raise ValueError('Daylight-saving transitions are not yet supported.')
            day+=dt.timedelta(days=1)
        periods.append((start,end))
    if any(periods[i][0]<periods[i-1][1] for i in range(1,len(periods))): raise ValueError('Windows must be sorted and non-overlapping.')
    with workbench.connect() as db:
        path=db.execute('select source from metadata').fetchone()[0]
        with zipfile.ZipFile(path) as z:
            names=[x for x in z.namelist() if x.lower()=='frequencies.txt'];frequency_ids=set()
            if names:
                with z.open(names[0]) as f: frequency_ids={x['trip_id'] for x in csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig',newline=''))}
        routes={r['route_id']:dict(r) for r in db.execute('select * from routes where route_id in (?,?)',(route_id,reference_route_id))}
        if len(routes)!=2: raise ValueError('Route or reference route not found.')
        for route in routes.values():
            agency=list(db.execute('select agency_timezone from agency where agency_id=?',(route['agency_id'],))) or list(db.execute('select distinct agency_timezone from agency'))
            if {r[0] for r in agency}!={timezone}: raise ValueError('Timezone does not uniquely match both routes.')
        cal={r['service_id']:dict(r) for r in db.execute('select * from calendar')};exceptions={}
        for r in db.execute('select * from calendar_dates'):
            key=(r['service_id'],r['date'])
            if key in exceptions: raise ValueError('Duplicate calendar exception.')
            exceptions[key]=r['exception_type']
        days=('monday','tuesday','wednesday','thursday','friday','saturday','sunday')
        def active(service,date):
            s=date.strftime('%Y%m%d');e=exceptions.get((service,s))
            if e is not None:
                if e not in ('1','2'): raise ValueError('Invalid calendar exception.')
                return e=='1'
            c=cal.get(service);return bool(c and c['start_date']<=s<=c['end_date'] and c[days[date.weekday()]]=='1')
        grouped={}
        sql='''select t.trip_id,t.route_id,t.direction_id,t.trip_headsign,t.service_id,st.stop_id,st.stop_sequence,st.arrival_time,st.departure_time,s.stop_name from trips t join stop_times st on st.trip_id=t.trip_id join stops s on s.stop_id=st.stop_id where t.route_id in (?,?) order by t.trip_id,cast(st.stop_sequence as integer)'''
        for r in db.execute(sql,(route_id,reference_route_id)):
            d=dict(r);grouped.setdefault(d['trip_id'],[]).append(d)
        wanted={norm(x) for x in endpoint_names};segments=[]; ambiguous=0;frequency_excluded=0
        for trip,rows in grouped.items():
            if trip in frequency_ids:
                frequency_excluded+=1
                continue
            if rows[0]['route_id']==route_id and direction_id is not None and rows[0]['direction_id']!=direction_id: continue
            indices=[i for i,r in enumerate(rows) if norm(r['stop_name']) in wanted]
            if len(indices)!=2 or {norm(rows[i]['stop_name']) for i in indices}!=wanted:
                if len(indices)>2: ambiguous+=1
                continue
            a,b=indices;part=rows[a:b+1];startsec=minute_time(part[0]['departure_time'] or part[0]['arrival_time']);endsec=minute_time(part[-1]['arrival_time'] or part[-1]['departure_time'])
            if startsec is None or endsec is None or endsec<startsec: ambiguous+=1;continue
            segments.append({'trip_id':trip,'route_id':part[0]['route_id'],'direction_id':part[0]['direction_id'],'headsign':part[0]['trip_headsign'],'service_id':part[0]['service_id'],'start':startsec,'end':endsec,'stops':[{'id':r['stop_id'],'name':r['stop_name']} for r in part]})
        observed=[]
        for seg in segments:
            for wi,(begin,end) in enumerate(periods):
                day=begin.date()-dt.timedelta(days=6)
                while day<=end.date():
                    first=dt.datetime.combine(day,dt.time())+dt.timedelta(seconds=seg['start']);last=dt.datetime.combine(day,dt.time())+dt.timedelta(seconds=seg['end'])
                    if first<end and last>=begin and active(seg['service_id'],day): observed.append((seg,wi,day.isoformat(),first.isoformat(),last.isoformat()))
                    day+=dt.timedelta(days=1)
        # A matching trip must use the same ordered station names, but platform IDs may differ.
        reference={}
        for seg,wi,day,first,last in observed:
            if seg['route_id']==reference_route_id:
                signature=tuple(norm(s['name']) for s in seg['stops']);key=(wi,signature[0],signature[-1]);reference.setdefault(key,{})[signature]=seg
        summaries={};examples=[];matched=0;different=0;no_ref=0
        for seg,wi,day,first,last in observed:
            if seg['route_id']!=route_id: continue
            names=tuple(norm(s['name']) for s in seg['stops']);refs=reference.get((wi,names[0],names[-1]),{})
            if not refs: no_ref+=1;continue
            outcome='matches_reference_stop_sequence' if names in refs else 'different_from_reference_stop_sequences'
            if outcome.startswith('matches'): matched+=1
            else: different+=1
            key=(wi,seg['direction_id'],tuple(s['name'] for s in seg['stops']),outcome)
            summaries[key]=summaries.get(key,0)+1
            if len(examples)<8 and outcome.startswith('different'):
                example_ref=next(iter(refs.values()))
                examples.append({'window':wi,'service_date':day,'trip_id':seg['trip_id'],'direction_id':seg['direction_id'],'headsign':seg['headsign'],'segment_start':first,'segment_end':last,'actual_stops':seg['stops'],'reference_stops':example_ref['stops'],'reference_trip_id':example_ref['trip_id']})
        reference_patterns=[]
        for (wi,first,last),items in reference.items():
            for signature,seg in items.items():reference_patterns.append({'window':wi,'direction_id':seg['direction_id'],'example_trip_id':seg['trip_id'],'stops':seg['stops']})
        return {'status':'likely_not_modeled' if different else 'inconclusive','target_route':routes[route_id],'reference_route':routes[reference_route_id],'direction_id':direction_id,'endpoint_names':endpoint_names,'timezone':timezone,'windows':windows,'different_trip_occurrences':different,'matching_trip_occurrences':matched,'unmatched_reference_occurrences':no_ref,'ambiguous_or_untimed_trip_templates':ambiguous,'frequency_trip_templates_excluded':frequency_excluded,'patterns':[{'window':k[0],'direction_id':k[1],'stops':list(k[2]),'result':k[3],'trip_occurrences':v} for k,v in summaries.items()],'examples':examples,'reference_patterns':reference_patterns[:20],'limitations':['The comparison infers that “via” entails the reference route’s ordered stop names; confirm this interpretation against the announcement.', 'Reference and target trips need not run at the same minute; reference patterns are taken from the same alert window and travel orientation.', 'Only scheduled stop sequences are compared; platform assignments, pickup/drop-off rules, shapes, track paths, frequency trips and actual operations are not verified.', 'Segment-overlap with the alert window is used; the precise operational boundary for a trip may need confirmation.', 'No matching difference does not prove a reroute is fully modeled.']}
