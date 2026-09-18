import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings


class DeploymentStatusTests(SimpleTestCase):
    def test_returns_valid_public_deployment_marker_without_caching(self):
        payload = {
            'project': 'trud-1',
            'commit': 'a' * 40,
            'deployed_at': '2026-09-18T10:00:00Z',
            'ignored_private_value': 'not returned',
        }
        with tempfile.TemporaryDirectory() as directory:
            status_file = Path(directory) / 'deployment-status.json'
            status_file.write_text(json.dumps(payload), encoding='utf-8')
            with override_settings(DEPLOYMENT_STATUS_FILE=status_file):
                response = self.client.get('/admin/deployment-status/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'project': 'trud-1',
            'commit': 'a' * 40,
            'deployed_at': '2026-09-18T10:00:00Z',
        })
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_returns_503_when_marker_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'missing.json'
            with override_settings(DEPLOYMENT_STATUS_FILE=missing):
                response = self.client.get('/admin/deployment-status/')

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'unavailable'})

    def test_returns_503_for_invalid_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            status_file = Path(directory) / 'deployment-status.json'
            status_file.write_text(json.dumps({
                'project': 'trud-1',
                'commit': 'not-a-commit',
                'deployed_at': 'yesterday',
            }), encoding='utf-8')
            with override_settings(DEPLOYMENT_STATUS_FILE=status_file):
                response = self.client.get('/admin/deployment-status/')

        self.assertEqual(response.status_code, 503)

    def test_rejects_mutating_methods(self):
        response = self.client.post('/admin/deployment-status/')

        self.assertEqual(response.status_code, 405)
