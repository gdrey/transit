#!/usr/bin/env python3
"""Generate diagram data for active scheduled trips in the local feed."""
import argparse, csv, datetime as dt, io, json, os, sqlite3, zipfile
import workbench

def build(route_id, date, destination=None, db_path=None):
    day=dt.date.fromisoformat(date); key=day.strftime('%Y%m%d')
    weekday=('monday','tuesday','wednesday','thursday','friday','saturday','sunday')[day.weekday()]
    with workbench.connect(db_path) as db:
        source=db.execute('select source from metadata').fetchone()[0]
        frequency_ids=set()
        with zipfile.ZipFile(source) as archive:
            entries=[n for n in archive.namelist() if n.lower()=='frequencies.txt']
            if entries:
                with archive.open(entries[0]) as raw:
                    frequency_ids={r['trip_id'] for r in csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline=''))}
        route=db.execute('select * from routes where route_id=?',(route_id,)).fetchone()
        if not route: raise ValueError('Route not found')
        active='''((exists(select 1 from calendar c where c.service_id=t.service_id and c.start_date<=? and c.end_date>=? and c."'''+weekday+'''"='1') and not exists(select 1 from calendar_dates e where e.service_id=t.service_id and e.date=? and e.exception_type='2')) or exists(select 1 from calendar_dates e where e.service_id=t.service_id and e.date=? and e.exception_type='1'))'''
        trips=db.execute('select t.* from trips t where t.route_id=? and '+active+' order by t.trip_id',(route_id,key,key,key,key)).fetchall()
        # shapes.txt and coordinates are optional; keep the schedule usable when absent.
        active_ids={trip['trip_id'] for trip in trips}
        shape_by_trip={};stop_coordinates={};shape_points={}
        with zipfile.ZipFile(source) as archive:
            names={n.lower():n for n in archive.namelist() if '/' not in n and '\\' not in n}
            with archive.open(names['trips.txt']) as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline='')):
                    if row.get('trip_id') in active_ids:shape_by_trip[row['trip_id']]=row.get('shape_id') or ''
            with archive.open(names['stops.txt']) as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline='')):
                    try:
                        lat=float(row.get('stop_lat') or 'nan');lon=float(row.get('stop_lon') or 'nan')
                        if -90<=lat<=90 and -180<=lon<=180:stop_coordinates[row['stop_id']]={'lat':lat,'lon':lon}
                    except (TypeError,ValueError):pass
            wanted={x for x in shape_by_trip.values() if x}
            if 'shapes.txt' in names and wanted:
                with archive.open(names['shapes.txt']) as raw:
                    for row in csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline='')):
                        shape=row.get('shape_id')
                        if shape not in wanted:continue
                        try:
                            lat=float(row['shape_pt_lat']);lon=float(row['shape_pt_lon']);seq=int(row['shape_pt_sequence'])
                            if -90<=lat<=90 and -180<=lon<=180:shape_points.setdefault(shape,[]).append((seq,lat,lon))
                        except (KeyError,TypeError,ValueError):continue
        shapes={shape:[{'lat':lat,'lon':lon} for _,lat,lon in sorted(points)] for shape,points in shape_points.items() if len(points)>=2}
        patterns={}; excluded=0
        for trip in trips:
            if trip['trip_id'] in frequency_ids:
                excluded+=1
                continue
            rows=db.execute('select st.stop_id,st.stop_sequence,st.arrival_time,st.departure_time,s.stop_name from stop_times st left join stops s on s.stop_id=st.stop_id where st.trip_id=? order by cast(st.stop_sequence as integer)',(trip['trip_id'],)).fetchall()
            if not rows: continue
            seq=tuple((r['stop_id'],r['stop_name'] or r['stop_id']) for r in rows)
            key2=(trip['direction_id'],seq)
            if key2 not in patterns: patterns[key2]={'direction_id':trip['direction_id'],'stops':[{'id':s[0],'name':s[1],**(stop_coordinates.get(s[0]) or {})} for s in seq],'trips':[]}
            patterns[key2]['trips'].append({'id':trip['trip_id'],'headsign':trip['trip_headsign'],'shape_id':shape_by_trip.get(trip['trip_id'],''),'times':[(r['departure_time'] or r['arrival_time']).strip() for r in rows], 'arrival_times':[(r['arrival_time'] or '').strip() for r in rows], 'departure_times':[(r['departure_time'] or '').strip() for r in rows]})
        data={'route':dict(route),'service_date':date,'patterns':sorted(patterns.values(),key=lambda p:len(p['trips']),reverse=True),'frequency_trips_excluded':excluded,'shapes':shapes,'note':'Stop spacing is ordinal, not geographic distance. Times over 24:00 belong to the selected service day. Frequency-based trips are excluded. Patterns reflect stop order only; pickup/drop-off rules and real-time changes are not represented.'}
    if destination:
        with open(destination,'w',encoding='utf8') as f: json.dump(data,f,ensure_ascii=False)
    return data

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('route_id');p.add_argument('date');p.add_argument('--output',default=os.path.join(workbench.ROOT,'diagram-data.json'));a=p.parse_args()
    result=build(a.route_id,a.date,a.output)
    print('Generated %d patterns and %d trip records.'%(len(result['patterns']),sum(len(x['trips']) for x in result['patterns'])))
