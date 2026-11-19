"""Resolve express-to-local stops using existing local patterns as timing evidence."""
import datetime as dt, re
import workbench, structured_checks, modeling_policy

def seconds(s):
    try:
        h,m,sec=map(int,s.split(':')); return h*3600+m*60+sec
    except (ValueError,AttributeError): return None

def clock(n): return f'{n//3600:02}:{n//60%60:02}:{n%60:02}'

def plan(entry):
    item=entry['item']; interp=entry['assessment']['interpretation']; route=interp['route_id']; windows=interp['windows']
    if not windows: raise ValueError('Added stops require explicit alert windows.')
    with workbench.connect() as db:
        entities={x['stop_id'] for x in item.get('informed_entities',[]) if x.get('stop_id')}
        stops=[dict(r) for r in db.execute('select stop_id,stop_name,parent_station from stops')]
        resolved=set(interp.get('stop_ids') or [])
        parent={s['stop_id']:s for s in stops if not s.get('parent_station') and ((s['stop_id'] in entities and (s['stop_name'] or '').lower() in item['text'].lower()) or s['stop_id'] in resolved or any(x['stop_id'] in resolved and x.get('parent_station')==s['stop_id'] for x in stops))}
        if not parent: raise ValueError('Could not resolve the named added stops to informed stations.')
        edits=[]; mappings=[]
        requested=interp.get('direction_id')
        if requested not in ('','0','1',None):raise ValueError('Unsupported direction for added-stop check.')
        for direction in ((requested,) if requested else ('0','1')):
            trips=[dict(r) for r in db.execute('select trip_id from trips where route_id=? and direction_id=?',(route,direction))]
            if not trips: continue
            tids=[t['trip_id'] for t in trips];marks=','.join('?' for _ in tids)
            rows=db.execute('select trip_id,stop_id,stop_sequence,arrival_time,departure_time from stop_times where trip_id in ('+marks+') order by trip_id,cast(stop_sequence as integer)',tids).fetchall()
            patterns={}
            for r in rows:patterns.setdefault(r['trip_id'],[]).append(dict(r))
            target_ids={s['stop_id'] for s in stops if s['parent_station'] in parent}
            templates=[]
            for tid,seq in patterns.items():
                ix=[j for j,r in enumerate(seq) if r['stop_id'] in target_ids]
                if len(ix)!=len(parent) or sorted(ix)!=list(range(min(ix),max(ix)+1)) or min(ix)<1 or max(ix)+1>=len(seq):continue
                section=seq[min(ix)-1:max(ix)+2]; a=seconds(section[0]['departure_time'] or section[0]['arrival_time']);b=seconds(section[-1]['arrival_time'] or section[-1]['departure_time'])
                times=[seconds(x['arrival_time'] or x['departure_time']) for x in section[1:-1]]
                if a is None or b is None or b<=a or any(t is None or not a<=t<=b for t in times): continue
                templates.append((section[0]['stop_id'],section[-1]['stop_id'],[(r['stop_id'],(t-a)/(b-a)) for r,t in zip(section[1:-1],times)],tid))
            if not templates: raise ValueError('No complete local reference pattern could be resolved for direction '+direction+'; partial patterns need review.')
            groups={}
            for x in templates:groups.setdefault((x[0],x[1],tuple(s for s,_ in x[2])),[]).append(x)
            best=max(groups.values(),key=len); first,last,targets,_=best[0]
            # Median fractions from local patterns with this exact station order.
            targets=[(sid,sorted(x[2][j][1] for x in best)[len(best)//2]) for j,(sid,_) in enumerate(targets)]
            mappings.extend({'station_id':next(s['parent_station'] for s in stops if s['stop_id']==sid),'target_stop_id':sid,'current_stop_id':'','station_name':next(s['stop_name'] for s in stops if s['stop_id']==sid)} for sid,_ in targets)
            expanded=[{'start':(dt.datetime.fromisoformat(w['start'])-dt.timedelta(minutes=30)).isoformat(),'end':(dt.datetime.fromisoformat(w['end'])+dt.timedelta(minutes=30)).isoformat()} for w in windows]
            matches=structured_checks.check_skips('stops_skipped',route,direction,[first],interp['timezone'],expanded,include_all=True)
            if matches['missing_time_records'] or matches['frequency_templates_excluded']:raise ValueError('Added-stop candidates contain missing times or frequency service.')
            bounds=[(dt.datetime.fromisoformat(w['start']),dt.datetime.fromisoformat(w['end'])) for w in modeling_policy.expanded_windows(windows)]
            for record in matches['sample_records']:
                seq=patterns[record['trip_id']]; pos=next((j for j,r in enumerate(seq) if r['stop_id']==first and r['stop_sequence']==record['stop_sequence']),None)
                if pos is None or pos+1>=len(seq) or seq[pos+1]['stop_id']!=last:continue
                a=seconds(seq[pos]['departure_time'] or seq[pos]['arrival_time']);b=seconds(seq[pos+1]['arrival_time'] or seq[pos+1]['departure_time'])
                if a is None or b is None or b<=a: raise ValueError('Express trip has missing or inconsistent anchor times.')
                added=[]; intersects=False
                for sid,fraction in targets:
                    value=round(a+(b-a)*fraction);when=dt.datetime.combine(dt.date.fromisoformat(record['service_date']),dt.time())+dt.timedelta(seconds=value)
                    if any(start<=when<end for start,end in bounds):intersects=True
                    added.append({'stop_id':sid,'time':clock(value)})
                if intersects:edits.append({'trip_id':record['trip_id'],'service_date':record['service_date'],'after_sequence':record['stop_sequence'],'added':added,'direction_id':direction})
        if not edits:raise ValueError('No express trips needing the named local stops were found in the alert windows.')
        return edits,mappings
