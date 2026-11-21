"""Reserve model calls before handing an app credential to the scratch worker."""
from dataclasses import dataclass, field
import json
import os
from urllib.parse import urlsplit

from backend import temp_sessions

BATCH_SIZE = 5
SESSION_CAP = 10
PILOT_CAP = 50
PILOT_SCOPE = 'pilot'
AI_ROUTES = {'assess-next', 'clarify-alert', 'manual-edits', 'analyze-alert'}

class QuotaExceeded(ValueError):
    pass

@dataclass(frozen=True)
class Provider:
    base_url: str
    model: str
    token: str = field(repr=False)

    def worker_config(self, allowance):
        return {'url':self.base_url + '/v1/chat/completions', 'model':self.model,
                'key':self.token, 'allowance':allowance}

def provider(oidc_token=None):
    if os.getenv('GTFS_INCLUDED_AI_ENABLED') != '1':
        return None
    if os.getenv('GTFS_INCLUDED_AI_PROVIDER') == 'vercel':
        if os.getenv('VERCEL_ENV') != 'production':
            return None
        token = oidc_token or os.getenv('VERCEL_OIDC_TOKEN')
        model = os.getenv('GTFS_INCLUDED_AI_MODEL')
        if not token or not model:
            return None
        return Provider('https://ai-gateway.vercel.sh', model, token)
    base_url = os.getenv('NEON_AI_GATEWAY_BASE_URL', '').rstrip('/')
    token = os.getenv('NEON_AI_GATEWAY_TOKEN')
    model = os.getenv('GTFS_INCLUDED_AI_MODEL')
    url = urlsplit(base_url)
    if (not token or not model or url.scheme != 'https' or not url.hostname
            or not url.hostname.endswith('.aws.neon.tech')
            or url.username or url.password or url.port is not None
            or url.path or url.query or url.fragment):
        return None
    return Provider(base_url, model, token)

def scope_for(session):
    return f'session:{session}'

def reserve(session, expires_at, calls):
    # Separate transaction. Model calls remain counted if the GTFS worker
    # fails, times out, or rolls back the user's edits.
    if type(calls) is not int or not 1 <= calls <= min(SESSION_CAP,PILOT_CAP):
        raise ValueError('Invalid included AI reservation.')
    scopes = [(PILOT_SCOPE, PILOT_CAP, None), (scope_for(session), SESSION_CAP, expires_at)]
    with temp_sessions.connect() as conn:
        for scope, limit, expiry in scopes:
            result = conn.execute("""
                INSERT INTO transient_gtfs.ai_allowances (scope,used,expires_at)
                VALUES (%s,%s,%s)
                ON CONFLICT (scope) DO UPDATE
                   SET used=transient_gtfs.ai_allowances.used+excluded.used
                 WHERE transient_gtfs.ai_allowances.used+excluded.used <= %s
                RETURNING used
            """, (scope, calls, expiry, limit)).fetchone()
            if result is None:
                if scope == PILOT_SCOPE:
                    raise QuotaExceeded('The included AI pilot has reached its total limit.')
                raise QuotaExceeded('This session does not have enough included AI calls for that request.')
    return scopes

def refund(scopes, unused):
    if not unused:
        return
    with temp_sessions.connect() as conn:
        for scope, _, _ in scopes:
            conn.execute("""
                UPDATE transient_gtfs.ai_allowances SET used=used-%s
                 WHERE scope=%s AND used >= %s
            """, (unused,scope,unused))

def used_calls(conn, session):
    row = conn.execute('SELECT used FROM transient_gtfs.ai_allowances WHERE scope=%s',
                       (scope_for(session),)).fetchone()
    return row[0] if row else 0

def request_budget(path, body):
    if path == 'assess-next':
        try:
            limit = json.loads(body).get('limit', BATCH_SIZE)
        except (ValueError, AttributeError):
            raise ValueError('Choose a batch size of 1 or 5 alerts.') from None
        if type(limit) is not int or limit not in (1, BATCH_SIZE):
            raise ValueError('Choose a batch size of 1 or 5 alerts.')
        return limit, json.dumps({'limit':limit}).encode()
    return 1, body
