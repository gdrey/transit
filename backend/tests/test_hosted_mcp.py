import asyncio,os,sys,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('DATABASE_URL','postgresql://invalid')
os.environ.setdefault('NEON_AUTH_ISSUER','https://example.org/auth')
os.environ.setdefault('NEON_AUTH_JWKS_URL','https://example.org/jwks')
os.environ.setdefault('MCP_RESOURCE_URL','https://example.org/mcp')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hosted_mcp
from starlette.testclient import TestClient

class HostedMcpTests(unittest.TestCase):
    def test_http_rejects_unauthenticated_requests(self):
        with TestClient(hosted_mcp.app) as client:
            r=client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/list'},headers={'Accept':'application/json, text/event-stream'})
            self.assertEqual(r.status_code,401)
    def test_verifier_checks_issuer_audience_and_signature(self):
        with patch.object(hosted_mcp.KEYS,'get_signing_key_from_jwt') as key,patch.object(hosted_mcp.jwt,'decode',return_value={'sub':'user','exp':2000000000}) as decode:
            key.return_value.key='key'
            token=asyncio.run(hosted_mcp.NeonVerifier().verify_token('signed'))
            self.assertEqual(token.subject,'user')
            self.assertEqual(decode.call_args.kwargs['audience'],'https://example.org/mcp')
            self.assertEqual(decode.call_args.kwargs['issuer'],'https://example.org/auth')
            self.assertIn('exp',decode.call_args.kwargs['options']['require'])
