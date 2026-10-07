"""Hermetic text-boundary and worker protocol regressions."""
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from adkl import execution, features, output_filters
from test_execution import make_project


class TestBufferedRedaction(unittest.TestCase):
    def test_every_split_matches_complete_redaction(self):
        for raw in ("call 555-123-4567 " + "x" * 60,
                    "mail " + "a" * 180 + "@example.com today",
                    "123-45-6789 123456789 today", "hello café world"):
            expected = output_filters.redact(raw)[0]
            for split in range(len(raw) + 1):
                redactor = output_filters.StreamRedactor()
                self.assertEqual(redactor.feed(raw[:split]), "")
                self.assertEqual(redactor.feed(raw[split:]), "")
                self.assertEqual(redactor.flush(), expected)
                self.assertEqual(redactor.flush(), "")

    def test_disabled_redaction_and_overflow(self):
        r = output_filters.StreamRedactor(False)
        r.feed("555-123-4567")
        self.assertEqual(r.flush(), "555-123-4567")
        r = output_filters.StreamRedactor(max_chars=3)
        with self.assertRaises(ValueError):
            r.feed("abcd")
        self.assertEqual(r.flush(), "")

    def test_context_validation_and_partial_capabilities(self):
        from adkl import capabilities
        for name, config in (
            ("short_circuit", {"phrases": {"hi": "bad"}}),
            ("short_circuit", {"phrases": {"hi": {"reply": ""}}}),
            ("prompt_state_injection", {"slots": {"x": {"from_file": "../secret"}}}),
            ("prompt_state_injection", {"slots": {"x": {"from_state": "x", "from_file": "x"}}}),
            ("progressive_auth", {"state_key": "verified"}),
        ):
            with self.assertRaises(features.FeatureError):
                features.resolve({name: config})
        report = capabilities.manifest(features.resolve({"pipeline": ["progressive_auth"]}))
        self.assertTrue(report["canRun"])
        self.assertFalse(report["complete"])
        self.assertTrue(report["warnings"])


class TestWorkerStream(unittest.TestCase):
    CODE = '''import json, os, sys, time
for line in sys.stdin:
    req = json.loads(line)
    mode = req.get('text')
    if mode == 'oversize':
        print('x' * (8 * 1024 * 1024 + 1), flush=True)
        continue
    if mode == 'invalid':
        print(json.dumps({'version': 2, 'type': 'done'}), flush=True)
        continue
    print(json.dumps({'version': 1, 'type': 'progress', 'phase': 'running'}), flush=True)
    if mode == 'hang':
        time.sleep(30)
    if mode == 'eof':
        sys.exit(0)
    if mode == 'error':
        print(json.dumps({'version': 1, 'type': 'error', 'error': os.environ['GOOGLE_API_KEY']}), flush=True)
        continue
    reply = os.environ['GOOGLE_API_KEY'] + ':safe'
    print(json.dumps({'version': 1, 'type': 'text', 'text': reply}), flush=True)
    print(json.dumps({'version': 1, 'type': 'done', 'result': {'reply': reply}}), flush=True)
'''

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = make_project(pathlib.Path(self.tmp.name))
        (self.root / '.env').write_text('GOOGLE_API_KEY=synthetic-secret\n')
        platform = self.root / 'platform/adkl'
        platform.mkdir(parents=True)
        (platform / 'worker.py').write_text(self.CODE)
        self.patch = patch.object(execution, 'PLATFORM', platform.parent)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.worker = execution.Worker(self.root, timeout=self.TIMEOUT)
        self.addCleanup(self.worker.close)

    # Spawning CPython and importing the child is not instantaneous, and 0.2s --
    # the deadline every test here used to share with the timeout test -- is well
    # inside the range a loaded machine takes to produce the first frame. Tests
    # about redaction, reuse and cancellation then failed on the clock instead of
    # their subject, in ~13% of runs (measured 4/30 at ec15872). Only the timeout
    # test wants a deadline it can outrun, and it says so locally.
    TIMEOUT = 10
    STALL_TIMEOUT = 0.2

    @staticmethod
    def _content(stream):
        """Frames with keepalives dropped.

        A read emits `ping` for every select interval (up to 1s) that passes with
        no output, so a keepalive legitimately precedes the worker's first real
        frame whenever the child is slow to start, and the assertions below index
        from a frame's position. This is protocol defence, not the measured fix
        above: at a 1s interval no run reproduced it (0/90 under six-way load),
        where the 0.2s deadline reproduced readily.
        """
        return (frame for frame in stream if frame['type'] != 'ping')

    def test_all_frames_redacted_and_worker_reused(self):
        for _ in range(2):
            frames = list(self._content(self.worker.stream('ok')))
            self.assertNotIn('synthetic-secret', json.dumps(frames))
            self.assertEqual(frames[-2]['text'], frames[-1]['result']['reply'])
            self.assertEqual(frames[-1]['result']['reply'], '[REDACTED]:safe')
            self.assertIn('execution', frames[-1]['result'])
        self.assertIsNone(self.worker.process.poll())

    def test_cancel_discards_worker_and_buffer(self):
        stream = self.worker.stream('hang')
        # Named, so the filter outlives the statement: `stream` itself is what
        # this test closes, and the wrapper must not be collected mid-read.
        content = self._content(stream)
        self.assertEqual(next(content)['type'], 'progress')
        process = self.worker.process
        stream.close()
        self.assertIsNotNone(process.poll())
        self.assertIsNone(self.worker.process)
        self.assertEqual(self.worker.buffer, b'')
        self.assertEqual(list(self._content(self.worker.stream('ok')))[-1]['type'], 'done')

    def test_timeout_eof_invalid_error_and_size_fail_closed(self):
        for mode in ('hang', 'eof', 'invalid', 'error', 'oversize'):
            with self.subTest(mode=mode):
                # 'hang' sleeps far past any deadline, so only that mode needs one
                # the test can outrun -- and it is the mode asserting the deadline
                # exists at all. The rest fail on their own output, not the clock.
                worker = self.worker
                if mode == 'hang':
                    worker = execution.Worker(self.root, timeout=self.STALL_TIMEOUT)
                    self.addCleanup(worker.close)
                with self.assertRaises(execution.ExecutionError) as error:
                    list(worker.stream(mode))
                self.assertNotIn('synthetic-secret', str(error.exception))
                self.assertIsNone(worker.process)

    def test_remote_stream_never_spawns(self):
        self.worker.entry['target'] = 'remote'
        # require_local reads the profile on disk, not the cached UI identity.
        p = self.root / 'execution.yaml'
        p.write_text(p.read_text().replace('target: local', 'target: remote'))
        other = execution.Worker(self.root)
        with patch.object(execution.subprocess, 'Popen') as spawn:
            with self.assertRaises(execution.ExecutionError):
                list(other.stream('ok'))
            spawn.assert_not_called()


class TestHttpFrames(unittest.TestCase):
    def handler(self, stream):
        import io
        from unittest.mock import Mock
        from adkl.server import Handler
        handler = Handler.__new__(Handler)
        handler.wfile = io.BytesIO()
        handler.connection = Mock()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._send = Mock()
        handler._chat = Mock(return_value=SimpleWorker(stream))
        return handler

    def test_error_after_headers_is_terminal_sse_not_second_http_response(self):
        def stream():
            yield {'version': 1, 'type': 'progress', 'phase': 'running'}
            raise execution.ExecutionError('fixture failure')
        h = self.handler(stream())
        h._chat_stream('hello', None)
        h.send_response.assert_called_once_with(200)
        h._send.assert_not_called()
        wire = h.wfile.getvalue().decode()
        self.assertIn('"type": "error"', wire)
        self.assertNotIn('"type": "done"', wire)

    def test_disconnect_closes_iterator(self):
        from unittest.mock import Mock
        closed = []
        def stream():
            try:
                yield {'version': 1, 'type': 'progress'}
                yield {'version': 1, 'type': 'done', 'result': {}}
            finally:
                closed.append(True)
        h = self.handler(stream())
        h.wfile = Mock()
        h.wfile.write.side_effect = BrokenPipeError()
        h._chat_stream('hello', None)
        self.assertEqual(closed, [True])
        h._send.assert_not_called()


class SimpleWorker:
    def __init__(self, events):
        self.events = events

    def stream(self, text, state):
        return self.events
