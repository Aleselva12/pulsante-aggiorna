import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from app import Controller, handler, Bridge


class FakeBridge:
    def __init__(self, fail=False, current='old', source='new'):
        self.calls = []
        self.current, self.source, self.fail = current, source, fail

    def request(self, path, data=None):
        self.calls.append((path, data))
        if path == '/status':
            return {'active_commit': self.current, 'jobs': []}
        if path == '/workspaces':
            return {'id': 'workspace', 'source_commit': self.source}
        if path.endswith('/commit'):
            return {'commit': self.source}
        if path == '/jobs':
            return {'id': data['kind']}
        if path == '/jobs/prepare':
            return {'id': 'prepare', 'commit': self.source, 'status': 'failed' if self.fail else 'ready', 'phase': 'testing'}
        if path == '/jobs/apply':
            self.current = self.source
            return {'id': 'apply', 'status': 'completed', 'phase': 'completed'}
        raise AssertionError(path)


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def controller(self, bridge):
        return Controller(bridge, self.root/'status.json', 'main', interval=0)

    def test_complete_pipeline_pins_commit(self):
        bridge = FakeBridge()
        c = self.controller(bridge)
        c.run()
        self.assertEqual(c.row['status'], 'completed')
        apply = [data for path, data in bridge.calls if path == '/jobs' and data['kind'] == 'apply'][0]
        self.assertEqual(apply, {'kind': 'apply', 'release_id': 'prepare', 'commit': 'new'})

    def test_failed_tests_never_apply(self):
        bridge = FakeBridge(fail=True)
        c = self.controller(bridge)
        c.run()
        self.assertEqual(c.row['status'], 'failed')
        self.assertFalse(any(path == '/jobs' and data['kind'] == 'apply' for path, data in bridge.calls))

    def test_no_change_skips_build(self):
        bridge = FakeBridge(current='new')
        self.controller(bridge).run()
        self.assertFalse(any(path == '/jobs' for path, _ in bridge.calls))

    def test_concurrent_updater_blocks(self):
        bridge = FakeBridge()
        original = bridge.request
        bridge.request = lambda path, data=None: {'active_commit': 'old', 'jobs': [{'status': 'running'}]} if path == '/status' else original(path, data)
        c = self.controller(bridge)
        c.run()
        self.assertEqual(c.row['status'], 'failed')
        self.assertFalse(bridge.calls)

    def test_restart_does_not_auto_apply(self):
        (self.root/'status.json').write_text(json.dumps({'status': 'running'}))
        bridge = FakeBridge()
        c = self.controller(bridge)
        self.assertEqual(c.row['status'], 'interrupted')
        self.assertFalse(bridge.calls)

    def test_branch_mismatch_fails_at_startup(self):
        (self.root/'config.json').write_text(json.dumps({'github_repository': 'Aleselva12/serveria1', 'github_branch': 'server-test/current-hardware'}))
        with self.assertRaises(ValueError):
            Bridge(self.root, 'main')

    def test_http_csrf_host_and_single_click(self):
        c = self.controller(FakeBridge())
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(c, {'http://button.local'}, 'secret'))
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        url = 'http://127.0.0.1:' + str(server.server_port)
        try:
            for headers in [{}, {'Host': 'button.local', 'Origin': 'http://evil.local', 'X-CSRF-Token': 'secret'},
                            {'Host': 'button.local', 'Origin': 'http://button.local'}]:
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(url+'/update', data=b'{}', headers=headers))
                self.assertEqual(error.exception.code, 403)
            request = Request(url+'/', headers={'Host': 'button.local'})
            with urlopen(request) as response:
                self.assertIn(b'Aggiorna da GitHub', response.read())
            # Keep a job active to verify duplicate clicks without spawning deployment work.
            c.change(status='running')
            request = Request(url+'/update', data=b'{}', headers={'Host': 'button.local',
                'Origin': 'http://button.local', 'X-CSRF-Token': 'secret', 'Content-Type': 'application/json'})
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 409)
        finally:
            server.shutdown()
            worker.join()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
