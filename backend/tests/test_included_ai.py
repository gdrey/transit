import os
import unittest
from unittest.mock import patch

from backend import included_ai

class IncludedAITest(unittest.TestCase):
    def test_requires_explicit_enablement(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(included_ai.provider())

    def test_only_fixed_managed_gateway(self):
        with patch.dict(os.environ, {
            'GTFS_INCLUDED_AI_ENABLED':'1',
            'GTFS_INCLUDED_AI_MODEL':'test-model',
            'NEON_AI_GATEWAY_TOKEN':'placeholder-secret',
            'NEON_AI_GATEWAY_BASE_URL':'https://test-api.ai.us-east-1.aws.neon.tech',
        }, clear=True):
            provider=included_ai.provider()
            self.assertNotIn('placeholder-secret',repr(provider))
            self.assertEqual(provider.worker_config(1)['url'],
                             'https://test-api.ai.us-east-1.aws.neon.tech/v1/chat/completions')
            with patch.dict(os.environ,{'NEON_AI_GATEWAY_BASE_URL':'https://example.com'}):
                self.assertIsNone(included_ai.provider())

    def test_caller_cannot_increase_batch_or_pass_provider_settings(self):
        self.assertEqual(included_ai.request_budget('assess-next',b'{"limit":1,"key":"ignored"}'),
                         (1,b'{"limit": 1}'))
        with self.assertRaises(ValueError):
            included_ai.request_budget('assess-next',b'{"limit":500}')
