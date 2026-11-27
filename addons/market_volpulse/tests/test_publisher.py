import json
import unittest

import httpx

from backend.publisher import Publisher, prepare, view_config


class PublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_and_publish(self):
        seen = []

        def handler(request):
            data = json.loads(request.content) if request.content else None
            seen.append((request.method, request.url.path, data))
            return httpx.Response(200, json={'items': []} if request.method == 'GET' else {'ok': True})

        transport = httpx.MockTransport(handler)
        with httpx.Client(base_url='http://127.0.0.1:3018', transport=transport) as http:
            prepare(http)
        async with httpx.AsyncClient(base_url='http://127.0.0.1:3018', transport=transport) as http:
            publisher = Publisher(http)
            await publisher({'schema_version': 1, 'value': None})
        self.assertEqual(seen[1], ('POST', '/api/ext-data', view_config()))
        self.assertEqual(seen[2][1], '/api/ext-data/market_volpulse_view_v1/ingest')
        self.assertEqual(len(seen[2][2]['rows']), 1)
        self.assertNotIn('date', seen[2][2])
        self.assertEqual(json.loads(seen[2][2]['rows'][0]['payload_json']), {'schema_version': 1, 'value': None})

    def test_reject_foreign_schema(self):
        def handler(request):
            self.assertEqual(request.method, 'GET')
            return httpx.Response(200, json={'items': [dict(view_config(), mode='history')]})

        with httpx.Client(base_url='http://127.0.0.1:3018', transport=httpx.MockTransport(handler)) as http:
            with self.assertRaises(ValueError):
                prepare(http)
