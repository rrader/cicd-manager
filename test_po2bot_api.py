import unittest
from unittest.mock import patch, MagicMock
import os
import json

# Set test environment
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ['PO2BOT_API_TOKEN'] = 'test-token-xyz'
os.environ['SERVICES_DIR'] = '/tmp'
os.environ['METRICS_DB_PATH'] = '/tmp/test_metrics_history.db'
os.environ['TEMPLATES_FILE'] = '/tmp/test_banner_templates.json'
os.environ['CURRENT_BANNER_FILE'] = '/tmp/test_current_banner.json'
os.environ['AIR_ALERT_STATE_FILE'] = '/tmp/test_air_alert_state.json'
os.environ['USERS_FILE'] = '/tmp/test_users.json'

from app import app


class TestPo2BotEndpoints(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()

    def test_unauthenticated_requests_require_login(self):
        """Verify endpoints are protected by @login_required."""
        res_dialogs = self.client.get('/api/po2bot/dialogs')
        self.assertEqual(res_dialogs.status_code, 302)  # Redirects to /login

        res_user = self.client.get('/api/po2bot/dialogs/123')
        self.assertEqual(res_user.status_code, 302)

        res_media = self.client.get('/api/po2bot/media/123/doc.jpg')
        self.assertEqual(res_media.status_code, 302)

    @patch('app.http_request')
    def test_authenticated_dialogs_proxies_with_token(self, mock_http):
        """Verify authenticated /api/po2bot/dialogs sends Bearer token to po2bot."""
        mock_http.return_value = (200, {'success': True, 'users': [{'user_id': 123}]})

        with self.client.session_transaction() as sess:
            sess['logged_in'] = True
            sess['user'] = 'admin'

        res = self.client.get('/api/po2bot/dialogs?search=taras')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['users'][0]['user_id'], 123)

        # Check call arguments
        mock_http.assert_called_once()
        url = mock_http.call_args[0][0]
        headers = mock_http.call_args[1].get('headers', {})
        self.assertIn('search=taras', url)
        self.assertEqual(headers.get('Authorization'), 'Bearer test-token-xyz')

    @patch('app.http_request')
    def test_authenticated_user_messages_proxies_with_token(self, mock_http):
        """Verify authenticated /api/po2bot/dialogs/<user_id> sends Bearer token to po2bot."""
        mock_http.return_value = (200, {'success': True, 'messages': [{'id': 1, 'text': 'Hi'}]})

        with self.client.session_transaction() as sess:
            sess['logged_in'] = True
            sess['user'] = 'admin'

        res = self.client.get('/api/po2bot/dialogs/777')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['messages'][0]['text'], 'Hi')

        mock_http.assert_called_once()
        url = mock_http.call_args[0][0]
        headers = mock_http.call_args[1].get('headers', {})
        self.assertIn('/api/dialogs/777', url)
        self.assertEqual(headers.get('Authorization'), 'Bearer test-token-xyz')


if __name__ == '__main__':
    unittest.main()
