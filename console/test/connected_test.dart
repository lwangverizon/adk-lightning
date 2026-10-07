import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:adkl_console/connected_main.dart';

Map<String, dynamic> observed(String id) => {
  'schemaVersion': 1, 'mode': 'connected', 'gatewayReceivedAt': DateTime.now().toUtc().toIso8601String(),
  'observationValidForSeconds': 15, 'snapshotDigest': 'fixture-digest',
  'snapshot': {
    'schemaVersion': 1, 'kind': 'adkl_loaded_declarations', 'agentId': id, 'environment': 'uat',
    'release': '$id-release', 'instanceId': 'fixture-process', 'generation': 'fixture-generation',
    'capturedAt': '2026-01-01T00:00:00Z', 'scope': 'registration declarations',
    'capabilities': {'chat': false, 'edit': false, 'sessions': false},
    'agents': [{'id': 'agents/0', 'name': id, 'tools': [], 'bindings': [],
      'toolsets': [{'id': 'set/0', 'skills': [{'id': 'skill/0', 'name': 'Procedure',
        'owner': 'set/0', 'instructions': {'kind': 'static', 'text': 'Approved instruction'}}], 'tools': []}]}],
  },
};

class FixtureApi extends ConnectedApi {
  final pending = Completer<Map<String, dynamic>>();
  bool fail = false;
  final paths = <String>[];
  @override
  Future<Map<String, dynamic>> get(String path) async {
    paths.add(path);
    if (fail) throw const FormatException('denied');
    if (path == '/api/v1/agents') {
      return {'agents': [
      {'id': 'prospect', 'name': 'Prospect', 'environment': 'uat'},
      {'id': 'classic-search', 'name': 'Classic Search', 'environment': 'uat'},
    ]};
    }
    if (path.contains('/prospect/')) return pending.future;
    return observed('classic-search');
  }
}

void main() {
  testWidgets('late results isolated; content and coverage visible; denial clears data', (t) async {
    final api = FixtureApi();
    await t.pumpWidget(MaterialApp(home: ConnectedPortal(api: api)));
    await t.pump();
    await t.tap(find.byType(DropdownButtonFormField<String>));
    await t.pump(const Duration(milliseconds: 300));
    await t.tap(find.text('Classic Search').last);
    await t.pumpAndSettle();
    api.pending.complete(observed('prospect'));
    await t.pumpAndSettle();
    expect(find.textContaining('classic-search-release'), findsOneWidget);
    expect(find.textContaining('prospect-release'), findsNothing);
    expect(find.textContaining('Doctor: not run'), findsOneWidget);
    await t.tap(find.text('Skills'));
    await t.pumpAndSettle();
    await t.tap(find.text('Procedure'));
    await t.pumpAndSettle();
    expect(find.textContaining('Approved instruction'), findsOneWidget);
    await t.pump(const Duration(seconds: 16));
    expect(find.textContaining('Observation expired'), findsOneWidget);
    final calls = api.paths.length;
    await t.pump(const Duration(seconds: 30));
    expect(api.paths.length, calls); // No background polling.
    api.fail = true;
    await t.tap(find.text('Refresh observation'));
    await t.pumpAndSettle();
    expect(find.textContaining('Previous content cleared'), findsOneWidget);
    expect(find.textContaining('Approved instruction'), findsNothing);
    expect(t.takeException(), isNull);
  });

  testWidgets('mobile content has no mutations or overflow', (t) async {
    t.view.physicalSize = const Size(390, 844);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final api = FixtureApi()..pending.complete(observed('prospect'));
    await t.pumpWidget(MaterialApp(home: ConnectedPortal(api: api)));
    await t.pumpAndSettle();
    await t.tap(find.text('Tools'));
    await t.pumpAndSettle();
    expect(find.textContaining('No matching declarations'), findsOneWidget);
    expect(find.text('Save'), findsNothing);
    expect(t.takeException(), isNull);
    await t.pumpWidget(const SizedBox());
  });

  testWidgets('retained evidence is identity-bound, labeled and expires', (t) async {
    t.view.physicalSize = const Size(1440, 1400);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final data = observed('prospect');
    final snapshot = data['snapshot'] as Map<String, dynamic>;
    data['retainedEvidence'] = {
      'state': 'matched', 'provenance': 'publisher_asserted', 'executedHere': false,
      'binding': {for (final key in ['agentId', 'environment', 'release', 'instanceId', 'generation'])
        key: snapshot[key], 'snapshotDigest': data['snapshotDigest']},
      'expiresAt': DateTime.now().toUtc().add(const Duration(seconds: 2)).toIso8601String(),
      'verificationState': 'passed', 'runId': 'retained-run',
      'contracts': [{'name': 'no_replay', 'state': 'passed', 'caseCount': 2, 'observations': 4}],
    };
    final api = FixtureApi()..pending.complete(data);
    await t.pumpWidget(MaterialApp(home: ConnectedPortal(api: api)));
    await t.pumpAndSettle();
    expect(find.text('Retained verification: passed (publisher asserted)'), findsOneWidget);
    expect(find.text('no_replay: passed'), findsOneWidget);
    expect(find.textContaining('No evaluator executed here'), findsOneWidget);
    await t.pump(const Duration(seconds: 3));
    expect(find.textContaining('Retained verification: historical'), findsOneWidget);
    expect(find.text('no_replay: passed'), findsNothing);
    await t.pumpWidget(const SizedBox());
  });

  testWidgets('mismatched retained evidence clears content', (t) async {
    final data = observed('prospect');
    data['retainedEvidence'] = {'state': 'matched', 'provenance': 'publisher_asserted',
      'executedHere': false, 'binding': {'snapshotDigest': 'different'}, 'contracts': []};
    final api = FixtureApi()..pending.complete(data);
    await t.pumpWidget(MaterialApp(home: ConnectedPortal(api: api)));
    await t.pumpAndSettle();
    expect(find.textContaining('Previous content cleared'), findsOneWidget);
    expect(find.textContaining('prospect-release'), findsNothing);
    await t.pumpWidget(const SizedBox());
  });

  test('client accepts only fixed same-origin read endpoints', () async {
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.url.toString(), 'https://host/adkl/api/v1/agents');
      expect(request.headers.containsKey('authorization'), false);
      return http.Response(jsonEncode({'schemaVersion': 1, 'mode': 'connected'}), 200);
    });
    final api = ConnectedApi(client: client, baseUri: Uri.parse('https://host/adkl/'));
    await api.get('/api/v1/agents');
    for (final path in ['https://upstream/agents', '/api/chat', '/api/v1/agents/../inspection']) {
      await expectLater(api.get(path), throwsFormatException);
    }
    api.close();
  });
}
