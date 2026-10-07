import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:adkl_console/api.dart';
import 'package:adkl_console/doctor_panel.dart';

class DoctorApi extends AdklApi {
  final calls = <Map<String, dynamic>>[];
  bool fail = false;
  @override
  Future<Map<String, dynamic>> projectAction(String action, [Map<String, dynamic> data = const {}]) async {
    calls.add(data);
    if (fail) throw const ApiException('Inspection unavailable');
    return {
      'report': {
        'freshness': 'current', 'passed': false,
        'checks': [{'id': 'native', 'status': data['inspect'] == true ? 'passed' : 'not_checked', 'detail': 'Native inventory'}],
        'findings': [{'id': 'tool', 'severity': 'ERROR', 'rule': 'TOOL_UNRESOLVED',
          'location': {'path': 'skills/math/SKILL.md'}, 'message': 'Missing lookup',
          'remediation': 'Check registration'}],
        if (data['previous'] != null) 'comparison': {'new': [], 'remaining': ['tool'], 'resolved': [], 'notVerified': []},
      },
      'brief': '# Fix brief\nCheck registration',
    };
  }
}

void main() {
  testWidgets('doctor defaults to static and exports selected findings with fresh checks', (tester) async {
    final api = DoctorApi();
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: DoctorPanel(api: api))));
    await tester.pumpAndSettle();
    expect(api.calls.single['inspect'], false);
    expect(find.text('native: not_checked'), findsOneWidget);
    expect(find.textContaining('ERROR TOOL_UNRESOLVED'), findsOneWidget);
    await tester.tap(find.byType(CheckboxListTile));
    await tester.tap(find.text('Export fix brief'));
    await tester.pumpAndSettle();
    expect(api.calls.last['selected'], isEmpty);
    expect(api.calls.last['previous'], isNotNull);
    expect(find.text('Coding-agent fix brief'), findsOneWidget);
    expect(find.text('Copy Markdown'), findsOneWidget);
  });

  testWidgets('explicit inspection and failure preserve previous findings', (tester) async {
    final api = DoctorApi();
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: DoctorPanel(api: api))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Inspect native checks'));
    await tester.pumpAndSettle();
    expect(api.calls.last['inspect'], true);
    expect(find.text('native: passed'), findsOneWidget);
    api.fail = true;
    await tester.tap(find.text('Rerun checks'));
    await tester.pumpAndSettle();
    expect(find.text('Inspection unavailable'), findsOneWidget);
    expect(find.textContaining('Evidence: unknown'), findsOneWidget);
    expect(find.textContaining('ERROR TOOL_UNRESOLVED'), findsOneWidget);
    expect(find.text('Export fix brief'), findsOneWidget);
  });
}
