import 'dart:async';
import 'package:adkl_console/api.dart';
import 'package:adkl_console/main.dart';
import 'package:adkl_console/rail.dart';
import 'package:adkl_console/chat_panel.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'native_editor_test.dart' show EditorApi;

class DelayedApi extends EditorApi {
  final pending = <String, Completer<SkillDetail>>{};
  Completer<SaveResult>? saveResult;
  int saves = 0;
  bool failSkills = false;
  @override
  Future<List<SkillSummary>> skills() async {
    if (failSkills) throw Exception('Refresh unavailable');
    return super.skills();
  }
  @override
  Future<SkillDetail> skill(String name) => pending[name]?.future ?? super.skill(name);
  @override
  Future<SaveResult> save(String name, {required String description,
    required List<String> tools, SkillBody? doc, String? body,
    String? revision, bool force = false}) async {
    saves++;
    this.description = description;
    return saveResult == null ? SaveResult(saved: true, refused: false) : await saveResult!.future;
  }
}

SkillDetail detail(String name) => SkillDetail.fromJson({
  'name': name, 'description': 'Description $name', 'body': 'Body $name',
  'tools': <String>[], 'editable': true, 'revision': name,
});

Future<void> boot(WidgetTester t, DelayedApi api, {double width = 1600}) async {
  t.view.physicalSize = Size(width, 1000);
  t.view.devicePixelRatio = 1;
  addTearDown(t.view.resetPhysicalSize);
  addTearDown(t.view.resetDevicePixelRatio);
  await t.pumpWidget(MaterialApp(home: ConsoleShell(api: api)));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('skill selection responds before request and failure restores editor', (t) async {
    final api = DelayedApi();
    await boot(t, api);
    api.pending['slow'] = Completer();
    t.widget<NavRail>(find.byType(NavRail)).onSkill('slow');
    await t.pump();
    expect(t.widget<NavRail>(find.byType(NavRail)).selectedSkill, 'slow');
    expect(find.text('Loading slow…'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Teach math'), findsNothing);
    api.pending['slow']!.completeError(Exception('Offline'));
    await t.pumpAndSettle();
    expect(find.text('Loading slow…'), findsNothing);
    expect(find.widgetWithText(TextField, 'Teach math'), findsOneWidget);
    expect(t.widget<NavRail>(find.byType(NavRail)).selectedSkill, 'math');
    expect(find.textContaining('Could not open slow'), findsOneWidget);
  });

  testWidgets('reselecting loaded skill cancels a pending different selection', (t) async {
    final api = DelayedApi();
    await boot(t, api);
    api.pending['slow'] = Completer();
    final rail = t.widget<NavRail>(find.byType(NavRail));
    rail.onSkill('slow');
    await t.pump();
    rail.onSkill('math');
    await t.pumpAndSettle();
    api.pending['slow']!.complete(detail('slow'));
    await t.pumpAndSettle();
    expect(find.widgetWithText(TextField, 'Teach math'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Description slow'), findsNothing);
  });

  testWidgets('successful save resets chat even when refresh fails', (t) async {
    final api = DelayedApi();
    await boot(t, api);
    final oldChat = t.state(find.byType(ChatPanel, skipOffstage: false));
    await t.enterText(find.widgetWithText(TextField, 'Teach math'), 'Saved value');
    await t.pump();
    api.failSkills = true;
    await t.tap(find.text('Save'));
    await t.pumpAndSettle();
    expect(find.textContaining('Saved, but workspace refresh failed'), findsOneWidget);
    expect(t.state(find.byType(ChatPanel, skipOffstage: false)), isNot(same(oldChat)));
    expect(api.description, 'Saved value');
  });
  testWidgets('late skill result cannot replace a newer skill or tool selection', (t) async {
    final api = DelayedApi();
    await boot(t, api);
    api.pending['old'] = Completer();
    api.pending['new'] = Completer();
    final rail = t.widget<NavRail>(find.byType(NavRail));
    rail.onSkill('old');
    rail.onSkill('new');
    api.pending['new']!.complete(detail('new'));
    await t.pumpAndSettle();
    api.pending['old']!.complete(detail('old'));
    await t.pumpAndSettle();
    expect(find.widgetWithText(TextField, 'Description new'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Description old'), findsNothing);
    api.pending['third'] = Completer();
    rail.onSkill('third');
    rail.onTool('lookup');
    api.pending['third']!.complete(detail('third'));
    await t.pumpAndSettle();
    expect(find.widgetWithText(TextField, 'Description third'), findsNothing);
  });

  testWidgets('reselecting current skill preserves unsaved edits', (t) async {
    await boot(t, DelayedApi());
    await t.enterText(find.widgetWithText(TextField, 'Teach math'), 'Keep draft');
    await t.pump();
    t.widget<NavRail>(find.byType(NavRail)).onSkill('math');
    await t.pumpAndSettle();
    expect(find.widgetWithText(TextField, 'Keep draft'), findsOneWidget);
    expect(find.text('unsaved'), findsOneWidget);
  });

  testWidgets('typing during save remains dirty and duplicate saves are blocked', (t) async {
    final api = DelayedApi();
    await boot(t, api);
    await t.enterText(find.widgetWithText(TextField, 'Teach math'), 'Submitted');
    await t.pump();
    api.saveResult = Completer();
    await t.tap(find.text('Save'));
    await t.pump();
    await t.enterText(find.widgetWithText(TextField, 'Submitted'), 'Newer draft');
    await t.pump();
    api.saveResult!.complete(SaveResult(saved: true, refused: false));
    await t.pumpAndSettle();
    expect(api.saves, 1);
    expect(api.description, 'Submitted');
    expect(find.widgetWithText(TextField, 'Newer draft'), findsOneWidget);
    expect(find.text('unsaved'), findsOneWidget);
    api.saveResult = null;
    await t.tap(find.text('Save'));
    await t.pumpAndSettle();
    expect(api.description, 'Newer draft');
    expect(find.text('unsaved'), findsNothing);
  });

  testWidgets('hidden compact editor cannot retain or receive keyboard focus', (t) async {
    await boot(t, DelayedApi(), width: 390);
    final field = find.descendant(of: find.widgetWithText(TextField, 'Teach math'), matching: find.byType(EditableText));
    final node = t.widget<EditableText>(field).focusNode;
    node.requestFocus();
    await t.pump();
    expect(node.hasFocus, isTrue);
    await t.tap(find.descendant(of: find.byType(NavigationBar), matching: find.text('Test')));
    await t.pumpAndSettle();
    node.requestFocus();
    await t.pump();
    expect(node.hasFocus, isFalse);
    await t.tap(find.descendant(of: find.byType(NavigationBar), matching: find.text('Editor')));
    await t.pumpAndSettle();
    node.requestFocus();
    await t.pump();
    expect(node.hasFocus, isTrue);
  });
}
