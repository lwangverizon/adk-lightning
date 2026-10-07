import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:adkl_console/api.dart';
import 'package:adkl_console/main.dart';
import 'package:adkl_console/rail.dart';

import 'native_editor_test.dart' show EditorApi;

class ParityApi extends EditorApi {
  final calls = <Map<String, dynamic>>[];
  bool fail = false;
  bool failRefresh = false;
  String subject = 'math';
  @override
  Future<List<SkillSummary>> skills() async {
    if (failRefresh) throw Exception('Refresh failed');
    return super.skills();
  }
  @override
  Future<Map<String, dynamic>> projectAction(String action, [Map<String, dynamic> data = const {}]) async {
    calls.add(data);
    if (fail) throw const ApiException('Doctor unavailable');
    return {'report': {
      'profile': 'uat', 'sourceDigest': 'fixture-hash', 'freshness': 'current',
      'passed': false,
      'checks': [{'id': 'native', 'status': data['inspect'] == true ? 'findings' : 'not_checked',
        'detail': 'Declarations only'}],
      'findings': [{
        'id': 'mapping', 'rule': 'AUTHORING_SOURCE_UNMAPPED', 'severity': 'INFO',
        'subject': {'kind': 'skill', 'name': subject}, 'title': 'Backend mapping title',
        'message': 'No editable mapping', 'remediation': 'Backend mapping guidance',
      }],
    }, 'brief': 'Fixture report'};
  }
}

void main() {
  testWidgets('filter does not rescan; failed rerun retains unverified evidence', (t) async {
    t.view.physicalSize = const Size(1600, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final api = ParityApi()..subject = 'other';
    await t.pumpWidget(MaterialApp(home: ConsoleShell(api: api)));
    await t.pumpAndSettle();
    await t.tap(find.text('Run Doctor'));
    await t.pumpAndSettle();
    await t.tap(find.text('Close'));
    await t.pumpAndSettle();
    await t.tap(find.text('Entire current agent'));
    await t.pumpAndSettle();
    await t.tap(find.text('Selected skill: math').last);
    await t.pumpAndSettle();
    expect(find.text('0 of 1 findings'), findsOneWidget);
    expect(find.text('No findings for this selection'), findsOneWidget);
    expect(api.calls.length, 1);
    api.fail = true;
    await t.tap(find.text('Rerun Doctor'));
    await t.pumpAndSettle();
    expect(find.text('Doctor unavailable'), findsOneWidget);
    await t.tap(find.text('Close'));
    await t.pumpAndSettle();
    expect(find.text('Report outdated or unverified — rerun Doctor.'), findsOneWidget);
    expect(find.text('0 of 1 findings'), findsOneWidget);
    expect(t.takeException(), isNull);
  });

  testWidgets('persisted save invalidates report even when refresh fails', (t) async {
    t.view.physicalSize = const Size(1600, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final api = ParityApi();
    await t.pumpWidget(MaterialApp(home: ConsoleShell(api: api)));
    await t.pumpAndSettle();
    await t.tap(find.text('Run Doctor'));
    await t.pumpAndSettle();
    await t.tap(find.text('Close'));
    await t.pumpAndSettle();
    final description = find.byWidgetPredicate((w) =>
      w is TextField && w.controller?.text == 'Teach math');
    await t.enterText(description, 'Changed draft');
    await t.pump(const Duration(milliseconds: 600));
    api.failRefresh = true;
    await t.tap(find.text('Save'));
    await t.pumpAndSettle();
    expect(find.textContaining('Saved, but workspace refresh failed'), findsOneWidget);
    expect(find.text('Report outdated or unverified — rerun Doctor.'), findsOneWidget);
    expect(find.text('1 of 1 findings'), findsOneWidget);
    expect(t.takeException(), isNull);
  });
  testWidgets('Doctor alone supplies findings; drafts stay separate and saves stale the report', (t) async {
    t.view.physicalSize = const Size(1600, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final api = ParityApi();
    await t.pumpWidget(MaterialApp(home: ConsoleShell(api: api)));
    await t.pumpAndSettle();
    expect(t.widget<NavRail>(find.byType(NavRail)).scopeLabel,
      'Doctor not run');
    expect(find.text('Shorten this procedure'), findsNothing);
    expect(find.text('Workspace preview'), findsNothing);
    expect(find.text('Doctor has not run'), findsOneWidget);
    await t.tap(find.text('Run Doctor'));
    await t.pumpAndSettle();
    expect(api.calls.single['inspect'], false);
    expect(find.text('native: not_checked'), findsOneWidget);
    await t.tap(find.text('Inspect native checks'));
    await t.pumpAndSettle();
    expect(api.calls.last['inspect'], true);
    expect(find.text('INFO Backend mapping title'), findsOneWidget);
    await t.tap(find.text('Close'));
    await t.pumpAndSettle();
    final rail = find.byType(NavRail);
    expect(t.widget<NavRail>(rail).scopeLabel,
      'Doctor report · saved files only');
    expect(find.descendant(of: rail, matching: find.text('Backend mapping title')), findsOneWidget);
    await t.tap(find.text('Coverage · current at scan'));
    await t.pumpAndSettle();
    expect(find.text('native: findings'), findsOneWidget);
    expect(find.textContaining('Saved base · profile uat · overlays excluded'), findsOneWidget);
    expect(find.text('Source: fixture-hash'), findsOneWidget);
    await t.tap(find.text('Coverage · current at scan'));
    await t.pumpAndSettle();
    expect(find.text('1 of 1 findings'), findsOneWidget);
    await t.tap(find.text('Entire current agent'));
    await t.pumpAndSettle();
    await t.tap(find.text('Selected skill: math').last);
    await t.pumpAndSettle();
    expect(find.text('1 of 1 findings'), findsOneWidget);
    final description = find.byWidgetPredicate((w) => w is TextField && w.controller?.text == 'Teach math');
    await t.enterText(description, 'Draft not saved');
    await t.pump(const Duration(milliseconds: 600));
    expect(find.text('Saved files only — draft changes not checked by Doctor.'), findsOneWidget);
    expect(api.calls.length, 2); // No Doctor request on keystrokes.
    expect(find.descendant(of: rail, matching: find.text('Backend mapping title')), findsOneWidget);
    await t.tap(find.text('Save'));
    await t.pumpAndSettle();
    expect(find.text('Report outdated or unverified — rerun Doctor.'), findsOneWidget);
    await t.tap(find.text('Rerun Doctor'));
    await t.pumpAndSettle();
    expect(api.calls.last['previous'], isNotNull);
    expect(api.calls.last['inspect'], false); // Reopening never imports a factory implicitly.
    await t.tap(find.text('Close'));
    await t.pumpAndSettle();
    expect(t.takeException(), isNull);
  });
}
