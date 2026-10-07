import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:adkl_console/api.dart';

/// Accepts the request and never answers, which is what a wedged worker looks
/// like from the browser: the socket stays open and nothing arrives.
class _Silent extends http.BaseClient {
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) =>
      Completer<http.StreamedResponse>().future;
}

class _Throws extends http.BaseClient {
  _Throws(this.error);
  final Object error;
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async => throw error;
}

void main() {
  // testWidgets, not test: its binding runs timers on fake time, so the real
  // 130s deadline is reachable without making the suite wait for it.
  testWidgets('a server that never answers is reported, not waited on forever', (t) async {
    final api = AdklApi(client: _Silent());
    Object? failure;
    final answered = api.project().then<String>((_) => 'answered',
        onError: (Object error) { failure = error; return 'failed'; });
    // The defect is a request that never settles, so the assertion has to be
    // able to observe one: race it against a marker past the deadline, or a
    // regression here would hang the suite instead of failing it.
    final outcome = Future.any([answered,
        Future<String>.delayed(const Duration(seconds: 200), () => 'still waiting')]);
    await t.pump(const Duration(seconds: 201));
    expect(await outcome, 'failed');
    expect(failure, isA<ApiException>());
    // The endpoint and the elapsed bound, because "failed" alone leaves the
    // reader unable to tell a slow host import from a dead server.
    expect('$failure', contains('GET /api/project'));
    expect('$failure', contains('did not answer within 130s'));
  });

  test('an unreachable server reads differently from a slow one', () async {
    final api = AdklApi(base: 'http://127.0.0.1:8787',
        client: _Throws(http.ClientException('Connection refused')));
    await expectLater(api.chatStatus(), throwsA(isA<ApiException>()
        .having((e) => e.message, 'message', contains('could not reach the adkl server'))
        .having((e) => e.message, 'message', contains('http://127.0.0.1:8787'))));
  });

  test('Stop is not reported as an unreachable server', () async {
    // `RequestAbortedException` is a `ClientException` subtype, so the transport
    // mapping above would otherwise tell the user their server had gone away
    // immediately after they pressed Stop.
    final api = AdklApi(
        streamClientFactory: () => _Throws(http.RequestAbortedException()));
    await expectLater(api.chatStream('hello').toList(),
        throwsA(isA<http.RequestAbortedException>()));
  });
}
