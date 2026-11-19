"""Create a local, date-scoped terminal-closure scenario from a GTFS ZIP."""
import csv, datetime as dt, hashlib, io, json, os, zipfile
import workbench

ALLOWED={'agency.txt','routes.txt','stops.txt','trips.txt','stop_times.txt','calendar.txt','calendar_dates.txt','shapes.txt','feed_info.txt'}

def csv_read(z,name):
    if name not in z.namelist(): return [],[]
    with z.open(name) as f:
        reader=csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig',newline=''))
        return reader.fieldnames,list(reader)

def csv_bytes(headers,rows):
    output=io.StringIO(newline='');w=csv.DictWriter(output,fieldnames=headers,lineterminator='\n');w.writeheader();w.writerows(rows);return output.getvalue().encode('utf-8')

def create(route_id,stop_ids,terminal_ids,start,end,output_dir):
    startday=dt.date.fromisoformat(start);endday=dt.date.fromisoformat(end)
    if endday<startday or (endday-startday).days>366: raise ValueError('Invalid date range.')
    if not stop_ids or not terminal_ids: raise ValueError('Stop and terminal IDs are required.')
    dates=[startday+dt.timedelta(days=i) for i in range((endday-startday).days+1)]
    affected={}
    with workbench.connect() as db:
        source=db.execute('select source from metadata').fetchone()[0]
        for day in dates:
            key=day.strftime('%Y%m%d');weekday=('monday','tuesday','wednesday','thursday','friday','saturday','sunday')[day.weekday()]
            active='''((exists(select 1 from calendar c where c.service_id=t.service_id and c.start_date<=? and c.end_date>=? and c."'''+weekday+'''"='1') and not exists(select 1 from calendar_dates e where e.service_id=t.service_id and e.date=? and e.exception_type='2')) or exists(select 1 from calendar_dates e where e.service_id=t.service_id and e.date=? and e.exception_type='1'))'''
            sql='select distinct t.trip_id,t.service_id from trips t where t.route_id=? and '+active+' and exists(select 1 from stop_times st where st.trip_id=t.trip_id and st.stop_id in ('+','.join('?' for _ in stop_ids)+'))'
            for r in db.execute(sql,[route_id,key,key,key,key]+stop_ids): affected.setdefault(r['trip_id'],{'service':r['service_id'],'dates':set()})['dates'].add(key)
    if not affected: raise ValueError('No matching trips found.')
    with zipfile.ZipFile(source) as z:
        if any(n.lower() not in ALLOWED for n in z.namelist()): raise ValueError('Unsupported optional or extension files require dependency handling before editing.')
        th,trips=csv_read(z,'trips.txt'); sh,stop_times=csv_read(z,'stop_times.txt'); ch,calendar=csv_read(z,'calendar.txt'); dh,exceptions=csv_read(z,'calendar_dates.txt')
        if not dh: dh=['service_id','date','exception_type']
        triprows={r['trip_id']:r for r in trips}; groups={}
        for row in stop_times:
            if row['trip_id'] in affected: groups.setdefault(row['trip_id'],[]).append(row)
        for trip,rows in groups.items():
            rows.sort(key=lambda x:int(x['stop_sequence']))
            if len(rows)<3: raise ValueError('Affected trip has too few stops: '+trip)
            first,last=rows[0]['stop_id'] in stop_ids,rows[-1]['stop_id'] in stop_ids
            if first==last or (first and rows[1]['stop_id'] not in terminal_ids) or (last and rows[-2]['stop_id'] not in terminal_ids): raise ValueError('Closed stop is not an unambiguous endpoint adjacent to the selected terminal: '+trip)
            if sum(r['stop_id'] in stop_ids for r in rows)!=1: raise ValueError('Trip visits a closed stop more than once: '+trip)
        if set(groups)!=set(affected): raise ValueError('Some affected trips lack stop times.')
        orig_service={r['service_id'] for r in calendar}|{r['service_id'] for r in exceptions}; used_trips=set(triprows)
        signature=hashlib.sha256((route_id+start+end+','.join(sorted(stop_ids))).encode()).hexdigest()[:10]
        def unique(base,used):
            v=base;i=0
            while v in used: i+=1;v=base+'_'+str(i)
            used.add(v);return v
        services=sorted({a['service'] for a in affected.values()});sid={}
        for index,s in enumerate(services): sid[s]=(unique('wb_'+signature+'_base_'+str(index),orig_service),unique('wb_'+signature+'_edit_'+str(index),orig_service))
        maps={trip:unique('wb_'+signature+'_'+trip,used_trips) for trip in sorted(affected)}
        calendar_by={r['service_id']:r for r in calendar}
        if len(calendar_by)!=len(calendar): raise ValueError('Duplicate service_id in calendar.txt.')
        # All trips on a service share effective dates; reject any inconsistent result.
        affected_dates={}
        for a in affected.values():
            prior=affected_dates.setdefault(a['service'],a['dates'])
            if prior!=a['dates']: raise ValueError('Inconsistent selected service dates.')
        derived_calendar=list(calendar)
        if ch:
            for old,(base,edited) in sid.items():
                if old in calendar_by: row=dict(calendar_by[old]);row['service_id']=base;derived_calendar.append(row)
        derived_exc=list(exceptions)
        for old,(base,edited) in sid.items():
            removed=affected_dates[old]
            for row in exceptions:
                if row['service_id']==old and row['date'] not in removed:
                    copy=dict(row);copy['service_id']=base;derived_exc.append(copy)
            cal=calendar_by.get(old)
            for d in sorted(removed):
                day=dt.datetime.strptime(d,'%Y%m%d').date(); weekday=('monday','tuesday','wednesday','thursday','friday','saturday','sunday')[day.weekday()]
                if cal and cal['start_date']<=d<=cal['end_date'] and cal.get(weekday)=='1':
                    entry={k:'' for k in dh};entry.update(service_id=base,date=d,exception_type='2');derived_exc.append(entry)
                entry={k:'' for k in dh};entry.update(service_id=edited,date=d,exception_type='1');derived_exc.append(entry)
        revised_trips=[]
        for row in trips:
            if row['trip_id'] not in affected: revised_trips.append(row);continue
            original=dict(row);original['service_id']=sid[row['service_id']][0];revised_trips.append(original)
            clone=dict(row);clone['trip_id']=maps[row['trip_id']];clone['service_id']=sid[row['service_id']][1]
            # Original geometry includes the closed station; omit the optional shape from the clone.
            if 'shape_id' in clone: clone['shape_id']=''
            revised_trips.append(clone)
        revised_stops=list(stop_times)
        for trip,rows in groups.items():
            retained=rows[1:] if rows[0]['stop_id'] in stop_ids else rows[:-1]
            for row in retained:
                clone=dict(row);clone['trip_id']=maps[trip]
                # Shape distances no longer describe a supplied shape.
                if 'shape_dist_traveled' in clone: clone['shape_dist_traveled']=''
                revised_stops.append(clone)
        replacements={'trips.txt':csv_bytes(th,revised_trips),'stop_times.txt':csv_bytes(sh,revised_stops),'calendar_dates.txt':csv_bytes(dh,derived_exc)}
        if ch: replacements['calendar.txt']=csv_bytes(ch,derived_calendar)
        os.makedirs(output_dir,exist_ok=True);out=os.path.join(output_dir,'scenario-'+signature+'.zip');temp=out+'.tmp'
        try:
            with zipfile.ZipFile(temp,'w') as w:
                for info in z.infolist(): w.writestr(info,replacements.pop(info.filename,z.read(info.filename)))
                for name,data in replacements.items(): w.writestr(name,data,compress_type=zipfile.ZIP_DEFLATED)
            os.replace(temp,out)
        finally:
            if os.path.exists(temp): os.unlink(temp)
    with open(out,'rb') as f: digest=hashlib.sha256(f.read()).hexdigest()
    manifest={'output':out,'sha256':digest,'source':source,'route_id':route_id,'start':start,'end_inclusive':end,'affected_trips':len(affected),'service_id_mapping':sid,'trip_id_mapping':maps,'removed_stop_ids':stop_ids,'terminal_stop_ids':terminal_ids,'note':'Modified clones keep original block IDs, with disjoint service dates. Optional shape IDs and shape distances are blanked for changed trip variants; original shapes and unaffected trips remain unchanged. This is a draft; independent GTFS validation has not run.'}
    return manifest
