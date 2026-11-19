"""Inspect whether a proposed date-scoped terminal change can be applied safely."""
import csv
import datetime as dt
import io
import sqlite3
import zipfile
import workbench

KNOWN={'agency.txt','routes.txt','stops.txt','trips.txt','stop_times.txt','calendar.txt','calendar_dates.txt','shapes.txt','feed_info.txt'}

def inspect(route_id, stop_ids, start, end):
    begin=dt.date.fromisoformat(start);finish=dt.date.fromisoformat(end)
    if finish<begin or (finish-begin).days>366: raise ValueError('Specify a valid interval no longer than 367 service dates.')
    affected=set()
    with workbench.connect() as db:
        path=db.execute('select source from metadata').fetchone()[0]
        for delta in range((finish-begin).days+1):
            day=begin+dt.timedelta(days=delta);date=day.strftime('%Y%m%d');weekday=('monday','tuesday','wednesday','thursday','friday','saturday','sunday')[day.weekday()]
            active='''((exists(select 1 from calendar c where c.service_id=t.service_id and c.start_date<=? and c.end_date>=? and c."'''+weekday+'''"='1') and not exists(select 1 from calendar_dates e where e.service_id=t.service_id and e.date=? and e.exception_type='2')) or exists(select 1 from calendar_dates e where e.service_id=t.service_id and e.date=? and e.exception_type='1'))'''
            sql='select distinct t.trip_id from trips t where t.route_id=? and '+active+' and exists(select 1 from stop_times s where s.trip_id=t.trip_id and s.stop_id in ('+','.join('?' for _ in stop_ids)+'))'
            affected.update(r[0] for r in db.execute(sql,[route_id,date,date,date,date]+stop_ids))
        stops={}
        for trip in affected:
            stops[trip]=[tuple(r) for r in db.execute('select s.stop_id,p.stop_name,s.stop_sequence from stop_times s left join stops p on p.stop_id=s.stop_id where s.trip_id=? order by cast(s.stop_sequence as integer)',(trip,))]
    with zipfile.ZipFile(path) as z:
        files=z.namelist(); unknown=[x for x in files if x.lower() not in KNOWN]
        with z.open('trips.txt') as raw:
            trips=list(csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline='')))
        targets=[r for r in trips if r.get('trip_id') in affected]
        blocks={r.get('block_id') for r in targets if r.get('block_id')}
        shared={r.get('block_id') for r in trips if r.get('trip_id') not in affected and r.get('block_id') in blocks}
        shape_ids={r.get('shape_id') for r in targets if r.get('shape_id')}
        ends=sum(1 for seq in stops.values() if seq and (seq[0][0] in stop_ids or seq[-1][0] in stop_ids))
        adjacent={}
        for seq in stops.values():
            if len(seq)<2: continue
            if seq[0][0] in stop_ids: adjacent[(seq[1][0],seq[1][1])]=adjacent.get((seq[1][0],seq[1][1]),0)+1
            if seq[-1][0] in stop_ids: adjacent[(seq[-2][0],seq[-2][1])]=adjacent.get((seq[-2][0],seq[-2][1]),0)+1
        return {'status':'blocked','route_id':route_id,'start':start,'end_inclusive':end,'trip_count':len(affected),'endpoint_trip_count':ends,'affected_block_count':len(blocks),'blocks_shared_with_other_trips':len(shared),'affected_shape_count':len(shape_ids),'has_shapes':any(x.lower()=='shapes.txt' for x in files),'adjacent_stops':[{'stop_id':k[0],'name':k[1],'trip_count':v} for k,v in sorted(adjacent.items())],'other_files_requiring_inspection':unknown,'blockers':['Date-specific block continuity and ID remapping are not implemented.','Shape preservation for terminal changes has not been verified.','A GTFS edit and exported-file validator are not implemented.'],'note':'Block counts alone do not establish whether trips overlap in time; a date-aware block check is required.'}
