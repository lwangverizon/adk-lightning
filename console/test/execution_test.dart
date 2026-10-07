import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:adkl_console/api.dart';
import 'package:adkl_console/chat_panel.dart';

class ProfileApi extends AdklApi {
  String selected = 'local';
  bool ready = true;
  int clears = 0;

  @override
  Future<Map<String, dynamic>> execution() async => {
    'selected': selected,
    'profiles': [
      {'name': 'local', 'environment': 'development', 'target': 'local'},
      {'name': 'staging', 'environment': 'staging', 'target': 'remote'},
    ],
  };

  @override
  Future<void> selectExecution(String profile) async { selected = profile; }

  @override
  Future<ChatStatus> chatStatus() async => ChatStatus(
    available: selected == 'local' && ready,
    problems: ['Execution unavailable'],
  );

  @override
  Future<void> chatClear() async { clears++; ready = true; }
}

class LoadingApi extends ProfileApi {
  final status = Completer<ChatStatus>();
  @override
  Future<ChatStatus> chatStatus() => status.future;
}

void main() {
  testWidgets('loading is distinct from failure and preflight offers typed recovery', (t) async {
    final api = LoadingApi();
    await t.pumpWidget(MaterialApp(home: Scaffold(body: ChatPanel(api: api))));
    await t.pump();
    expect(find.text('Loading…'), findsOneWidget);
    expect(find.text('unavailable'), findsNothing);
    api.status.complete(ChatStatus.fromJson({'available': false, 'problems': ['Source changed'],
      'error': 'Source changed', 'recoverable': true, 'action': 'clear_conversation'}));
    await t.pumpAndSettle();
    expect(find.text('Loading…'), findsNothing);
    expect(find.text('Clear conversation'), findsOneWidget);
  });

  testWidgets('test context validates JSON and resets before applying state', (t) async {
    final api = ProfileApi();
    await t.pumpWidget(MaterialApp(home: Scaffold(body: ChatPanel(api: api))));
    await t.pumpAndSettle();
    await t.tap(find.byTooltip('Set test context'));
    await t.pumpAndSettle();
    final input = find.widgetWithText(TextField, 'Test state JSON');
    await t.enterText(input, '[]');
    await t.tap(find.text('Apply'));
    await t.pumpAndSettle();
    expect(find.text('Enter a valid JSON object.'), findsOneWidget);
    expect(api.clears, 0);
    await t.enterText(input, '{"scenario": "prospect"}');
    await t.tap(find.text('Apply'));
    await t.pumpAndSettle();
    expect(api.clears, 1);
    expect(api.testState, {'scenario': 'prospect'});
    expect(t.takeException(), isNull);
  });

  testWidgets('switching execution target blocks chat and Clear recovers preflight', (t) async {
    final api = ProfileApi();
    await t.pumpWidget(MaterialApp(home: Scaffold(body: ChatPanel(api: api))));
    await t.pumpAndSettle();
    await t.tap(find.byType(DropdownButton<String>));
    await t.pumpAndSettle();
    await t.tap(find.text('staging · staging · remote').last);
    await t.pumpAndSettle();
    expect(api.selected, 'staging');
    expect(find.text('Chat is not available'), findsOneWidget);
    expect(t.widget<TextField>(find.byType(TextField)).enabled, isFalse);

    api.ready = false;
    await t.tap(find.byType(DropdownButton<String>));
    await t.pumpAndSettle();
    await t.tap(find.text('local · development · local').last);
    await t.pumpAndSettle();
    expect(find.text('Chat is not available'), findsOneWidget);
    await t.tap(find.text('Clear'));
    await t.pumpAndSettle();
    expect(api.clears, 1);
    expect(find.text('Chat is not available'), findsNothing);
    expect(t.widget<TextField>(find.byType(TextField)).enabled, isTrue);
  });
}
