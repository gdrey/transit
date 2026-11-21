"""Materialize and run a single request against temporary Neon session state."""
import base64
import gzip
import hashlib
import json
import logging
import re
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import SpooledTemporaryFile, TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

from backend import temp_sessions

WORKER = Path(__file__).with_name('hosted_worker.py')
# The worker needs neither the database connection nor platform credentials.
WORKER_ENV = ('PATH','LANG','LC_ALL','SSL_CERT_FILE','SSL_CERT_DIR','LD_LIBRARY_PATH','PYTHONPATH','TZ')

def files_for_read(path):
    url = urlsplit(path)
    if url.path in {'/api/summary','/api/hosted-trips'}:
        return ['feed.sqlite']
    if url.path == '/api/routes' and parse_qs(url.query).get('feed',[''])[0] != 'draft':
        return ['feed.sqlite']
    if url.path == '/api/agency-presets':
        return ['agency-presets.json']
    if url.path == '/api/auto-model-status':
        return []
    if url.path == '/api/model-job':
        return ['hosted-model-job.json']
    return None


OUTPUT_REQUIRED = {
    '/api/pipeline-output','/api/pipeline-verification','/api/latest-platform-version',
}
RESTORE_CHUNK = 512 * 1024


def restore_files(conn, session, directory, method, path):
    requested = files_for_read(path) if method == 'GET' else None
    if requested is None:
        rows = conn.execute("""
            SELECT name,size FROM transient_gtfs.files
            WHERE session_id=%s AND name NOT LIKE '@%%'
        """, (session,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT name,size FROM transient_gtfs.files
            WHERE session_id=%s AND name=ANY(%s)
        """, (session,requested)).fetchall()
    names = {name for name,_ in rows}
    route = urlsplit(path).path
    if method == 'GET' and (
        (route in OUTPUT_REQUIRED and 'pipeline/current.json' not in names)
        or (route == '/api/alert-assessments' and 'alerts.sqlite' not in names)
    ):
        return {}

    originals = {}
    for name,size in rows:
        file = directory / name
        file.parent.mkdir(parents=True,exist_ok=True)
        # Never return a full-size bytea result through the Neon pooler.
        # gzip output already compresses these snapshots before storage.
        with SpooledTemporaryFile(max_size=RESTORE_CHUNK) as buffer:
            for offset in range(0,size,RESTORE_CHUNK):
                row = conn.execute("""
                    SELECT substring(content FROM %s FOR %s)
                    FROM transient_gtfs.files WHERE session_id=%s AND name=%s
                """, (offset+1,RESTORE_CHUNK,session,name),binary=True).fetchone()
                if not row or len(row[0]) != min(RESTORE_CHUNK,size-offset):
                    raise ValueError('The temporary feed changed while it was loading. Please retry.')
                buffer.write(row[0])
            buffer.seek(0)
            with gzip.GzipFile(fileobj=buffer,mode='rb') as compressed, file.open('wb') as out:
                shutil.copyfileobj(compressed,out)
        stat = file.stat()
        originals[name] = (stat.st_size,stat.st_mtime_ns)
    return originals


def run(conn, session, method, path, body=b'', ai=None, upload_parts=None, part=None):
    with TemporaryDirectory(prefix='gtfs-') as root:
        directory = Path(root, '.data')
        directory.mkdir()
        originals = restore_files(conn,session,directory,method,path)
        event = {'method':method,'path':path,'body':base64.b64encode(body).decode('ascii'),'ai':ai}
        if upload_parts is not None:
            upload = Path(root,'upload.zip')
            with upload.open('wb') as handle:
                for _, buffer in upload_parts:
                    handle.write(buffer)
            event['upload'] = str(upload)
        response = Path(root,'response')
        env = {key:os.environ[key] for key in WORKER_ENV if key in os.environ}
        # Vercel adds runtime libraries to the parent's sys.path. Rebase those
        # entries before changing the worker's current directory.
        env['PYTHONPATH'] = os.pathsep.join(os.path.abspath(path) for path in sys.path)
        env.update(GTFS_DATA_DIR='.data',GTFS_RESPONSE=str(response),PYTHONDONTWRITEBYTECODE='1',TMPDIR=root)
        worker = subprocess.run([sys.executable,str(WORKER)],input=json.dumps(event),text=True,cwd=root,env=env,
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=260)
        if worker.returncode:
            failure = re.search(r'^([A-Za-z]+Error)',
                                worker.stderr.splitlines()[-1] if worker.stderr else '')
            logging.getLogger(__name__).warning('GTFS worker exited %s (%s, bundle included: %s)',
                                                worker.returncode,failure.group(0) if failure else 'unknown',
                                                WORKER.is_file())
            raise RuntimeError('The GTFS engine could not process this request.')
        result = json.loads(worker.stdout)
        if method == 'POST' and result['status'] < 400:
            current = set()
            for file in directory.rglob('*'):
                if not file.is_file():
                    continue
                name = file.relative_to(directory).as_posix()
                if name.startswith('draft-view/') or name.endswith(('-shm','-wal','-journal','.tmp')):
                    continue
                current.add(name)
                stat = file.stat()
                if originals.get(name) != (stat.st_size,stat.st_mtime_ns):
                    temp_sessions.put(conn,session,name,gzip.compress(file.read_bytes(),compresslevel=3))
            for name in set(originals)-current:
                temp_sessions.discard(conn,session,name)
            if upload_parts is None:
                temp_sessions.check_size(conn,session)
        size = response.stat().st_size
        if part is not None and result['type']=='application/zip':
            with response.open('rb') as stream:
                result['sha256'] = hashlib.file_digest(stream,'sha256').hexdigest()
            with response.open('rb') as stream:
                stream.seek(part * temp_sessions.PART_BYTES)
                payload=stream.read(temp_sessions.PART_BYTES)
            return result, payload, size
        if size > 4 * 1024 * 1024:
            raise ValueError('This response exceeds the hosted size limit. Narrow the selection.')
        return result, response.read_bytes(), size
