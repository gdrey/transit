"""Conservative local analysis of simple station-closure alert text."""
import datetime as dt
import re
import uuid
import workbench
import closure_check
import edit_preflight

MONTHS={x.lower():i for i,x in enumerate(('January','February','March','April','May','June','July','August','September','October','November','December'),1)}

def analyze(text, source='pasted text'):
    result={'id':'alert-'+uuid.uuid4().hex[:10], 'text':text,'source':source,'status':'inconclusive','evidence':[], 'plan':[], 'output':'No new feed created', 'method':'Rule-based prototype; no language model used'}
    if not re.search(r'\b(clos(?:ed|ure)|not served|no service)\b',text,re.I):
        result['evidence'].append('No supported station-closure wording found.');result['status']='unsupported';return result
    dates=[]
    for match in re.finditer(r'\b(20\d{2}-\d{2}-\d{2})\b',text):
        try: dates.append(dt.date.fromisoformat(match.group(1)))
        except ValueError: pass
    for match in re.finditer(r'\b('+'|'.join(MONTHS)+r')\s+(\d{1,2}),?\s+(20\d{2})\b',text,re.I):
        try: dates.append(dt.date(int(match.group(3)),MONTHS[match.group(1).lower()],int(match.group(2))))
        except ValueError: pass
    dates=sorted(set(dates))
    with workbench.connect() as db:
        candidates=[]
        for row in db.execute('select stop_id,stop_name from stops where stop_name<>\'\''):
            name=re.sub(r'\s+',' ',row['stop_name'].strip())
            if len(name)>=8 and name.casefold() in text.casefold(): candidates.append(dict(row))
        names={x['stop_name'].casefold() for x in candidates}
        if len(names)!=1:
            result['evidence'].append('Could not uniquely match a complete station name to the feed; resolve the stop IDs.');return result
        ids=[x['stop_id'] for x in candidates]
        sql='select distinct r.* from routes r join trips t on t.route_id=r.route_id join stop_times s on s.trip_id=t.trip_id where s.stop_id in ('+','.join('?' for _ in ids)+')'
        routes=[dict(r) for r in db.execute(sql,ids)]
    result['matched_stops']=candidates
    if len(routes)!=1:
        result['evidence'].append('The station is associated with '+str(len(routes))+' routes; resolve the affected route before checking.');result['candidate_routes']=routes[:20];return result
    route=routes[0];result['matched_route']=route
    if len(dates)!=2:
        result['evidence'].append('A start and end date with years are required; duration, relative dates, and approximate end dates require review.');return result
    try:
        report=closure_check.check(route['route_id'],ids,dates[0].isoformat(),dates[1].isoformat(),source)
    except ValueError as e:
        result['evidence'].append(str(e));return result
    result['check']=report
    result['status']='not_modeled' if report['status']=='not_modeled' else 'inconclusive'
    result['evidence'].append('Checked stop-time references on '+str(len(report['daily_evidence']))+' service dates. Absence of references does not prove the complete service change is modeled.')
    if result['status']=='not_modeled':
        # Resolve the union of trip templates active on at least one selected service date.
        affected={}
        with workbench.connect() as db:
            for day in report['daily_evidence']:
                date=dt.date.fromisoformat(day['service_date']); key=date.strftime('%Y%m%d')
                weekday=('monday','tuesday','wednesday','thursday','friday','saturday','sunday')[date.weekday()]
                active='''((exists(select 1 from calendar c where c.service_id=t.service_id and c.start_date<=? and c.end_date>=? and c."'''+weekday+'''"='1') and not exists(select 1 from calendar_dates e where e.service_id=t.service_id and e.date=? and e.exception_type='2')) or exists(select 1 from calendar_dates e where e.service_id=t.service_id and e.date=? and e.exception_type='1'))'''
                q='select distinct t.trip_id,t.service_id from trips t where t.route_id=? and '+active+' and exists(select 1 from stop_times st where st.trip_id=t.trip_id and st.stop_id in ('+','.join('?' for _ in ids)+'))'
                for row in db.execute(q,[route['route_id'],key,key,key,key]+ids):
                    affected.setdefault(row['trip_id'],{'service_id':row['service_id'],'dates':[]})['dates'].append(day['service_date'])
        services=sorted({v['service_id'] for v in affected.values()})
        result['affected_summary']={'trip_template_count':len(affected),'service_ids':services,'service_dates_start':dates[0].isoformat(),'service_dates_end_inclusive':dates[1].isoformat(),'stop_ids':ids,'example_trip_ids':list(affected)[:10]}
        preflight=edit_preflight.inspect(route['route_id'],ids,dates[0].isoformat(),dates[1].isoformat())
        result['preflight']=preflight
        result['plan']=[
            'Scope: route '+route['route_id']+', station stop IDs '+', '.join(ids)+', service dates '+dates[0].isoformat()+' through '+dates[1].isoformat()+' inclusive. '+str(len(affected))+' distinct scheduled trip IDs are affected; existing service IDs: '+', '.join(services)+'. Example trip IDs: '+', '.join(list(affected)[:5])+'.',
            'calendar.txt / calendar_dates.txt: for each affected service, partition its effective dates into affected and unaffected sets. Create new service IDs and calendar-date entries (or equivalent calendar rules) so the two sets are disjoint and preserve every original service date. Do not remove dates from the existing shared service IDs.',
            'trips.txt: where unaffected dates exist, assign each affected original trip to its unaffected-date service; otherwise omit it from the new schedule after checking dependencies. Clone it with a new trip ID and assign the clone to the affected-date service. Leave unrelated trips on their existing service IDs. Record the original-to-clone ID mapping.',
            'stop_times.txt: copy each cloned trip’s stop times, then apply the confirmed service change only to the clones. If trips must start or end at another station, identify the correct terminal and trim the stop sequence accordingly; do not assume that deleting the closed stop alone models the announcement.',
            ('shapes.txt: update or create the applicable changed-trip shape when reliable geometry is available, update the trips.txt shape_id and reconcile stop_times.txt shape_dist_traveled; preserve unaffected shapes.' if preflight['has_shapes'] else 'No shapes.txt exists in the source feed; do not create geometry without an authoritative source.'),
            'Dependencies: '+str(preflight['affected_block_count'])+' block IDs occur on affected trips; '+str(preflight['blocks_shared_with_other_trips'])+' also occur on other trip records. '+str(preflight['affected_shape_count'])+' shapes are referenced. Inspect date-aware block continuity and shape handling before cloning.',
            'Implementation status: blocked until date-specific block handling and shape preservation are implemented and verified. No feed has been modified.'
        ]
    return result
