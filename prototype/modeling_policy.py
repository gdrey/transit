"""Trip-scoping and conflict rules for schedule drafts."""
import datetime as dt
import os
import workbench, structured_checks

# The alert's exact times remain visible; a short configurable margin includes
# trips close to a boundary without claiming that the source specified it.
def grace_seconds():
    try: n=int(os.environ.get('GTFS_ALERT_GRACE_SECONDS','60'))
    except ValueError: raise ValueError('GTFS_ALERT_GRACE_SECONDS must be an integer.')
    if not 0 <= n <= 900: raise ValueError('GTFS_ALERT_GRACE_SECONDS must be between 0 and 900.')
    return n

def expanded_windows(windows):
    grace=dt.timedelta(seconds=grace_seconds()); ranges=[]
    for w in windows:
        start=dt.datetime.fromisoformat(w['start'])-grace;end=dt.datetime.fromisoformat(w['end'])+grace
        if ranges and start<=ranges[-1][1]:ranges[-1]=(ranges[-1][0],max(ranges[-1][1],end))
        else:ranges.append((start,end))
    return [{'start':a.isoformat(),'end':b.isoformat()} for a,b in ranges]

def matching_trip_stops(route_id,direction_id,stop_ids,timezone,windows):
    checked=structured_checks.check_skips('stops_skipped',route_id,direction_id,stop_ids,timezone,expanded_windows(windows),include_all=True)
    if checked['missing_time_records'] or checked['frequency_templates_excluded']:
        raise ValueError('Missing stop times or frequency-based trips require review before editing.')
    selected={(r['trip_id'],r['service_date']) for r in checked['sample_records']}
    with workbench.connect() as db:
        q=','.join('?' for _ in stop_ids)
        rows=db.execute('select t.trip_id,t.service_id,t.trip_headsign,st.stop_id,st.stop_sequence,st.arrival_time,st.departure_time from trips t join stop_times st on st.trip_id=t.trip_id where t.route_id=? and t.direction_id=? and st.stop_id in ('+q+')',[route_id,direction_id]+stop_ids).fetchall()
    by_trip={}
    for r in rows:by_trip.setdefault(r['trip_id'],[]).append(r)
    results=[]
    for trip,day in sorted(selected):
        for row in by_trip.get(trip,[]):
            time=row['arrival_time'] or row['departure_time']
            if not time:raise ValueError('Selected trip has an affected stop without a time; review is required.')
            try:h,m,s=map(int,time.split(':'));when=dt.datetime.combine(dt.date.fromisoformat(day),dt.time())+dt.timedelta(hours=h,minutes=m,seconds=s)
            except (ValueError,TypeError):raise ValueError('Invalid stop time on selected trip.')
            results.append({'trip_id':trip,'service_id':row['service_id'],'service_date':day,'stop_id':row['stop_id'],'stop_sequence':row['stop_sequence'],'local_time':when.isoformat(),'headsign':row['trip_headsign']})
    return results

def merge_edit(slot, seq, value, trip, day, updated):
    prior=slot.get(seq)
    if not prior:
        value['change_ids']=[value['change_id']];slot[seq]=value;return
    if (prior.get('action'),prior.get('old'),prior.get('new'),prior.get('time')) == (value.get('action'),value.get('old'),value.get('new'),value.get('time')):
        if value['change_id'] not in prior['change_ids']:prior['change_ids'].append(value['change_id'])
        return
    if prior.get('old')==value.get('old') and {prior.get('action'),value.get('action')}=={'skip','platform'}:
        platform=prior if prior['action']=='platform' else value
        prior['action']='platform_skip';prior['new']=platform['new']
        if value['change_id'] not in prior['change_ids']:prior['change_ids'].append(value['change_id'])
        return
    left=prior['change_id'];right=value['change_id'];a=updated.get(left);b=updated.get(right)
    recent=(' More recently updated: '+(left if a>b else right)+'.') if a and b and a!=b else ' Publication order is unavailable or tied.'
    raise ValueError('Conflicting changes '+left+' and '+right+' affect the same trip on '+day+'. Neither conflict was modeled; choose which change to apply.'+recent)
