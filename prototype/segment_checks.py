"""Read-only evidence for an asserted suspension between two scheduled stops."""
import csv
import datetime as dt
import io
import zipfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import reroute_checks
import workbench

def check(route_id,endpoint_names,timezone,windows,direction_id=None):
    if direction_id not in (None,'0','1'): raise ValueError('Direction must be 0, 1, or omitted for both.')
    if not isinstance(endpoint_names,list) or len(endpoint_names)!=2 or not all(isinstance(x,str) and x.strip() for x in endpoint_names): raise ValueError('Provide two endpoint names.')
    wanted={reroute_checks.norm(x) for x in endpoint_names}
    if len(wanted)!=2: raise ValueError('Endpoints must be distinct.')
    if not isinstance(windows,list) or not 1<=len(windows)<=20: raise ValueError('Provide 1–20 explicit time windows.')
    try: zone=ZoneInfo(timezone)
    except (ZoneInfoNotFoundError,ValueError): raise ValueError('Invalid timezone.')
    periods=[]
    for w in windows:
        if not isinstance(w,dict) or set(w)!={'start','end'}: raise ValueError('Each window requires start and end.')
        try: start=dt.datetime.fromisoformat(w['start']);end=dt.datetime.fromisoformat(w['end'])
        except (ValueError,TypeError): raise ValueError('Use ISO local timestamps.')
        if start.tzinfo or end.tzinfo or end<=start or end-start>dt.timedelta(days=8): raise ValueError('Invalid local time window.')
        day=start.date()-dt.timedelta(days=7)
        while day<=end.date():
            if dt.datetime.combine(day,dt.time(),zone).utcoffset()!=dt.datetime.combine(day+dt.timedelta(days=1),dt.time(),zone).utcoffset(): raise ValueError('Service dates around daylight-saving transitions are not supported.')
            day+=dt.timedelta(days=1)
        periods.append((start,end))
    if any(periods[i][0]<periods[i-1][1] for i in range(1,len(periods))): raise ValueError('Windows must be sorted and non-overlapping.')
    with workbench.connect() as db:
        route=db.execute('select * from routes where route_id=?',(route_id,)).fetchone()
        if not route: raise ValueError('Route not found in the loaded feed.')
        agency=list(db.execute('select agency_timezone from agency where agency_id=?',(route['agency_id'],))) or list(db.execute('select distinct agency_timezone from agency'))
        if {r[0] for r in agency}!={timezone}: raise ValueError('Timezone does not uniquely match the route agency.')
        source=db.execute('select source from metadata').fetchone()[0]
        with zipfile.ZipFile(source) as z:
            names=[x for x in z.namelist() if x.lower()=='frequencies.txt'];frequency_ids=set()
            if names:
                with z.open(names[0]) as f: frequency_ids={x['trip_id'] for x in csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig',newline=''))}
        cal={r['service_id']:dict(r) for r in db.execute('select * from calendar')};exc={}
        for r in db.execute('select * from calendar_dates'):
            key=(r['service_id'],r['date'])
            if key in exc: raise ValueError('Duplicate calendar exception.')
            exc[key]=r['exception_type']
        weekdays=('monday','tuesday','wednesday','thursday','friday','saturday','sunday')
        def active(service,day):
            date=day.strftime('%Y%m%d');e=exc.get((service,date))
            if e is not None:
                if e not in ('1','2'):raise ValueError('Invalid calendar exception.')
                return e=='1'
            c=cal.get(service);return bool(c and c['start_date']<=date<=c['end_date'] and c[weekdays[day.weekday()]]=='1')
        grouped={}
        sql='''select t.trip_id,t.route_id,t.direction_id,t.trip_headsign,t.service_id,st.stop_id,st.stop_sequence,st.arrival_time,st.departure_time,s.stop_name from trips t join stop_times st on st.trip_id=t.trip_id join stops s on s.stop_id=st.stop_id where t.route_id=? order by t.trip_id,cast(st.stop_sequence as integer)'''
        for row in db.execute(sql,(route_id,)):
            r=dict(row)
            if direction_id is None or r['direction_id']==direction_id: grouped.setdefault(r['trip_id'],[]).append(r)
        matches=[];ambiguous=0;excluded=0;has_both=False
        for trip,rows in grouped.items():
            indexes=[i for i,r in enumerate(rows) if reroute_checks.norm(r['stop_name']) in wanted]
            if len(indexes)!=2 or {reroute_checks.norm(rows[i]['stop_name']) for i in indexes}!=wanted:
                if len(indexes)>2: ambiguous+=1
                continue
            has_both=True
            if trip in frequency_ids: excluded+=1;continue
            segment=rows[indexes[0]:indexes[1]+1]
            first=reroute_checks.minute_time(segment[0]['departure_time'] or segment[0]['arrival_time']);last=reroute_checks.minute_time(segment[-1]['arrival_time'] or segment[-1]['departure_time'])
            if first is None or last is None or last<first: ambiguous+=1;continue
            for wi,(start,end) in enumerate(periods):
                day=start.date()-dt.timedelta(days=6)
                while day<=end.date():
                    begin=dt.datetime.combine(day,dt.time())+dt.timedelta(seconds=first);finish=dt.datetime.combine(day,dt.time())+dt.timedelta(seconds=last)
                    if begin<end and finish>start and active(rows[0]['service_id'],day):
                        matches.append({'window':wi,'trip_id':trip,'direction_id':rows[0]['direction_id'],'headsign':rows[0]['trip_headsign'],'service_id':rows[0]['service_id'],'service_date':day.isoformat(),'segment_start':begin.isoformat(),'segment_end':finish.isoformat(),'stops':[{'id':x['stop_id'],'name':x['stop_name']} for x in segment]})
                    day+=dt.timedelta(days=1)
        if not has_both: raise ValueError('No single trip in the selected route/direction contains both endpoints; names or scope need review.')
        counts=[sum(x['window']==i for x in matches) for i in range(len(periods))]
        dirs={}
        for x in matches: dirs[x['direction_id']]=dirs.get(x['direction_id'],0)+1
        return {'status':'likely_not_modeled' if matches else 'inconclusive','route':dict(route),'direction_id':direction_id,'endpoint_names':endpoint_names,'windows':windows,'timezone':timezone,'conflicting_trip_occurrences':len(matches),'window_counts':counts,'direction_counts':dirs,'sample_records':matches[:12],'sample_truncated':len(matches)>12,'frequency_trip_templates_excluded':excluded,'ambiguous_or_untimed_trip_templates':ambiguous,'limitations':['A scheduled trip containing both named endpoints is evidence of service across the segment, not proof of actual operation.', 'Only trips with both named endpoints are detected; short-turn, express, alternate-platform and other patterns require review.', 'Segment overlap is used; whether trips already underway at a boundary should be affected requires confirmation.', 'Frequency trips, pickup/drop-off restrictions, intermediate routing, shapes and replacement service are not verified.']}
