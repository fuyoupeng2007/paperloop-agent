"""Windows distribution settings never expose or store API keys as plain text."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import secret_store, store
from backend.app import app


@unittest.skipUnless(os.name == 'nt', 'Windows DPAPI is required')
class DistributionSettings(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=os.environ.get('PAPERLOOP_TEST_WORK'))
        self.data = Path(self.folder.name)
        self.patch_data = patch.object(store, 'DATA', self.data)
        self.patch_env = patch.dict(os.environ, {'PAPERLOOP_DISTRIBUTED': '1'})
        self.patch_data.start()
        self.patch_env.start()
        store.init()

    def tearDown(self):
        self.patch_env.stop()
        self.patch_data.stop()
        self.folder.cleanup()

    def test_defaults_secret_encryption_update_and_clear(self):
        current = store.settings()
        self.assertEqual((current['provider'], current['model']), ('api', 'deepseek-flash'))
        self.assertEqual(current['api_key'], '')
        secret = 'fake-DeepSeek-key-仅用于测试'
        store.save_settings({'api_key': secret, 'call_limit': 25})
        with store.connect() as db:
            body = db.execute('SELECT body FROM settings WHERE id=1').fetchone()[0]
        self.assertNotIn(secret, body)
        self.assertNotIn(secret.encode(), (self.data / 'api-key.dpapi').read_bytes())
        self.assertEqual(secret_store.load(self.data), secret)
        store.save_settings({'api_key': None, 'model': 'another-model'})
        self.assertEqual(store.settings()['api_key'], secret)
        self.assertEqual(store.settings()['call_limit'], 25)
        store.save_settings({'api_key': ''})
        self.assertEqual(store.settings()['api_key'], '')
        self.assertFalse((self.data / 'api-key.dpapi').exists())

    def test_legacy_plaintext_migrated(self):
        secret = 'fake-old-plain-key'
        with store.connect() as db:
            db.execute('INSERT INTO settings VALUES(1,?)', (json.dumps({'provider': 'api', 'api_key': secret}),))
        self.assertEqual(store.settings()['api_key'], secret)
        with store.connect() as db:
            body = db.execute('SELECT body FROM settings WHERE id=1').fetchone()[0]
        self.assertNotIn(secret, body)
        self.assertEqual(secret_store.load(self.data), secret)

    def test_connection_check_has_no_write_and_public_key_never_echoes(self):
        with TestClient(app) as client:
            headers = {'X-PaperLoop': '1'}
            candidate = {'provider': 'api', 'base_url': 'https://api.deepseek.com',
                         'model': 'deepseek-flash', 'api_key': 'fake-check-only-key'}
            with patch('backend.provider.check_api_connection', return_value={'ok': True, 'message': '连接成功'}):
                checked = client.post('/api/settings/check', headers=headers, json=candidate)
            self.assertEqual(checked.status_code, 200)
            self.assertFalse(client.get('/api/settings').json()['has_key'])
            saved = client.put('/api/settings', headers=headers, json=candidate)
            self.assertEqual(saved.status_code, 200)
            self.assertNotIn(candidate['api_key'], saved.text)
            self.assertTrue(saved.json()['has_key'])
            invalid = client.post('/api/settings/check', headers=headers,
                json={'provider': 'api', 'base_url': 'http://example.com', 'model': 'x'})
            self.assertEqual(invalid.status_code, 400)
            self.assertNotIn(candidate['api_key'], invalid.text)


if __name__ == '__main__':
    unittest.main()
