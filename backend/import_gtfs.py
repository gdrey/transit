#!/usr/bin/env python3
"""Import a public GTFS ZIP into a private Postgres database.

DATABASE_URL is read from the environment, or from stdin with --stdin-dsn.
Each feed is immutable and identified by its ZIP SHA-256. The original ZIP is
preserved so optional files and unindexed fields are not lost.
"""
import argparse, csv, hashlib, io, os, sys, zipfile
import psycopg
from psycopg import sql

TABLES = {
    'agency': ['agency_id', 'agency_name', 'agency_timezone'],
    'routes': ['route_id','route_short_name','route_long_name','agency_id','route_type','route_color','route_text_color'],
    'stops': ['stop_id','stop_name','parent_station'],
    'trips': ['trip_id','route_id','service_id','trip_headsign','direction_id'],
    'stop_times': ['trip_id','stop_id','stop_sequence','arrival_time','departure_time'],
    'calendar': ['service_id','monday','tuesday','wednesday','thursday','friday','saturday','sunday','start_date','end_date'],
    'calendar_dates': ['service_id','date','exception_type'],
}

def main():
    p = argparse.ArgumentParser()
    p.add_argument('zipfile'); p.add_argument('--source-url', default=''); p.add_argument('--stdin-dsn', action='store_true')
    args = p.parse_args()
    dsn = sys.stdin.readline().strip() if args.stdin_dsn else os.environ.get('DATABASE_URL','')
    if not dsn: raise SystemExit('DATABASE_URL is required')
    with open(args.zipfile, 'rb') as f: blob = f.read()
    digest = hashlib.sha256(blob).hexdigest()
    counts = {}
    with psycopg.connect(dsn, connect_timeout=15) as conn:
      with conn.cursor() as cur:
        cur.execute('CREATE SCHEMA IF NOT EXISTS transit')
        cur.execute('CREATE TABLE IF NOT EXISTS transit.feed_version (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, sha256 text UNIQUE NOT NULL, source_url text NOT NULL, imported_at timestamptz NOT NULL DEFAULT now(), original_zip bytea NOT NULL)')
        for name, columns in TABLES.items():
            fields = sql.SQL(', ').join(sql.SQL('{} text').format(sql.Identifier(c)) for c in columns)
            cur.execute(sql.SQL('CREATE TABLE IF NOT EXISTS transit.{} (feed_id bigint NOT NULL REFERENCES transit.feed_version(id), {})').format(sql.Identifier(name), fields))
        cur.execute('SELECT id FROM transit.feed_version WHERE sha256=%s',(digest,))
        existing=cur.fetchone()
        if existing:
            print('Feed already imported:', digest); return
        cur.execute('INSERT INTO transit.feed_version (sha256,source_url,original_zip) VALUES (%s,%s,%s) RETURNING id',(digest,args.source_url,blob))
        feed_id=cur.fetchone()[0]
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
          names={n.lower():n for n in archive.namelist()}
          for name,columns in TABLES.items():
            filename=names.get(name+'.txt')
            if not filename: continue
            with archive.open(filename) as raw:
              reader=csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline=''))
              if reader.fieldnames is None: raise ValueError('Missing header: '+filename)
              command=sql.SQL('COPY transit.{} ({}) FROM STDIN').format(sql.Identifier(name),sql.SQL(',').join(map(sql.Identifier,['feed_id']+columns)))
              count=0
              with cur.copy(command) as copy:
                for row in reader:
                  copy.write_row([feed_id]+[row.get(c) or None for c in columns]); count+=1
              counts[name]=count
              print(name,count,flush=True)
        for name, cols in [('routes',['route_id']),('stops',['stop_id']),('trips',['route_id','direction_id']),('trips',['trip_id']),('trips',['service_id']),('stop_times',['trip_id','stop_sequence']),('calendar_dates',['service_id','date'])]:
            index='idx_'+name+'_'+'_'.join(cols)
            cur.execute(sql.SQL('CREATE INDEX IF NOT EXISTS {} ON transit.{} ({})').format(sql.Identifier(index),sql.Identifier(name),sql.SQL(',').join(map(sql.Identifier,['feed_id']+cols))))
    print('Imported feed',feed_id,'SHA-256',digest,flush=True)

if __name__=='__main__': main()
