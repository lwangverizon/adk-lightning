import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:adkl_console/api.dart';
import 'package:adkl_console/chat_panel.dart';

List<int> frame(String type, [Map<String, dynamic> body = const {}]) =>
    utf8.encode('data: ${jsonEncode({'version': 1, 'type': type, ...body})}\n\n');

class WireClient extends http.BaseClient {
  WireClient(this.bytes, {this.status = 200});
  final Stream<List<int>> bytes;
  final int status;
  bool closed = false;
  Future<void>? aborted;
  Map<String, dynamic>? payload;
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    expect(request.headers['Accept'], 'text/event-stream');
    expect(request, isA<http.AbortableRequest>());
    aborted = (request as http.AbortableRequest).abortTrigger;
    payload = jsonDecode(request.body) as Map<String, dynamic>;
    return http.StreamedResponse(bytes, status);
  }
  @override
  void close() { closed = true; }
}

class ChatApi extends AdklApi {
  ChatApi(this.events);
  final StreamController<Map<String, dynamic>> events;
  bool cancelled = false;
  bool failClear = false;
  @override
  Stream<Map<String, dynamic>> chatStream(String text) => events.stream;
  @override
  void cancelChat() { cancelled = true; }
  @override
  Future<Map<String, dynamic>> execution() async => {'profiles': []};
  @override
  Future<ChatStatus> chatStatus() async => ChatStatus(available: true, problems: []);
  @override
  Future<void> chatClear() async {
    if (failClear) throw const ApiException('Reset failed');
  }
}

void main() {
  testWidgets('workflow trace is visible without skill matches or bound tools', (t) async {
    final events = StreamController<Map<String, dynamic>>.broadcast();
    final api = ChatApi(events);
    await t.pumpWidget(MaterialApp(home: Scaffold(body: ChatPanel(api: api))));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField), 'Find phones');
    await t.testTextInput.receiveAction(TextInputAction.send);
    await t.pump();
    events.add({'type': 'done', 'result': {
      'reply': 'Public answer',
      'trace': {'matches': [], 'boundTools': [], 'toolCalls': [
        {'name': 'device_discovery', 'status': 'called', 'args': {}},
      ], 'execution': {
        'schemaVersion': 1, 'preparedModelRequests': 2, 'droppedEvents': 1,
        'events': [
          {'type': 'model_request', 'agent': 'prospect', 'tools': ['device_discovery']},
          {'type': 'capability', 'kind': 'workflow', 'capability': 'device_discovery',
           'operation': 'device_discovery', 'status': 'denied', 'reason': 'not_eligible',
           'agent': 'prospect', 'call_id': 'c1'},
          {'type': 'retrieval', 'candidates': [
            {'kind': 'skill', 'name': 'records'},
            {'kind': 'workflow', 'name': 'device_discovery'},
          ]},
          {'type': 'capability', 'kind': 'skill', 'capability': 'records',
           'operation': 'load_skill', 'status': 'completed', 'reason': 'ok'},
          {'type': 'node_output', 'path': 'device_discovery/search',
           'status': 'output_observed'},
        ],
      }},
    }});
    await events.close();
    await t.pumpAndSettle();
    expect(find.text('Execution trace'), findsOneWidget);
    expect(find.textContaining('2 prepared requests'), findsOneWidget);
    expect(find.text('Device Discovery'), findsOneWidget);
    expect(find.textContaining('Denied'), findsWidgets);
    expect(find.text('Retrieval shortlist'), findsOneWidget);
    expect(find.text('Load Skill'), findsOneWidget);
    expect(find.textContaining('events omitted'), findsOneWidget);
    expect(find.text('Technical details'), findsOneWidget);
    expect(t.takeException(), isNull);
  });

  test('old trace payloads remain compatible', () {
    expect(ChatTurn.fromJson('hello', {'trace': <String, dynamic>{}}).execution, isNull);
  });

  test('recovery metadata survives HTTP and streaming error responses', () async {
    final failure = {'error': 'Source changed', 'code': 'stale_conversation',
      'recoverable': true, 'action': 'clear_conversation'};
    for (final status in [200, 409]) {
      final client = WireClient(Stream.value(status == 200
        ? frame('error', failure) : utf8.encode(jsonEncode(failure))), status: status);
      final api = AdklApi(streamClientFactory: () => client);
      await expectLater(api.chatStream('hello').toList(), throwsA(isA<ApiException>()
        .having((e) => e.code, 'code', 'stale_conversation')
        .having((e) => e.canClear, 'canClear', true)));
      expect(client.closed, isTrue);
    }
  });

  testWidgets('stale conversation blocks sends and only clears after a successful reset', (t) async {
    final events = StreamController<Map<String, dynamic>>.broadcast();
    final api = ChatApi(events)..failClear = true;
    await t.pumpWidget(MaterialApp(home: Scaffold(body: ChatPanel(api: api))));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField), 'hello');
    await t.testTextInput.receiveAction(TextInputAction.send);
    await t.pump();
    events.addError(const ApiException('Source changed', code: 'stale_conversation',
      recoverable: true, action: 'clear_conversation'));
    await events.close();
    await t.pumpAndSettle();
    expect(find.byType(MaterialBanner), findsOneWidget);
    expect(t.widget<TextField>(find.byType(TextField)).enabled, isFalse);
    await t.tap(find.text('Clear conversation'));
    await t.pumpAndSettle();
    expect(find.byType(MaterialBanner), findsOneWidget);
    expect(find.text('hello'), findsOneWidget);
    expect(find.textContaining('Could not clear conversation'), findsOneWidget);
    api.failClear = false;
    await t.tap(find.text('Clear conversation'));
    await t.pumpAndSettle();
    expect(find.byType(MaterialBanner), findsNothing);
    expect(find.text('hello'), findsNothing);
    expect(t.widget<TextField>(find.byType(TextField)).enabled, isTrue);
  });

  test('stream transports explicit local scenario state', () async {
    final client = WireClient(Stream.value([
      ...frame('text', {'text': 'ok'}),
      ...frame('done', {'result': {'reply': 'ok'}}),
    ]));
    final api = AdklApi(streamClientFactory: () => client)
      ..testState = {'scenario': 'prospect'};
    await api.chatStream('hello').toList();
    expect(client.payload, {'text': 'hello', 'state': {'scenario': 'prospect'}});
  });

  test('missing margin is unknown rather than a zero-score tie', () {
    for (final trace in <Map<String, dynamic>>[{}, {'margin': null}]) {
      expect(ChatTurn.fromJson('fee', {'reply': 'Done', 'trace': trace}).margin,
          isNull);
    }
    expect(ChatTurn.fromJson('fee', {'trace': {'margin': 0}}).margin, 0);
    expect(ChatTurn.fromJson('fee', {'trace': {'margin': 0.25}}).margin, 0.25);
  });

  test('final trace separates model calls and active skills from candidates', () {
    final turn = ChatTurn.fromJson('hello', {
      'reply': 'Done',
      'trace': {'modelCalls': 2, 'activatedSkills': ['math'], 'matches': []},
    });
    expect(turn.modelCalls, 2);
    expect(turn.activatedSkills, ['math']);
    expect(turn.matches, isEmpty);
  });

  test('UTF-8 split frames preserve complete text and final result', () async {
    final bytes = [...frame('progress', {'phase': 'running'}),
      ...frame('text', {'text': 'café [phone]'}),
      ...frame('done', {'result': {'reply': 'café [phone]'}})];
    final client = WireClient(Stream.fromIterable(bytes.map((b) => [b])));
    final api = AdklApi(streamClientFactory: () => client);
    final events = await api.chatStream('hello').toList();
    expect(events[1]['text'], 'café [phone]');
    expect(client.closed, isTrue);
  });

  test('missing done, terminal errors and mismatched final text close the client', () async {
    for (final bytes in [frame('progress'), frame('error', {'error': 'Failed'}),
      [...frame('text', {'text': 'safe'}), ...frame('done', {'result': {'reply': 'different'}})],
      utf8.encode('data: {"version":1')]) {
      final client = WireClient(Stream.value(bytes));
      final api = AdklApi(streamClientFactory: () => client);
      await expectLater(api.chatStream('hello').toList(), throwsA(isA<Exception>()));
      expect(client.closed, isTrue);
    }
  });

  test('explicit cancellation closes the active HTTP client', () async {
    final wire = StreamController<List<int>>();
    final client = WireClient(wire.stream);
    final api = AdklApi(streamClientFactory: () => client);
    final result = api.chatStream('hello').toList();
    final check = expectLater(result, throwsA(isA<FormatException>()));
    await Future<void>.delayed(Duration.zero);
    api.cancelChat();
    await client.aborted;
    expect(client.closed, isTrue);
    await wire.close();
    await check;
  });

  testWidgets('progress is bounded and Stop invalidates late results', (t) async {
    final events = StreamController<Map<String, dynamic>>.broadcast();
    final api = ChatApi(events);
    await t.pumpWidget(MaterialApp(home: Scaffold(body: ChatPanel(api: api))));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField), 'hello');
    await t.testTextInput.receiveAction(TextInputAction.send);
    await t.pump();
    events.add({'type': 'progress', 'phase': 'tool_call', 'name': 'lookup'});
    await t.pump();
    expect(find.text('Calling lookup…'), findsOneWidget);
    await t.tap(find.text('Stop'));
    await t.pumpAndSettle();
    expect(api.cancelled, isTrue);
    events.add({'type': 'text', 'text': 'stale reply'});
    await t.pumpAndSettle();
    expect(find.text('stale reply'), findsNothing);
    expect(find.text('Stop'), findsNothing);
    events.close();
    await t.pumpAndSettle();
  });

  testWidgets('stream failure releases spinner and allows Clear', (t) async {
    final events = StreamController<Map<String, dynamic>>.broadcast();
    final api = ChatApi(events);
    await t.pumpWidget(MaterialApp(home: Scaffold(body: ChatPanel(api: api))));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField), 'hello');
    await t.testTextInput.receiveAction(TextInputAction.send);
    await t.pump();
    events.addError(const FormatException('Missing done'));
    events.close();
    await t.pumpAndSettle();
    await t.pumpAndSettle();
    expect(find.textContaining('Missing done'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(t.widget<TextButton>(find.widgetWithText(TextButton, 'Clear')).onPressed, isNotNull);
  });
}
