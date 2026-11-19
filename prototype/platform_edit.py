"""Write date-scoped platform variants to a new GTFS ZIP."""
import csv,datetime,hashlib,io,json,os,sqlite3,tempfile,uuid,zipfile
import workbench,structured_checks,platform_checks,assessment_queue,added_stop_plan,modeling_policy,review_changes

ALLOWED={'agency.txt','routes.txt','stops.txt','trips.txt','stop_times.txt','calendar.txt','calendar_dates.txt','shapes.txt','feed_info.txt','transfers.txt'}

def read_csv(z,n):
    if n not in z.namelist(): return [],[]
    with z.open(n) as f:
        r=csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig',newline=''));return r.fieldnames,list(r)

def csv_bytes(h,rows):
    out=io.StringIO(newline='');w=csv.DictWriter(out,fieldnames=h,lineterminator='\n');w.writeheader();w.writerows(rows);return out.getvalue().encode()

def create(data,selected,blocked=None,progress=None):
    def report(message, **details):
        if progress: progress(message, **details)
    if not selected: raise ValueError('Select at least one change to model.')
    if data['stale'] or assessment_queue.feed_hash()!=data['feed_sha256']:raise ValueError('The base schedule changed; reassess before modeling.')
    edits={};changes=[]
    updated={x['change_id']:(x['item'].get('updated_at') or 0) for x in selected}
    for index,entry in enumerate(selected,1):
        report('Checking scheduled platform records', change_id=entry['change_id'], current=index, total=len(selected))
        result=entry['assessment']; i=result.get('interpretation') or {}
        kind=i.get('change_type')
        added=kind=='stops_added' or (entry['item'].get('alert_type') or '').lower().find('express to local')>=0
        if added:
            occurrences,mappings=added_stop_plan.plan(entry)
            for occurrence in occurrences:
                key=(occurrence['trip_id'],occurrence['service_date']);slot=edits.setdefault(key,{})
                for j,stop in enumerate(occurrence['added'],1):
                    seq='add:'+occurrence['after_sequence']+':'+str(j)
                    modeling_policy.merge_edit(slot,seq,{'old':'','new':stop['stop_id'],'time':stop['time'],'action':'add','change_id':entry['change_id']},key[0],key[1],updated)
            changes.append({'change_id':entry['change_id'],'alert':entry['item']['text'],'mappings':mappings,'windows':i['windows'],'kind':'added_stops','note':'Added stop times are estimates based on relative timing of existing local trips; original anchor times are retained.'})
            continue
        if kind not in ('platform_change','stops_skipped','stops_added'):raise ValueError(entry['change_id']+': this change type cannot be drafted yet.')
        if kind=='platform_change':
            check=platform_checks.check(i['route_id'],i['direction_id'],i['stop_ids'],i['target_direction_id'],i['target_stop_ids'],i['timezone'],i['windows'])
            mapping=dict(zip(i['stop_ids'],i['target_stop_ids'])); mappings=check['mappings']
        else:
            mapping={sid:sid for sid in i['stop_ids']}
            with workbench.connect() as db:
                mappings=[]
                for sid in i['stop_ids']:
                    row=db.execute('select stop_name,parent_station from stops where stop_id=?',(sid,)).fetchone()
                    mappings.append({'station_id':row['parent_station'] or sid,'station_name':row['stop_name'],'current_stop_id':sid,'target_stop_id':sid})
        records=modeling_policy.matching_trip_stops(i['route_id'],i['direction_id'],i['stop_ids'],i['timezone'],i['windows'])
        if not records:raise ValueError(entry['change_id']+': no affected scheduled stop records were found.')
        for r in records:
            key=(r['trip_id'],r['service_date']);seq=r['stop_sequence'];new=mapping[r['stop_id']];slot=edits.setdefault(key,{})
            value={'old':r['stop_id'],'new':new,'action':'skip' if kind=='stops_skipped' else 'platform','change_id':entry['change_id']}
            modeling_policy.merge_edit(slot,seq,value,key[0],key[1],updated)
        changes.append({'change_id':entry['change_id'],'alert':entry['item']['text'],'mappings':mappings,'windows':i['windows'],'kind':kind})
    # A stop added by one alert can conflict with a skip alert even when the
    # original express trip had no stop record for the skip checker to find.
    skip_entries=[e for e in selected if (e['assessment'].get('interpretation') or {}).get('change_type')=='stops_skipped']
    if skip_entries:
        with workbench.connect() as db:
            for (trip,day),mapping in edits.items():
                added=[v for v in mapping.values() if v['action']=='add']
                if not added:continue
                trip_row=db.execute('select route_id,direction_id from trips where trip_id=?',(trip,)).fetchone()
                for entry in skip_entries:
                    i=entry['assessment']['interpretation']
                    if not trip_row or (trip_row['route_id'],trip_row['direction_id'])!=(i['route_id'],i['direction_id']):continue
                    bounds=[(datetime.datetime.fromisoformat(w['start']),datetime.datetime.fromisoformat(w['end'])) for w in modeling_policy.expanded_windows(i['windows'])]
                    for value in added:
                        if value['new'] not in i['stop_ids']:continue
                        h,m,sec=map(int,value['time'].split(':'));when=datetime.datetime.combine(datetime.date.fromisoformat(day),datetime.time())+datetime.timedelta(hours=h,minutes=m,seconds=sec)
                        if any(a<=when<b for a,b in bounds):
                            raise ValueError('Conflicting changes '+value['change_id']+' and '+entry['change_id']+' add and skip the same stop on the same trip. Neither conflict was modeled; choose which change to apply.')
    report('Finished checking changes; preparing the combined GTFS draft', completed=len(selected), total=len(selected))
    if not edits:raise ValueError('No affected scheduled platform records were found.')
    with workbench.connect() as db: source=db.execute('select source from metadata').fetchone()[0]
    version=uuid.uuid4().hex[:12]; prefix='wb_'+version+'_'
    outdir=os.path.join(workbench.DATA_DIR,'versions');os.makedirs(outdir,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='platform-',suffix='.tmp',dir=outdir);os.close(fd);output=os.path.join(outdir,'platform-'+version+'.zip')
    details=[]
    try:
      with zipfile.ZipFile(source) as z:
        unknown=[n for n in z.namelist() if n.lower() not in ALLOWED]
        if unknown:raise ValueError('Unsupported optional feed files require review before editing: '+', '.join(unknown))
        th,trips=read_csv(z,'trips.txt');ch,cal=read_csv(z,'calendar.txt');dh,exceptions=read_csv(z,'calendar_dates.txt')
        transfer_headers,transfers=read_csv(z,'transfers.txt')
        if any(row.get(k) for row in transfers for k in ('from_trip_id','to_trip_id')):raise ValueError('Trip-specific transfer references require review before editing.')
        if len({(r['service_id'],r['date']) for r in exceptions})!=len(exceptions):raise ValueError('Duplicate calendar date exceptions must be resolved before editing.')
        if not dh:dh=['service_id','date','exception_type']
        trip_by={r['trip_id']:r for r in trips};calendar_by={r['service_id']:r for r in cal}
        if len(trip_by)!=len(trips) or len(calendar_by)!=len(cal):raise ValueError('Duplicate trip or calendar IDs in feed.')
        affected={trip for trip,_ in edits}
        if not affected.issubset(trip_by):raise ValueError('An affected trip no longer exists in the source feed.')
        if any(trip_by[x].get('block_id') for x in affected):raise ValueError('Affected trip blocks require continuity validation before editing.')
        used_services={r['service_id'] for r in cal}|{r['service_id'] for r in exceptions};used_trips=set(trip_by)
        original_exceptions={}
        for r in exceptions:original_exceptions.setdefault(r['service_id'],[]).append(r)
        variants={}; derived_cal=list(cal);derived_exc=list(exceptions);revised=[];counter=0
        def unique(base,used):
            name=base;n=0
            while name in used:n+=1;name=base+'_'+str(n)
            used.add(name);return name
        by_trip={}
        for (trip,date),mapping in edits.items():by_trip.setdefault(trip,[]).append((date,mapping))
        report('Creating service date exceptions and trip variants in calendar and trips files', total_trips=len(affected))
        for row in trips:
            tid=row['trip_id']
            if tid not in affected:revised.append(row);continue
            counter+=1
            if counter == 1 or counter % 100 == 0 or counter == len(affected): report('Creating trip and service ID variants', trip_id=tid, trips_done=counter, total_trips=len(affected))
            service=row['service_id'];base=unique(prefix+'base_'+str(counter),used_services);date_groups={}
            for date,mapping in by_trip[tid]:
                signature=tuple(sorted((seq,json.dumps(v,sort_keys=True)) for seq,v in mapping.items()));date_groups.setdefault(signature,[]).append(date)
            affected_dates={date.replace('-','') for date,_ in by_trip[tid]}
            if service in calendar_by:
                clone=dict(calendar_by[service]);clone['service_id']=base;derived_cal.append(clone)
            for exc in original_exceptions.get(service,[]):
                if exc['date'] not in affected_dates:
                    clone=dict(exc);clone['service_id']=base;derived_exc.append(clone)
            for date in sorted(affected_dates):
                calrow=calendar_by.get(service);day=datetime.datetime.strptime(date,'%Y%m%d').date();days=['monday','tuesday','wednesday','thursday','friday','saturday','sunday']
                if calrow and calrow['start_date']<=date<=calrow['end_date'] and calrow[days[day.weekday()]]=='1':
                    exc={k:'' for k in dh};exc.update(service_id=base,date=date,exception_type='2');derived_exc.append(exc)
            old=dict(row);old['service_id']=base;revised.append(old);variants[tid]=[]
            for j,(signature,dates) in enumerate(date_groups.items()):
                sid=unique(prefix+'edit_'+str(counter)+'_'+str(j),used_services);newid=unique(prefix+'trip_'+str(counter)+'_'+str(j),used_trips)
                clone=dict(row);clone.update(trip_id=newid,service_id=sid);revised.append(clone)
                for date in dates:
                    exc={k:'' for k in dh};exc.update(service_id=sid,date=date.replace('-',''),exception_type='1');derived_exc.append(exc)
                    for seq,value in signature:
                        value=json.loads(value);newseq=str(int(seq.split(':')[1])*100+int(seq.split(':')[2])) if value['action']=='add' else (str(int(seq)*100) if any(x.startswith('add:') for x,_ in signature) else seq)
                        for change_id in value.get('change_ids',[value['change_id']]): details.append({'trip_id':tid,'new_trip_id':newid,'service_date':date,'stop_sequence':seq if value['action']!='add' else seq.split(':')[1],'new_stop_sequence':newseq,'before':value['old'],'after':value['new'],'action':value['action'],'change_id':change_id})
                variants[tid].append((newid,{seq:json.loads(value) for seq,value in signature}))
        with tempfile.TemporaryDirectory(dir=outdir) as tmpdir:
            report('Writing platform changes to stop_times.txt for the new trip IDs')
            stopfile=os.path.join(tmpdir,'stop_times.txt');seen=set()
            with z.open('stop_times.txt') as src,open(stopfile,'w',encoding='utf-8',newline='') as dst,sqlite3.connect(os.path.join(tmpdir,'variants.sqlite')) as spool:
                spool.execute('CREATE TABLE rows (trip_id TEXT, stop_sequence INTEGER, data TEXT)')
                reader=csv.DictReader(io.TextIOWrapper(src,encoding='utf-8-sig',newline='')); headers=list(reader.fieldnames)
                add_timepoint=any(v['action']=='add' for m in edits.values() for v in m.values()) and 'timepoint' not in headers
                fields=('pickup_type','drop_off_type')+(('timepoint',) if add_timepoint else ())
                for field in fields:
                    if field not in headers:headers.append(field)
                writer=csv.DictWriter(dst,fieldnames=headers,lineterminator='\n');writer.writeheader()
                def spool_row(record):spool.execute('INSERT INTO rows VALUES (?,?,?)',(record['trip_id'],int(record['stop_sequence']),json.dumps(record,ensure_ascii=False)))
                for row in reader:
                    if add_timepoint:row['timepoint']='1'
                    writer.writerow(row)
                    for newid,mapping in variants.get(row['trip_id'],[]):
                        clone=dict(row);clone['trip_id']=newid;seq=row['stop_sequence'];has_added=any(k.startswith('add:') for k in mapping)
                        if has_added:clone['stop_sequence']=str(int(seq)*100)
                        if seq in mapping:
                            change=mapping[seq]
                            if row['stop_id']!=change['old']:raise ValueError('Source stop record changed during editing.')
                            if change['action'] in ('skip','platform_skip'):clone['pickup_type']='1';clone['drop_off_type']='1'
                            if change['action'] in ('platform','platform_skip'):clone['stop_id']=change['new']
                            seen.add((newid,seq))
                        spool_row(clone)
                        for key,change in sorted(mapping.items()):
                            if change['action']!='add' or key.split(':')[1]!=seq:continue
                            addition={k:'' for k in headers};addition.update(trip_id=newid,stop_id=change['new'],stop_sequence=str(int(seq)*100+int(key.split(':')[2])),arrival_time=change['time'],departure_time=change['time'],pickup_type='0',drop_off_type='0',timepoint='0')
                            spool_row(addition);seen.add((newid,key))
                spool.commit()
                for variant_row in spool.execute('SELECT data FROM rows ORDER BY trip_id,stop_sequence'):
                    writer.writerow(json.loads(variant_row[0]))
            expected={(newid,seq) for vs in variants.values() for newid,m in vs for seq in m}
            if seen!=expected:raise ValueError('Some selected stop records could not be applied.')
            replacements={'trips.txt':csv_bytes(th,revised),'calendar_dates.txt':csv_bytes(dh,derived_exc)}
            if ch:replacements['calendar.txt']=csv_bytes(ch,derived_cal)
            report('Packaging GTFS files into a draft ZIP')
            with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED) as dest:
                for info in z.infolist():
                    if info.filename=='stop_times.txt':dest.write(stopfile,'stop_times.txt')
                    elif info.filename in replacements:dest.writestr(info,replacements.pop(info.filename))
                    else:dest.writestr(info,z.read(info.filename))
                for name,content in replacements.items():dest.writestr(name,content)
      report('Checking draft ZIP and reading back changed trip and stop records')
      with zipfile.ZipFile(temp) as verify:
        if verify.testzip():raise ValueError('Generated ZIP failed integrity checks.')
        h,rows=read_csv(verify,'trips.txt');outtrips={r['trip_id']:r for r in rows}
        for trip in affected:
            if outtrips[trip]['service_id'] == trip_by[trip]['service_id']:raise ValueError('Original trip service was not partitioned.')
        with verify.open('stop_times.txt') as f:
            r=csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig',newline=''));found={(x['trip_id'],x['stop_sequence']):x for x in r if x['trip_id'].startswith(prefix+'trip_')}
        for detail in details:
            row=found.get((detail['new_trip_id'],detail['new_stop_sequence']))
            if not row or row['stop_id']!=detail['after'] or (detail['action'] in ('skip','platform_skip') and (row.get('pickup_type')!='1' or row.get('drop_off_type')!='1')):raise ValueError('Output readback does not contain a selected stop change.')
      if assessment_queue.feed_hash()!=data['feed_sha256']:raise ValueError('The base schedule changed while the draft was being built; retry.')
      report('Saving the draft GTFS and activity record')
      os.replace(temp,output)
      digest=hashlib.sha256()
      with open(output,'rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):digest.update(part)
      result={'version':version,'output':output,'sha256':digest.hexdigest(),'base_sha256':data['feed_sha256'],'batch_id':data['id'],'changes':changes,'blocked_changes':blocked or [],'records':details,'selection_signature':review_changes.selection_signature(data,[x for x in data['items'] if x.get('decision')=='yes']),'status':'draft','policy':{'trip_scope':'whole trip when any affected stop is in an alert window','boundary_grace_seconds':modeling_policy.grace_seconds(),'conflicts':'conflicting changes require user review'},'note':'Stop changes and service date variants were checked by reading the output ZIP. Added stop times are estimates from existing local trips. Shapes and station transfers are preserved; track geometry and independent GTFS validation still require review.'}
      manifest=os.path.join(outdir,'platform-'+version+'.json')
      with open(manifest,'w',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False)
      return result
    finally:
      if os.path.exists(temp):os.unlink(temp)
