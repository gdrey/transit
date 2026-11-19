"""Bounded, read-only checks for an explicitly resolved alert."""
import datetime as dt
import io
import csv
import re
import zipfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import workbench

def route_stops(route_id, direction_id):
    if direction_id not in ('0','1'): raise ValueError('direction_id must be 0 or 1.')
    with workbench.connect() as db:
        route=db.execute('select * from routes where route_id=?',(route_id,)).fetchone()
        if not route: raise ValueError('Route not found.')
        rows=db.execute('''select distinct s.stop_id,s.stop_name,s.parent_station from trips t join stop_times st on st.trip_id=t.trip_id join stops s on s.stop_id=st.stop_id where t.route_id=? and t.direction_id=? order by s.stop_name,s.stop_id''',(route_id,direction_id)).fetchall()
        heads=db.execute('select distinct trip_headsign from trips where route_id=? and direction_id=? and trip_headsign<>\'\' order by trip_headsign limit 20',(route_id,direction_id)).fetchall()
    return {'route':dict(route),'direction_id':direction_id,'headsigns':[x[0] for x in heads], 'stops':[dict(x) for x in rows], 'note':'Direction IDs are feed-specific; use trip headsigns and stop sequences as evidence, not a universal compass mapping.'}

def check_skips(change_type,route_id,direction_id,stop_ids,timezone,windows,include_all=False,require_route_stops=True):
    if change_type!='stops_skipped': raise ValueError('Only stops_skipped checks are supported.')
    if direction_id not in ('0','1'): raise ValueError('direction_id must be 0 or 1.')
    if not isinstance(stop_ids,list) or not 1<=len(stop_ids)<=20 or len(set(stop_ids))!=len(stop_ids) or not all(isinstance(x,str) and x for x in stop_ids): raise ValueError('Provide 1–20 distinct stop IDs.')
    if not isinstance(windows,list) or not 1<=len(windows)<=20: raise ValueError('Provide 1–20 explicit windows.')
    try: zone=ZoneInfo(timezone)
    except (ZoneInfoNotFoundError,ValueError): raise ValueError('Invalid IANA timezone.')
    intervals=[]
    for w in windows:
        if not isinstance(w,dict) or set(w)!={'start','end'}: raise ValueError('Each window requires start and end.')
        try: start=dt.datetime.fromisoformat(w['start']);end=dt.datetime.fromisoformat(w['end'])
        except (ValueError,TypeError): raise ValueError('Use ISO local timestamps.')
        if start.tzinfo or end.tzinfo or end<=start or end-start>dt.timedelta(days=8): raise ValueError('Windows must use local timestamps, end after start, and last at most eight days.')
        # GTFS service-day time has special daylight-saving semantics; do not guess.
        probe=start.date()-dt.timedelta(days=7)
        while probe<=end.date():
            before=dt.datetime.combine(probe,dt.time(0),zone).utcoffset();after=dt.datetime.combine(probe+dt.timedelta(days=1),dt.time(0),zone).utcoffset()
            if before!=after: raise ValueError('A relevant service day crosses a daylight-saving transition; this check does not support it yet.')
            probe+=dt.timedelta(days=1)
        intervals.append((start,end))
    if any(intervals[i][0]<intervals[i-1][1] for i in range(1,len(intervals))): raise ValueError('Windows must be sorted and non-overlapping.')
    with workbench.connect() as db:
        route=db.execute('select * from routes where route_id=?',(route_id,)).fetchone()
        if not route: raise ValueError('Route ID not found.')
        agencies=db.execute('select agency_timezone from agency where agency_id=?',(route['agency_id'],)).fetchall()
        if not agencies:
            agencies=db.execute('select distinct agency_timezone from agency').fetchall()
        zones={r[0] for r in agencies}
        if zones!={timezone}: raise ValueError('Timezone does not uniquely match the route agency in this feed.')
        for sid in stop_ids:
            found=db.execute('select 1 from stop_times st join trips t on t.trip_id=st.trip_id where t.route_id=? and t.direction_id=? and st.stop_id=? limit 1',(route_id,direction_id,sid)).fetchone()
            if not found and (require_route_stops or not db.execute('select 1 from stops where stop_id=?',(sid,)).fetchone()): raise ValueError('Stop '+sid+' does not match the route and direction in this feed; resolve IDs first.')
        path=db.execute('select source from metadata').fetchone()[0]
        stop_rules={}
        with zipfile.ZipFile(path) as z:
            names=[x for x in z.namelist() if x.lower()=='frequencies.txt']
            stop_name=next((x for x in z.namelist() if x.lower()=='stop_times.txt'),None)
            if stop_name:
                with z.open(stop_name) as f:
                    for x in csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig',newline='')):
                        stop_rules[(x.get('trip_id'),x.get('stop_sequence'))]=(x.get('pickup_type','') or '0',x.get('drop_off_type','') or '0')
            freq=set()
            if names:
                with z.open(names[0]) as f: freq={x['trip_id'] for x in csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig',newline=''))}
        placeholders=','.join('?' for _ in stop_ids)
        rows=db.execute('''select t.trip_id,t.service_id,t.trip_headsign,st.stop_id,st.stop_sequence,st.arrival_time,st.departure_time from trips t join stop_times st on st.trip_id=t.trip_id where t.route_id=? and t.direction_id=? and st.stop_id in ('''+placeholders+')',[route_id,direction_id]+stop_ids).fetchall()
        def seconds(value):
            if not value: return None
            m=re.fullmatch(r'\s*(\d+):(\d{2}):(\d{2})\s*',value)
            if not m or int(m[2])>59 or int(m[3])>59: raise ValueError('Invalid GTFS stop time; check cannot proceed.')
            return int(m[1])*3600+int(m[2])*60+int(m[3])
        records=[]; max_offset=0; missing=0; frequency_seen=False
        for row in rows:
            when=seconds(row['arrival_time'])
            if when is None: when=seconds(row['departure_time'])
            if when is None: missing+=1;continue
            max_offset=max(max_offset,when//86400)
            if row['trip_id'] in freq: frequency_seen=True;continue
            records.append((row,when))
        if max_offset>7: raise ValueError('A stop time exceeds seven service-day offsets; not supported by this prototype.')
        cal={}
        for r in db.execute('select * from calendar'):cal[r['service_id']]=dict(r)
        exc={}
        for r in db.execute('select * from calendar_dates'):
            k=(r['service_id'],r['date'])
            if k in exc: raise ValueError('Duplicate calendar exception: '+str(k))
            exc[k]=r['exception_type']
        days=('monday','tuesday','wednesday','thursday','friday','saturday','sunday')
        def active(service,day):
            date=day.strftime('%Y%m%d');e=exc.get((service,date))
            if e is not None:
                if e not in ('1','2'): raise ValueError('Invalid calendar exception.')
                return e=='1'
            c=cal.get(service);return bool(c and c['start_date']<=date<=c['end_date'] and c[days[day.weekday()]]=='1')
        seen=set();samples=[];by_stop={s:0 for s in stop_ids};window_counts=[0]*len(intervals);disabled=set();disabled_by_window=[set() for _ in intervals];served_by_window=[set() for _ in intervals]
        for row,sec in records:
            for wi,(start,end) in enumerate(intervals):
                day=start.date()-dt.timedelta(days=max_offset)
                while day<=end.date():
                    when=dt.datetime.combine(day,dt.time())+dt.timedelta(seconds=sec)
                    if start<=when<end and active(row['service_id'],day):
                        key=(row['trip_id'],day.isoformat(),row['stop_sequence'])
                        if stop_rules.get((row['trip_id'],row['stop_sequence']))==('1','1'):
                            disabled.add(key);disabled_by_window[wi].add(row['stop_id']);continue
                        served_by_window[wi].add(row['stop_id'])
                        if key not in seen:
                            seen.add(key);by_stop[row['stop_id']]+=1;window_counts[wi]+=1
                            if include_all or len(samples)<30:samples.append({'trip_id':row['trip_id'],'service_id':row['service_id'],'service_date':day.isoformat(),'stop_id':row['stop_id'],'stop_sequence':row['stop_sequence'],'local_time':when.isoformat(),'headsign':row['trip_headsign']})
                    day+=dt.timedelta(days=1)
        status='not_modeled' if seen else ('already_modeled' if not missing and not frequency_seen and all(set(stop_ids)<=stops for stops in disabled_by_window) else 'inconclusive')
        return {'status':status,'interpretation':{'change_type':change_type,'route_id':route_id,'direction_id':direction_id,'stop_ids':stop_ids,'timezone':timezone,'windows':windows},'contradicting_stop_records':len(seen),'no_boarding_stop_records':len(disabled),'served_stops_by_window':[sorted(x) for x in served_by_window],'by_stop':by_stop,'window_counts':window_counts,'sample_records':samples,'sample_truncated':len(seen)>len(samples),'missing_time_records':missing,'frequency_templates_excluded':frequency_seen,'limitations':['Stop records with pickup_type 1 and drop_off_type 1 are treated as no boarding or alighting; other records are treated as scheduled service.', 'No conflicts does not prove the alert is modeled: feed coverage, alternatives and frequency-based trips require further checks.', 'Windows are local civil times; end is exclusive. Arrival time is used when available, otherwise departure.','Only exact stop IDs and this direction were examined.']}
