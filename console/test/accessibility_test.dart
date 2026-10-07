import 'dart:async';
import 'dart:ui' show Tristate;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:adkl_console/api.dart';
import 'package:adkl_console/chat_panel.dart';
import 'package:adkl_console/rail.dart';
import 'package:adkl_console/section_editor.dart';
import 'package:adkl_console/theme.dart';
import 'chat_stream_test.dart' show ChatApi;

void main() {
  testWidgets('filled workflow fields and repeated actions have contextual names', (t) async {
    final semantics = t.ensureSemantics();
    final doc = SkillBody.fromJson({'sections': [
      {'title': 'Workflows', 'kind': 'workflows', 'workflows': [
        {'name': 'Explain', 'when': 'User asks how', 'steps': [
          {'text': 'Identify the request'}, {'text': 'Explain the process'}
        ]}
      ]}
    ]});
    await t.pumpWidget(MaterialApp(theme: AdklTheme.light(), home: Scaffold(
      body: SingleChildScrollView(child: SectionEditor(doc: doc,
        onChanged: () {}, toolNames: const {})))));
    await t.pumpAndSettle();
    expect(find.bySemanticsLabel(RegExp('Workflow name')), findsOneWidget);
    expect(find.bySemanticsLabel(RegExp('When to use Explain')), findsOneWidget);
    expect(find.bySemanticsLabel(RegExp('^Explain, step 1')), findsOneWidget);
    expect(find.byTooltip('Delete Explain, step 1'), findsOneWidget);
    expect(find.byTooltip('Delete Explain, step 2'), findsOneWidget);
    final first = find.byWidgetPredicate((w) => w is TextField &&
      w.controller?.text == 'Identify the request');
    await t.enterText(first, 'A revised request');
    await t.pump();
    expect(find.bySemanticsLabel(RegExp('^Explain, step 1')), findsOneWidget);
    semantics.dispose();
  });

  testWidgets('rail exposes selected collection and selected item', (t) async {
    final semantics = t.ensureSemantics();
    await t.pumpWidget(MaterialApp(theme: AdklTheme.light(), home: Scaffold(
      body: SizedBox(width: 320, child: NavRail(
        skills: [SkillSummary.fromJson({'name': 'Explain'})], tools: const [],
        tab: 0, onTab: (_) {}, selectedSkill: 'Explain', selectedTool: null,
        onSkill: (_) {}, onTool: (_) {}, query: '', onQuery: (_) {}, env: null)))));
    expect(t.getSemantics(find.text('Skills')).flagsCollection.isSelected == Tristate.isTrue, isTrue);
    expect(t.getSemantics(find.text('Tools')).flagsCollection.isSelected == Tristate.isTrue, isFalse);
    expect(t.getSemantics(find.text('Explain')).flagsCollection.isSelected == Tristate.isTrue, isTrue);
    semantics.dispose();
  });

  testWidgets('chat message and send action are named and keyboard reachable', (t) async {
    final semantics = t.ensureSemantics();
    final events = StreamController<Map<String, dynamic>>();
    events.stream.listen((_) {});
    addTearDown(events.close);
    await t.pumpWidget(MaterialApp(theme: AdklTheme.light(), home: Scaffold(
      body: ChatPanel(api: ChatApi(events)))));
    await t.pumpAndSettle();
    expect(find.bySemanticsLabel(RegExp('Test message')), findsOneWidget);
    expect(find.bySemanticsLabel('Send message'), findsOneWidget);
    expect(t.getSemantics(find.bySemanticsLabel('Send message')).flagsCollection.isButton, isTrue);
    await t.tap(find.byType(TextField));
    await t.sendKeyEvent(LogicalKeyboardKey.tab);
    await t.pump();
    expect(t.getSemantics(find.bySemanticsLabel('Send message')).flagsCollection.isFocused == Tristate.isTrue, isTrue);
    expect(await meetsGuideline(labeledTapTargetGuideline).matchAsync(t), isNull);
    await t.pumpWidget(const SizedBox());
    semantics.dispose();
  });
}
