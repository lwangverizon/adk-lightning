import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:adkl_console/api.dart';
import 'package:adkl_console/project_panel.dart';

class FakeApi extends AdklApi {
  Map<String, dynamic>? created;
  bool reject = false;
  final actions = <String>[];
  @override
  Future<Map<String, dynamic>> project() async => {
    'path': '/workspace/projects/notes', 'prepared': false, 'canPrepare': false, 'job': {'state': 'idle'}};
  @override
  Future<Map<String, dynamic>> projectAction(String action, [Map<String, dynamic> data = const {}]) async {
    if (reject) throw Exception('A project with that name already exists.');
    actions.add(action);
    if (action == 'create') created = data;
    return {'saved': true};
  }
}

void main() {
  testWidgets('native audit uses the isolated runtime inspection', (tester) async {
    final api = FakeApi();
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ProjectPanel(
      api: api, nativeHost: true, onRuntimeChanged: () {}))));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(OutlinedButton, 'Run audit'));
    await tester.pumpAndSettle();
    expect(api.actions, ['inspect']);
  });
  testWidgets('creation sends purpose and model and keeps errors editable', (tester) async {
    final api = FakeApi()..reject = true;
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: CreateAgentDialog(api: api))));
    await tester.enterText(find.widgetWithText(TextField, 'Agent name'), 'notes');
    await tester.enterText(find.widgetWithText(TextField, 'What should this agent help people do?'), 'Organize supplied notes.');
    await tester.tap(find.widgetWithText(FilledButton, 'Create agent'));
    await tester.pumpAndSettle();
    expect(find.textContaining('already exists'), findsOneWidget);
    expect(find.text('notes'), findsOneWidget);
  });

  testWidgets('project screen separates unprepared runtime from release and audit', (tester) async {
    final api = FakeApi();
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ProjectPanel(api: api, onRuntimeChanged: () {}))));
    await tester.pumpAndSettle();
    expect(tester.widget<FilledButton>(find.widgetWithText(FilledButton, 'Prepare runtime')).onPressed, isNull);
    expect(find.textContaining('trusted SDK wheel'), findsOneWidget);
    await tester.tap(find.widgetWithText(OutlinedButton, 'Inspect runtime'));
    await tester.pumpAndSettle();
    expect(api.actions, ['inspect']);
  });
}
