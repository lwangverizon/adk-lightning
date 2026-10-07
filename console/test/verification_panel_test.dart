import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:adkl_console/verification_panel.dart';

void main() {
  Future<void> show(WidgetTester tester, Map<String, dynamic> status,
      {bool busy = false, void Function(String)? onRun}) async {
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: SingleChildScrollView(
      child: VerificationPanel(status: status, busy: busy,
        onRun: onRun ?? (_) {}, onRefresh: () {})))));
    await tester.pumpAndSettle();
  }

  testWidgets('unconfigured evaluator cannot start a run', (tester) async {
    await show(tester, {'state': 'unconfigured'});
    expect(find.text('Verification not configured'), findsOneWidget);
    expect(tester.widget<FilledButton>(find.widgetWithText(FilledButton, 'Verify saved changes')).onPressed, isNull);
    expect(tester.widget<OutlinedButton>(find.widgetWithText(OutlinedButton, 'Record baseline')).onPressed, isNull);
  });

  testWidgets('candidate needs baseline and runs only the configured action', (tester) async {
    final actions = <String>[];
    await show(tester, {'state': 'not_run', 'configured': true, 'profile': 'local'}, onRun: actions.add);
    expect(tester.widget<FilledButton>(find.widgetWithText(FilledButton, 'Verify saved changes')).onPressed, isNull);
    await tester.tap(find.text('Record baseline'));
    expect(actions, ['baseline']);
    await show(tester, {'state': 'baseline_recorded', 'configured': true, 'profile': 'local',
      'baseline': 'abc'}, onRun: actions.add);
    await tester.tap(find.text('Verify saved changes'));
    expect(actions, ['baseline', 'candidate']);
  });

  testWidgets('stale comparison never labels saved edits as passed', (tester) async {
    await show(tester, {'state': 'stale', 'configured': true, 'baseline': 'abc', 'latest': 'def',
      'changes': [{'path': 'skills/display/SKILL.md', 'change': 'modified'}],
      'comparison': {'passed': true, 'baseline': {}, 'candidate': {}, 'failures': [], 'regressed_cases': []}});
    expect(find.text('Saved changes need verification'), findsOneWidget);
    expect(find.text('Saved changes passed'), findsNothing);
    expect(find.textContaining('Previous result'), findsOneWidget);
    await tester.tap(find.text('1 changed input file'));
    await tester.pumpAndSettle();
    expect(find.text('skills/display/SKILL.md'), findsOneWidget);
  });

  testWidgets('failed and running states cannot reuse previous success', (tester) async {
    await show(tester, {'state': 'failed', 'configured': true, 'baseline': 'abc', 'message': 'Worker timed out'});
    expect(find.text('Verification did not complete'), findsOneWidget);
    expect(find.text('Worker timed out'), findsOneWidget);
    expect(find.text('Comparison measurements'), findsNothing);
    await show(tester, {'state': 'running', 'configured': true, 'baseline': 'abc'});
    expect(tester.widget<FilledButton>(find.widgetWithText(FilledButton, 'Verify saved changes')).onPressed, isNull);
  });

  testWidgets('baseline has no candidate column and metrics use readable precision', (tester) async {
    await show(tester, {'state': 'baseline_recorded', 'mode': 'baseline', 'configured': true, 'baseline': 'abc',
      'comparison': {'baseline': {'p95_latency_ms': 123.456, 'total_cost_usd': 0.012345},
        'candidate': {}, 'failures': [], 'regressed_cases': []}});
    expect(find.text('Candidate'), findsNothing);
    expect(find.text('123.5'), findsOneWidget);
    expect(find.text('0.0123'), findsOneWidget);
  });
}
