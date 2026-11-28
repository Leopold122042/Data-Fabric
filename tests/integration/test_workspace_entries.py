import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from platform_core.bootstrap import create_app


class WorkspaceEntriesTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.app = create_app(folder.name, start_runtime=False)
        self.client = self.enterContext(TestClient(self.app, client=('127.0.0.1', 45000), follow_redirects=False))

    def test_logged_out_pages_return_to_their_own_workspace(self):
        targets = {
            '/user?page=ask': '/user/login',
            '/user?page=query&flow=FLOW-example&delivery=DEL-example': '/user/login',
            '/user/services': '/user/login',
            '/admin': '/admin/login',
            '/admin/accounts': '/admin/login',
            '/static/management/intake.html?selected=REQ-1': '/admin/login',
            '/static/execution/index.html?tab=run': '/admin/login',
            '/static/platform/resources.html': '/admin/login',
            '/showcase?topic=3': '/showcase/login',
            '/static/report/index.html': '/showcase/login',
        }
        for page, login in targets.items():
            with self.subTest(page=page):
                response = self.client.get(page)
                self.assertEqual(response.status_code, 303)
                location = urlsplit(response.headers['location'])
                self.assertEqual(location.path, login)
                self.assertEqual(parse_qs(location.query)['return'], [page])
                self.assertEqual(self.client.get(login).status_code, 200)
        self.assertEqual(self.client.get('/').headers['location'], '/user')
        self.assertEqual(self.client.get('/api/portal/catalog').status_code, 401)

    def test_existing_account_sessions_and_api_permissions_apply_to_each_entry(self):
        accounts = self.app.state.accounts
        password = 'test-only-password-041'
        accounts.register_account('workspace_test', password, '入口测试')
        login = self.client.post('/api/auth/login', json={'username': 'workspace_test', 'password': password})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.json()['principal']['level'], 1)
        for page, text in (
            ('/user', '正在载入工作空间'),
            ('/user/services', '正在载入服务目录'),
            ('/admin', '最近需求'),
            ('/showcase', '四个课题'),
        ):
            with self.subTest(page=page):
                response = self.client.get(page)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn(text, response.text)
        legacy_services = self.client.get('/static/portal/services.html?check=1')
        self.assertEqual(legacy_services.headers['location'], '/user/services?check=1')
        services = self.client.get('/user/services')
        self.assertEqual(services.headers['cache-control'], 'no-cache')
        self.assertNotIn('管理入口', services.text)
        self.assertNotIn('账户权限', services.text)
        self.assertEqual(self.client.get('/api/portal/services').status_code, 200)
        self.assertEqual(self.client.get('/static/portal/accounts.html').headers['location'], '/admin/accounts')
        self.assertEqual(self.client.get('/api/accounts').status_code, 403)
        self.assertEqual(self.client.get('/api/report/topic3/ai/providers').status_code,
                         self.client.get('/api/portal/ai/providers').status_code)
        self.assertEqual(self.client.post('/api/portal/queries/missing/result', json={}).status_code, 404)
