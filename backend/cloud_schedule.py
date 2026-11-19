"""Schedule reads from the workspace-scoped Postgres GTFS index."""
import datetime

def routes(conn,workspace,feed):
    with conn.cursor() as cur:
        cur.execute("SELECT row_data FROM workbench.gtfs_rows WHERE workspace_id=%s AND feed_id=%s AND file_name='routes.txt' ORDER BY row_data->>'route_short_name',row_data->>'route_id'",(workspace,feed))
        return [row[0] for row in cur.fetchall()]

def trips_on_date(conn,workspace,feed,route_id,service_date,limit=250):
    day=datetime.date.fromisoformat(service_date)
    key=day.strftime('%Y%m%d')
    weekday=('monday','tuesday','wednesday','thursday','friday','saturday','sunday')[day.weekday()]
    limit=max(1,min(int(limit),250))
    # Weekday is from a fixed allowlist; GTFS calendar additions/removals both apply.
    sql="""SELECT t.row_data FROM workbench.gtfs_rows t
    WHERE t.workspace_id=%s AND t.feed_id=%s AND t.file_name='trips.txt' AND t.row_data->>'route_id'=%s
    AND ((EXISTS (SELECT 1 FROM workbench.gtfs_rows c WHERE c.workspace_id=t.workspace_id AND c.feed_id=t.feed_id AND c.file_name='calendar.txt' AND c.row_data->>'service_id'=t.row_data->>'service_id' AND c.row_data->>'start_date'<=%s AND c.row_data->>'end_date'>=%s AND c.row_data->>%s='1')
    AND NOT EXISTS (SELECT 1 FROM workbench.gtfs_rows x WHERE x.workspace_id=t.workspace_id AND x.feed_id=t.feed_id AND x.file_name='calendar_dates.txt' AND x.row_data->>'service_id'=t.row_data->>'service_id' AND x.row_data->>'date'=%s AND x.row_data->>'exception_type'='2'))
    OR EXISTS (SELECT 1 FROM workbench.gtfs_rows x WHERE x.workspace_id=t.workspace_id AND x.feed_id=t.feed_id AND x.file_name='calendar_dates.txt' AND x.row_data->>'service_id'=t.row_data->>'service_id' AND x.row_data->>'date'=%s AND x.row_data->>'exception_type'='1'))
    ORDER BY t.row_data->>'trip_id' LIMIT %s"""
    with conn.cursor() as cur:
        cur.execute(sql,(workspace,feed,route_id,key,key,weekday,key,key,limit+1))
        rows=[x[0] for x in cur.fetchall()]
    return {'trips':rows[:limit],'truncated':len(rows)>limit,'note':'Trip templates; frequency-based service is not expanded.'}

def stop_times(conn,workspace,feed,trip_id):
    with conn.cursor() as cur:
        cur.execute("SELECT row_data FROM workbench.gtfs_rows WHERE workspace_id=%s AND feed_id=%s AND file_name='stop_times.txt' AND row_data->>'trip_id'=%s ORDER BY CASE WHEN row_data->>'stop_sequence' ~ '^[0-9]+$' THEN (row_data->>'stop_sequence')::numeric ELSE NULL END, row_number",(workspace,feed,trip_id))
        return [x[0] for x in cur.fetchall()]
