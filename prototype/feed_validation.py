"""Compare two GTFS ZIPs and validate selected references; not a full GTFS validator."""
import collections,csv,datetime,hashlib,io,json,os,re,subprocess,tempfile,zipfile

REQUIRED={'agency.txt':('agency_name','agency_url','agency_timezone'),'routes.txt':('route_id','route_type'),'stops.txt':('stop_id','stop_name'),'trips.txt':('route_id','service_id','trip_id'),'stop_times.txt':('trip_id','stop_id','stop_sequence')}

def inspect(path):
    notices=collections.Counter(); files={}; rows={}; sets={}
    with zipfile.ZipFile(path) as z:
        bad=z.testzip()
        if bad:raise ValueError('ZIP integrity check failed for '+bad)
        names=z.namelist()
        if len(names)!=len(set(n.lower() for n in names)):raise ValueError('Duplicate ZIP member names.')
        for info in z.infolist():
            h=hashlib.sha256()
            with z.open(info) as f:
                for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
            files[info.filename]={'sha256':h.hexdigest(),'bytes':info.file_size}
        nameset=set(names)
        for n,headers in REQUIRED.items():
            if n not in nameset:notices['missing_file:'+n]+=1
        if not ({'calendar.txt','calendar_dates.txt'}&nameset):notices['missing_service_calendar']+=1
        def read(name):
            if name not in nameset:return []
            with z.open(name) as raw:
                reader=csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline=''))
                if not reader.fieldnames:raise ValueError('Empty CSV: '+name)
                if len(reader.fieldnames)!=len(set(reader.fieldnames)):raise ValueError('Duplicate CSV headers: '+name)
                for field in REQUIRED.get(name,()):
                    if field not in reader.fieldnames:notices['missing_header:'+name+':'+field]+=1
                result=list(reader);rows[name]=len(result);return result
        routes=read('routes.txt');stops=read('stops.txt');trips=read('trips.txt');cal=read('calendar.txt');exc=read('calendar_dates.txt');read('agency.txt')
        for name,data,key in [('routes',routes,'route_id'),('stops',stops,'stop_id'),('trips',trips,'trip_id')]:
            ids=[r.get(key,'') for r in data];notices['missing_'+key]+=sum(not x for x in ids);notices['duplicate_'+key]+=len([x for x,c in collections.Counter(ids).items() if x and c>1]);sets[name]=set(ids)
        services={r.get('service_id','') for r in cal+exc};sets['services']=services
        for t in trips:
            if t.get('route_id') not in sets['routes']:notices['trip_missing_route']+=1
            if t.get('service_id') not in services:notices['trip_missing_service']+=1
        for r in routes:
            for field in ('route_color','route_text_color'):
                val=r.get(field,'')
                if val and not re.fullmatch('[A-Fa-f0-9]{6}',val):notices['invalid_'+field]+=1
        if 'stop_times.txt' in nameset:
            count=0
            with z.open('stop_times.txt') as raw:
                reader=csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline=''))
                if not reader.fieldnames:raise ValueError('Empty CSV: stop_times.txt')
                for field in REQUIRED['stop_times.txt']:
                    if field not in reader.fieldnames:notices['missing_header:stop_times.txt:'+field]+=1
                for r in reader:
                    count+=1
                    if r.get('trip_id') not in sets['trips']:notices['stop_time_missing_trip']+=1
                    if r.get('stop_id') and r['stop_id'] not in sets['stops']:notices['stop_time_missing_stop']+=1
                    for field in ('arrival_time','departure_time'):
                        v=r.get(field,'')
                        if v and not re.fullmatch(r'\d+:([0-5]\d):([0-5]\d)',v):notices['invalid_'+field]+=1
                    for field in ('pickup_type','drop_off_type'):
                        if r.get(field,'') not in ('','0','1','2','3'):notices['invalid_'+field]+=1
            rows['stop_times.txt']=count
    return {'files':files,'row_counts':rows,'checks':{k:v for k,v in notices.items() if v}}

def verify_intended(base,path,items,service):
    expected={};manual=[]
    for item in items:
        if not item.get('applied'):continue
        for edit in item['edits']:expected[(edit['route_id'],edit['field'])]=edit['value']
    records=(service or {}).get('records',[]);wanted={(r['new_trip_id'],r.get('new_stop_sequence',r['stop_sequence'])) for r in records};found={}
    with zipfile.ZipFile(base) as orig:
        with orig.open('routes.txt') as raw:original_routes={r['route_id']:r for r in csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline=''))}
    with zipfile.ZipFile(path) as z:
        with z.open('routes.txt') as raw:
            routes={r['route_id']:r for r in csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline=''))}
        for (route,field),value in expected.items():
            actual=routes.get(route,{}).get(field,'')
            if actual!=value:raise ValueError('Output verification failed for route '+route+' '+field+'.')
            manual.append({'route_id':route,'field':field,'original':original_routes.get(route,{}).get(field,'') or '', 'output':actual})
        if records:
            new_ids={d['new_trip_id'] for d in records}
            with z.open('trips.txt') as raw:
                trip_services={r['trip_id']:r['service_id'] for r in csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline='')) if r.get('trip_id') in new_ids}
            def read_optional(name):
                if name not in z.namelist():return []
                with z.open(name) as raw:return list(csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline='')))
            weekly={r['service_id']:r for r in read_optional('calendar.txt')}
            exceptions={(r['service_id'],r['date']):r['exception_type'] for r in read_optional('calendar_dates.txt')}
            for d in records:
                service_id=trip_services.get(d['new_trip_id']);day=datetime.date.fromisoformat(d['service_date']);key=day.strftime('%Y%m%d');ex=exceptions.get((service_id,key));calendar=weekly.get(service_id,{})
                active=ex=='1' or (ex!='2' and calendar.get('start_date','')<=key<=calendar.get('end_date','') and calendar.get(('monday','tuesday','wednesday','thursday','friday','saturday','sunday')[day.weekday()])=='1')
                if not active:raise ValueError('Output trip is not active on the intended service date for '+d['change_id'])
            with z.open('stop_times.txt') as raw:
                for r in csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline='')):
                    key=(r['trip_id'],r['stop_sequence'])
                    if key in wanted:found[key]=r
            for d in records:
                r=found.get((d['new_trip_id'],d.get('new_stop_sequence',d['stop_sequence'])))
                if not r or r.get('stop_id')!=d['after'] or (d['action'] in ('skip','platform_skip') and (r.get('pickup_type')!='1' or r.get('drop_off_type')!='1')):raise ValueError('Output verification failed for '+d['change_id']+' trip '+d['new_trip_id'])
    return {'route_fields_verified':len(manual),'stop_records_verified':len(records),'service_dates_verified':len(records),'route_fields':manual,'note':'Checks selected route fields and changed stop records; does not prove all alert requirements or geometry.'}

def canonical(path,work):
    tools=os.path.join(os.path.dirname(os.path.abspath(__file__)),'.data','tools')
    jar=os.environ.get('GTFS_VALIDATOR_JAR',os.path.join(tools,'gtfs-validator-8.0.1-cli.jar'))
    bundled_java=os.path.join(tools,'java','Contents','Home','bin','java')
    java=os.environ.get('GTFS_VALIDATOR_JAVA',bundled_java if os.path.isfile(bundled_java) else 'java')
    if not jar or not os.path.isfile(jar):return {'status':'unavailable','reason':'MobilityData validator JAR and Java runtime are not configured.'}
    try:
        proc=subprocess.run([java,'-jar',jar,'-i',path,'-o',work,'-svu'],capture_output=True,text=True,timeout=300)
        report=os.path.join(work,'report.json');errors=os.path.join(work,'system_errors.json')
        if proc.returncode or not os.path.exists(report):return {'status':'failed','reason':'Canonical validator did not produce a report.'}
        with open(report,encoding='utf-8') as f:data=json.load(f)
        if os.path.isfile(errors):
            with open(errors,encoding='utf-8') as f:system=json.load(f)
            if system.get('notices') or system.get('systemErrors'):return {'status':'failed','reason':'Canonical validator reported system errors.'}
        return {'status':'completed','report':data}
    except (OSError,subprocess.TimeoutExpired,ValueError):return {'status':'failed','reason':'Canonical validator could not run; check Java, JAR, and logs.'}

def compare(base,output,items,service):
    before=inspect(base);after=inspect(output)
    changed=[]
    for name in sorted(set(before['files'])|set(after['files'])):
        a=before['files'].get(name);b=after['files'].get(name)
        if a!=b:changed.append({'file':name,'before_rows':before['row_counts'].get(name),'after_rows':after['row_counts'].get(name),'before_sha256':a and a['sha256'],'after_sha256':b and b['sha256']})
    fields=sorted(set(before['checks'])|set(after['checks']))
    checks=[{'rule':k,'original':before['checks'].get(k,0),'output':after['checks'].get(k,0),'delta':after['checks'].get(k,0)-before['checks'].get(k,0)} for k in fields]
    with tempfile.TemporaryDirectory(prefix='gtfs-check-') as d:
        a=os.path.join(d,'original');b=os.path.join(d,'output');os.mkdir(a);os.mkdir(b)
        independent={'original':canonical(base,a),'output':canonical(output,b)}
    canonical_deltas=[]
    if all(v['status']=='completed' for v in independent.values()):
        a={(x.get('code'),x.get('severity')):x.get('totalNotices',0) for x in independent['original']['report'].get('notices',[])}
        b={(x.get('code'),x.get('severity')):x.get('totalNotices',0) for x in independent['output']['report'].get('notices',[])}
        canonical_deltas=[{'rule':k[0],'severity':k[1],'original':a.get(k,0),'output':b.get(k,0),'delta':b.get(k,0)-a.get(k,0)} for k in sorted(set(a)|set(b)) if a.get(k,0)!=b.get(k,0)]
    return {'original':before,'output':after,'changed_files':changed,'check_deltas':checks,'intent':verify_intended(base,output,items,service),'canonical':independent,'canonical_deltas':canonical_deltas,'scope':'Basic local structural/reference checks and task readback, not full GTFS validation. Check-count deltas are not incident-level diffs.'}
