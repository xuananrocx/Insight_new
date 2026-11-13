"""Readiness diagnostics must not follow public URLs, redirect or send credentials."""
import asyncio
import os
import unittest
from unittest.mock import patch

import httpx

from src import __version__
from src.mcp import deployment


class DeploymentStatus(unittest.TestCase):
    def status(self, handler):
        client = httpx.AsyncClient
        with patch.dict(os.environ, {'INSIGHT_MCP_HEALTH_URL': 'http://gateway:8001/health'}), \
             patch.object(deployment.httpx, 'AsyncClient', side_effect=lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)):
            return asyncio.run(deployment.status())

    def test_unconfigured_does_not_request_a_public_url(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(deployment.httpx, 'AsyncClient') as client:
            self.assertEqual(asyncio.run(deployment.status())['state'], 'not_configured')
            client.assert_not_called()

    def test_ready_and_version_mismatch_are_distinct(self):
        for version, expected in ((__version__, 'ready'), ('0.0.0', 'version_mismatch')):
            def handler(request):
                self.assertEqual(str(request.url), 'http://gateway:8001/health')
                self.assertNotIn('authorization', request.headers)
                self.assertNotIn('cookie', request.headers)
                return httpx.Response(200, json={'component': 'insight-mcp', 'status': 'ok', 'version': version})
            value = self.status(handler)
            self.assertEqual(value['state'], expected)
            self.assertEqual(value['gateway_version'], version)
            self.assertNotIn('gateway:8001', str(value))

    def test_bad_responses_redirects_and_timeouts_are_not_ready(self):
        for response in (httpx.Response(200, text='<html>wrong route</html>'), httpx.Response(200, json=[]),
                         httpx.Response(503, json={'status': 'ok', 'component': 'insight-mcp', 'version': __version__}),
                         httpx.Response(302, headers={'Location': 'http://other/private'})):
            calls = []
            def handler(request):
                calls.append(str(request.url))
                return response
            self.assertEqual(self.status(handler)['state'], 'unavailable')
            self.assertEqual(calls, ['http://gateway:8001/health'])
        def timeout(request):
            raise httpx.ConnectTimeout('not exposed')
        self.assertEqual(self.status(timeout)['state'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
