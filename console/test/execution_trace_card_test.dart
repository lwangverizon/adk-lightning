import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:adkl_console/execution_trace_card.dart';

Map<String, dynamic> event(String status, {String call = 'c1'}) => {
  'type': 'capability', 'kind': 'workflow', 'capability': 'discover_devices',
  'agent': 'prospect', 'call_id': call, 'operation': 'discover_devices',
  'status': status, 'reason': status == 'failed' ? 'search_failed' : 'returned',
};

Future<void> mount(WidgetTester t, List<Map<String, dynamic>> events) async {
  await t.binding.setSurfaceSize(const Size(340, 1400));
  addTearDown(() => t.binding.setSurfaceSize(null));
  await t.pumpWidget(MaterialApp(home: Scaffold(body: SingleChildScrollView(
    child: ExecutionTraceCard(toolCalls: const [], execution: {
      'schemaVersion': 1, 'preparedModelRequests': 2, 'events': events,
    }),
  ))));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('workflow lifecycle becomes one card with ordered steps', (t) async {
    await mount(t, [event('requested'), event('started'),
      {'type': 'node_output', 'path': 'discover_devices/retrieve'},
      {'type': 'node_output', 'path': 'discover_devices/select'},
      {'type': 'node_output', 'path': 'discover_devices/commit'},
      event('completed'),
    ]);
    expect(find.text('Discover Devices'), findsOneWidget);
    expect(find.text('Retrieve'), findsOneWidget);
    expect(find.text('Select'), findsOneWidget);
    expect(find.text('Commit'), findsOneWidget);
    expect(find.text('Output observed'), findsNWidgets(3));
    expect(t.getTopLeft(find.text('Retrieve')).dy,
        lessThan(t.getTopLeft(find.text('Select')).dy));
    expect(find.byType(SelectableText), findsNothing);
    await t.tap(find.text('Technical details'));
    await t.pumpAndSettle();
    expect(find.byType(SelectableText), findsOneWidget);
    expect(t.takeException(), isNull);
  });

  testWidgets('failure remains visible after a successful retry', (t) async {
    await mount(t, [event('started'), event('failed'),
      event('started', call: 'c2'), event('completed', call: 'c2')]);
    expect(find.text('Failed'), findsOneWidget);
    expect(find.text('Failed · search_failed'), findsOneWidget);
    expect(find.text('Run 1 outcome'), findsOneWidget);
    expect(find.text('Run 2 outcome'), findsOneWidget);
    expect(t.takeException(), isNull);
  });

  testWidgets('ambiguous parallel node output is not assigned to a run', (t) async {
    await mount(t, [event('started'), event('started', call: 'c2'),
      {'type': 'node_output', 'path': 'discover_devices/retrieve'}]);
    expect(find.text('Retrieve'), findsNothing);
    expect(find.text('Unconfirmed'), findsOneWidget);
    await t.tap(find.text('Technical details'));
    await t.pumpAndSettle();
    expect(find.textContaining('discover_devices/retrieve'), findsOneWidget);
    expect(t.takeException(), isNull);
  });

  testWidgets('unknown schema is explicit and legacy tool data remains available', (t) async {
    await t.pumpWidget(MaterialApp(home: Scaffold(body: ExecutionTraceCard(
      execution: const {'schemaVersion': 2},
      toolCalls: const [{'name': 'load_skill', 'status': 'called'}],
    ))));
    expect(find.textContaining('version unsupported'), findsOneWidget);
    await t.tap(find.text('Technical details'));
    await t.pumpAndSettle();
    expect(find.textContaining('load_skill'), findsOneWidget);
  });
}
