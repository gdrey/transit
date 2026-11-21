"""Run the existing GTFS engine in an isolated, disposable request directory."""
import base64
import io
import json
import os
import re
from urllib.parse import parse_qs, urlsplit
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'prototype'))
import auto_model
import llm_client
import preview_server
import workbench
from hosted_network import opener

class WorkbenchRequest(preview_server.Handler):
    def ai_service(self):
        return self.service

    def send(self, status, body, content_type):
        self.result = {'status':status, 'type':content_type}
        Path(os.environ['GTFS_RESPONSE']).write_bytes(body)

def main():
    event = json.load(sys.stdin)
    original_opener = preview_server.urllib.request.build_opener
    preview_server.urllib.request.build_opener = lambda *handlers: opener(original_opener, *handlers)
    # The durable pipeline runs when the operator clicks Run. There is no
    # always-on process for local automatic draft rebuilds on Vercel.
    auto_model.request = lambda: None
    assessment_threads = []
    original_start = preview_server.assessment_queue.start
    if event['method'] == 'POST':
        def track_assessment():
            if not preview_server.assessment_queue.LOCK.acquire(blocking=False):
                return
            thread = preview_server.threading.Thread(target=preview_server.assessment_queue.worker, daemon=True)
            assessment_threads.append(thread)
            thread.start()
        preview_server.assessment_queue.start = track_assessment

    handler = WorkbenchRequest.__new__(WorkbenchRequest)
    handler.path = event['path']
    handler.headers = {'Origin':'http://127.0.0.1:8766'}
    handler.service = event.get('ai')
    calls_used = 0
    if handler.service:
        complete = llm_client.complete
        def capped_completion(prompt, schema, service):
            nonlocal calls_used
            if calls_used >= handler.service['allowance']:
                raise ValueError('The included AI limit for this request was reached.')
            calls_used += 1
            return complete(prompt,schema,handler.service,output_limit=2048,prompt_limit=131072)
        llm_client.complete = capped_completion
    else:
        def unavailable_completion(*args, **kwargs):
            raise ValueError('Included AI is unavailable for this request.')
        llm_client.complete = unavailable_completion
    if event['method'] == 'POST':
        body = Path(event['upload']).read_bytes() if event.get('upload') else base64.b64decode(event['body'])
        handler.headers['Content-Length'] = str(len(body))
        handler.rfile = io.BytesIO(body)
        if handler.path == '/api/hosted-route-edit':
            value = json.loads(body)
            try:
                route_id, field, setting = (value[key] for key in ('route_id','field','value'))
                _, digest, routes = preview_server.manual_edits.source()
                if field not in preview_server.manual_edits.FIELDS or not any(row['route_id']==route_id for row in routes):
                    raise ValueError('Choose an existing route and a supported route field.')
                if field=='route_color':
                    setting = setting.removeprefix('#').upper()
                    if not re.fullmatch('[0-9A-F]{6}',setting):
                        raise ValueError('Use a six-digit RGB color.')
                elif not 0 < len(setting) <= 255:
                    raise ValueError('Use a short name from 1 to 255 characters.')
                edit = {'route_id':route_id,'field':field,'value':setting}
                result = preview_server.manual_edits.stage(f'Change {route_id} {field} to {setting}',digest,{'edits':[edit]})
                handler.send(200,json.dumps(result).encode(),'application/json')
            except ValueError as error:
                handler.send(400,json.dumps({'error':str(error)}).encode(),'application/json')
        elif handler.path == '/api/model-job':
            # The local job tracker lives in memory. Run its worker to
            # completion inside this invocation and persist its activity log.
            original_thread = preview_server.threading.Thread
            class InlineThread:
                def __init__(self, target, **kwargs):
                    self.target = target
                def start(self):
                    self.target()
            try:
                preview_server.threading.Thread = InlineThread
                handler.do_POST()
            finally:
                preview_server.threading.Thread = original_thread
            if preview_server.MODEL_JOBS:
                Path(workbench.DATA_DIR, 'hosted-model-job.json').write_text(json.dumps(next(iter(preview_server.MODEL_JOBS.values()))))
        else:
            handler.do_POST()
        for thread in assessment_threads:
            thread.join()
        preview_server.assessment_queue.start = original_start
    else:
        if handler.path.startswith('/api/model-job?'):
            job_file = Path(workbench.DATA_DIR, 'hosted-model-job.json')
            if job_file.exists():
                job = json.loads(job_file.read_text())
                preview_server.MODEL_JOBS[job['id']] = job
        if handler.path.startswith('/api/hosted-trips?'):
            query = parse_qs(urlsplit(handler.path).query)
            try:
                result = workbench.trips_on_date(query['route_id'][0],query['date'][0],query['limit'][0])
                handler.send(200,json.dumps(result).encode(),'application/json')
            except (ValueError, KeyError) as error:
                handler.send(400,json.dumps({'error':str(error)}).encode(),'application/json')
        else:
            handler.do_GET()
    handler.result['ai_calls_used'] = calls_used
    print(json.dumps(handler.result))

if __name__ == '__main__':
    main()
