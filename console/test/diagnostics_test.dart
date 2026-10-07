import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:adkl_console/api.dart';
import 'package:adkl_console/diagnostics.dart';
import 'package:adkl_console/rail.dart';
import 'package:adkl_console/theme.dart';

void main() {
  final findings = [
    Finding('WARN', 'SKILL_BODY_LARGE', 'shopping', 'Estimated 4200 tokens.', displayTitle: 'Long instructions', subjectKind: 'skill'),
    Finding('WARN', 'WIDGET_OUTPUT_MISSING', 'shopping', 'No display tool declared.', displayTitle: 'No widget declared', subjectKind: 'skill', remediation: 'A declaration does not prove a widget rendered.'),
    Finding('ERROR', 'TOOL_UNRESOLVED', 'shopping', 'lookup is absent.', displayTitle: 'Tool not found', subjectKind: 'skill'),
    Finding('INFO', 'TOOL_NOT_IN_ANY_SKILL', 'shopping', 'No skill references this tool.', displayTitle: 'Not linked to a skill', subjectKind: 'tool', remediation: 'No skill link does not mean unused or unreachable.'),
  ];

  Future<void> pumpRail(WidgetTester tester, {int initialTab = 0,
      bool stale = false}) async {
    var attention = false;
    var tab = initialTab;
    await tester.pumpWidget(MaterialApp(theme: AdklTheme.light(),
      home: Scaffold(body: StatefulBuilder(builder: (context, setState) => SizedBox(
        width: 240,
        child: NavRail(
          skills: [SkillSummary.fromJson({'name': 'shopping'}),
            SkillSummary.fromJson({'name': 'quiet'})],
          tools: [ToolInfo.fromJson({'name': 'shopping'}),
            ToolInfo.fromJson({'name': 'quiet'})],
          findings: findings, findingsStale: stale,
          needsAttention: attention,
          onNeedsAttention: (value) => setState(() => attention = value),
          tab: tab, onTab: (value) => setState(() => tab = value),
          selectedSkill: null, selectedTool: null,
          onSkill: (_) {}, onTool: (_) {}, query: '', onQuery: (_) {}, env: null,
        ),
      )))));
  }

  testWidgets('badges cap at two and overflow exposes all details without clipping', (t) async {
    await pumpRail(t);
    expect(find.text('Tool not found'), findsOneWidget);
    expect(find.text('+1'), findsOneWidget);
    expect(find.text('Not linked to a skill'), findsNothing);
    expect(find.byIcon(Icons.check_circle_outline), findsNothing);
    expect(t.takeException(), isNull);
    await t.tap(find.text('+1'));
    await t.pumpAndSettle();
    expect(find.text('Static checks'), findsOneWidget);
    expect(find.text('No widget declared'), findsOneWidget);
    expect(find.textContaining('A declaration does not prove'), findsOneWidget);
    await t.tap(find.text('Close'));
    await t.pumpAndSettle();
    expect(find.text('Static checks'), findsNothing);
  });

  testWidgets('attention filter includes info, composes with tabs and preserves subject ownership', (t) async {
    await pumpRail(t);
    await t.tap(find.text('Needs attention'));
    await t.pumpAndSettle();
    expect(find.text('quiet'), findsNothing);
    expect(find.text('shopping'), findsOneWidget);
    await t.tap(find.text('Tools'));
    await t.pumpAndSettle();
    expect(find.text('quiet'), findsNothing);
    expect(find.text('Not linked to a skill'), findsOneWidget);
    expect(find.text('Tool not found'), findsNothing);
    await t.tap(find.text('Not linked to a skill'));
    await t.pumpAndSettle();
    expect(find.textContaining('does not mean unused or unreachable'), findsOneWidget);
    await t.tap(find.text('Technical details'));
    await t.pumpAndSettle();
    expect(find.text('TOOL_NOT_IN_ANY_SKILL'), findsOneWidget);
    expect(t.takeException(), isNull);
  });

  test('backend subject and guidance are authoritative even for new rules', () {
    final f = Finding.fromJson({'severity': 'INFO', 'rule': 'NEW_RULE',
      'subject': {'kind': 'tool', 'name': 'shared'}, 'title': 'Backend title',
      'remediation': 'Backend guidance', 'category': 'Authoring'});
    expect(f.targetsTool, isTrue);
    expect(f.skill, 'shared');
    expect(f.title, 'Backend title');
    expect(f.nextStep, 'Backend guidance');
    expect(f.category, 'Authoring');
  });

  testWidgets('updated findings remove obsolete badges without recreating the rail', (t) async {
    final source = ValueNotifier<List<Finding>>(findings);
    addTearDown(source.dispose);
    await t.pumpWidget(MaterialApp(home: Scaffold(body: ValueListenableBuilder<List<Finding>>(
      valueListenable: source,
      builder: (context, value, _) => SizedBox(width: 240, child: NavRail(
        skills: [SkillSummary.fromJson({'name': 'shopping'})], tools: const [],
        findings: value, tab: 0, onTab: (_) {}, selectedSkill: null,
        selectedTool: null, onSkill: (_) {}, onTool: (_) {}, query: '',
        onQuery: (_) {}, env: null,
      )),
    ))));
    expect(find.text('Tool not found'), findsOneWidget);
    source.value = [];
    await t.pumpAndSettle();
    expect(find.text('Tool not found'), findsNothing);
    expect(find.byType(ActionChip), findsNothing);
    expect(find.byIcon(Icons.check_circle_outline), findsNothing);
  });

  testWidgets('dirty edits label results as potentially outdated', (t) async {
    await pumpRail(t, stale: true);
    expect(find.text('Edits pending · findings may be outdated'), findsOneWidget);
    expect(find.byIcon(Icons.check_circle_outline), findsNothing);
  });

  testWidgets('unknown rules retain explanation and technical code; repeated rules share a badge', (t) async {
    await t.pumpWidget(MaterialApp(home: Scaffold(body: FindingBadges(findings: [
      Finding('INFO', 'NEW_CHECK', 'a', 'First occurrence'),
      Finding('INFO', 'NEW_CHECK', 'a', 'Second occurrence'),
    ]))));
    expect(find.text('new check'), findsOneWidget);
    await t.tap(find.text('new check'));
    await t.pumpAndSettle();
    expect(find.text('First occurrence'), findsOneWidget);
    expect(find.text('Second occurrence'), findsOneWidget);
    expect(find.byIcon(Icons.warning_amber_rounded), findsNothing);
  });
}
