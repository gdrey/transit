"""Read paired schedule evidence from the original and generated GTFS ZIPs."""
import csv,datetime as dt,hashlib,io,json,os,zipfile
import assessment_queue,workbench


def latest():
    import pipeline_output
    output=pipeline_output.current()
    if not output or not output.get('service'):raise ValueError('The completed pipeline output contains no service change to compare.')
    data=dict(output['service']);data.update(output=output['source'],sha256=output['sha256'],version=output['version'])
    return data


def rows(z,name):
    with z.open(name) as f: return list(csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig',newline='')))


def stop_times(z,ids):
    result={k:[] for k in ids}
    with z.open('stop_times.txt') as f:
        for r in csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig',newline='')):
            if r['trip_id'] in ids:result[r['trip_id']].append(r)
    for v in result.values():v.sort(key=lambda x:int(x['stop_sequence']))
    return result


def moment(day,value):
    try:
        h,m,s=map(int,value.split(':'))
        return dt.datetime.combine(dt.date.fromisoformat(day),dt.time())+dt.timedelta(hours=h,minutes=m,seconds=s)
    except (ValueError,TypeError,AttributeError):return None


def active(z,trip,date):
    key=date.replace('-',''); exceptions=rows(z,'calendar_dates.txt') if 'calendar_dates.txt' in z.namelist() else []
    for x in exceptions:
        if x['service_id']==trip['service_id'] and x['date']==key:return x['exception_type']=='1'
    if 'calendar.txt' not in z.namelist():return False
    day=dt.date.fromisoformat(date);weekday=['monday','tuesday','wednesday','thursday','friday','saturday','sunday'][day.weekday()]
    return any(r['service_id']==trip['service_id'] and r['start_date']<=key<=r['end_date'] and r[weekday]=='1' for r in rows(z,'calendar.txt'))


def build(change_id,date=None,direction=None):
    data=latest(); change=next((x for x in data['changes'] if x['change_id']==change_id),None)
    if not change:raise ValueError('Change ID is not included in the current draft.')
    mappings={(x['current_stop_id'],x['target_stop_id']) for x in change['mappings']}
    candidates=[r for r in data['records'] if r.get('change_id')==change_id or (not r.get('change_id') and (r['before'],r['after']) in mappings)]
    if not candidates:raise ValueError('No changed stop records found for this change.')
    with workbench.connect() as db:source=db.execute('select source from metadata').fetchone()[0]
    with zipfile.ZipFile(source) as original,zipfile.ZipFile(data['output']) as revised:
        ids={x['trip_id'] for x in candidates}; old=stop_times(original,ids)
        windows=[(dt.datetime.fromisoformat(w['start']),dt.datetime.fromisoformat(w['end'])) for w in change['windows']]
        filtered=[]
        for r in candidates:
            if r.get('change_id'): filtered.append(r);continue
            row=next((x for x in old[r['trip_id']] if x['stop_sequence']==r['stop_sequence'] and x['stop_id']==r['before']),None)
            when=moment(r['service_date'],(row.get('arrival_time') or row.get('departure_time')) if row else None)
            if when and any(a<=when<b for a,b in windows):filtered.append(r)
        dates=sorted({r['service_date'] for r in filtered})
        if not dates:raise ValueError('No changed stop occurrences fall inside this change’s windows.')
        selected=date or dates[0]
        if selected not in dates:raise ValueError('No changed trips for the selected service date.')
        chosen=[r for r in filtered if r['service_date']==selected]
        pairs={r['trip_id']:r['new_trip_id'] for r in chosen}; new=stop_times(revised,set(pairs.values()))
        oldtrips={x['trip_id']:x for x in rows(original,'trips.txt') if x['trip_id'] in pairs};newtrips={x['trip_id']:x for x in rows(revised,'trips.txt') if x['trip_id'] in set(pairs.values())}
        directions=sorted({r.get('direction_id','') for r in oldtrips.values()})
        selected_direction=direction if direction in directions else directions[0]
        pairs={old_id:new_id for old_id,new_id in pairs.items() if oldtrips[old_id].get('direction_id','')==selected_direction}
        oldstops={x['stop_id']:x for x in rows(original,'stops.txt')};newstops={x['stop_id']:x for x in rows(revised,'stops.txt')}
        trip_pairs=[]
        for before_id,after_id in pairs.items():
            if before_id not in oldtrips or after_id not in newtrips or not active(original,oldtrips[before_id],selected) or not active(revised,newtrips[after_id],selected):raise ValueError('A compared trip is not active on the selected service date in both feeds.')
            def sequence(records,stops):
                return [{'sequence':r['stop_sequence'],'stop_id':r['stop_id'],'station_id':(stops.get(r['stop_id']) or {}).get('parent_station') or r['stop_id'],'name':(stops.get(r['stop_id']) or {}).get('stop_name',r['stop_id']),'time':r.get('departure_time') or r.get('arrival_time') or '', 'pickup_type':r.get('pickup_type') or '0', 'drop_off_type':r.get('drop_off_type') or '0'} for r in records]
            a=sequence(old[before_id],oldstops); b=sequence(new[after_id],newstops)
            for r in (x for x in chosen if x['trip_id']==before_id):
                if not any(x['sequence']==r.get('new_stop_sequence',r['stop_sequence']) and x['stop_id']==r['after'] and (r.get('action') not in ('skip','platform_skip') or (x['pickup_type']=='1' and x['drop_off_type']=='1')) for x in b):raise ValueError('Draft stop record does not match the change manifest.')
            trip_pairs.append({'original_id':before_id,'draft_id':after_id,'original':a,'draft':b,'changed_sequences':[r['stop_sequence'] for r in chosen if r['trip_id']==before_id and r.get('action')!='add'],'draft_changed_sequences':[r.get('new_stop_sequence',r['stop_sequence']) for r in chosen if r['trip_id']==before_id]})
        first=oldtrips[next(iter(pairs))]
        return {'change_id':change_id,'alert':change['alert'],'date':selected,'dates':dates,'route_id':first['route_id'],'direction_id':first.get('direction_id',''),'directions':directions,'mappings':change['mappings'],'pairs':trip_pairs,'version':data['version'],'sha256':data['sha256'],'blocked_changes':data.get('blocked_changes',[]),'kind':change.get('kind','platform_change'),'note':('Added stop times are estimated from existing local schedules; review them before use. ' if change.get('kind')=='added_stops' else '')+'Shows affected trip occurrences and nearby stops from the actual original and draft GTFS ZIPs. Service-day times may exceed 24:00. Track geometry requires review; see the output validation report for validation status.'}
