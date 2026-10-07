import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:adkl_console/api.dart';
import 'package:adkl_console/theme.dart';
import 'package:adkl_console/tool_detail.dart';

void main() {
  final tool = ToolInfo(
    name: 'add_to_checkout_v2',
    summary: 'Adds items to the checkout cart and renders a cart CTA.',
    sideEffect: 'write',
    sideEffectInferred: true,
    requiresAuth: false,
    estTokens: 1338,
    params: [
      ParamInfo('request', 'dict', true, 'CartBuildRequestList object'),
      ParamInfo('bill_upload', 'bool', false, ''),
    ],
  );

  final skills = [
    SkillSummary(
        name: 'prospect-cart-management',
        path: 'prospect-cart-management',
        overridden: false,
        description: '',
        tools: const ['add_to_checkout_v2'],
        estTokens: 4173,
        errors: 0,
        warnings: 3),
    SkillSummary(
        name: 'safety-and-refusal',
        path: 'safety-and-refusal',
        overridden: false,
        description: '',
        tools: const [],
        estTokens: 0,
        errors: 0,
        warnings: 0),
  ];

  Future<void> pump(WidgetTester t, {List<SkillSummary>? s}) => t.pumpWidget(
        MaterialApp(
          theme: AdklTheme.light(),
          home: Scaffold(
            body: ToolDetail(
                tool: tool, skills: s ?? skills, onOpenSkill: (_) {}),
          ),
        ),
      );

  testWidgets('renders name, cost, and inferred class caveat', (t) async {
    await pump(t);
    expect(find.text('add_to_checkout_v2'), findsOneWidget);
    expect(find.text('~1338 tok/turn'), findsOneWidget);
    // Inference must never be presented as declared fact.
    expect(find.text('write (inferred)'), findsOneWidget);
  });

  testWidgets('flags an undocumented parameter', (t) async {
    await pump(t);
    expect(find.textContaining('undocumented'), findsOneWidget);
    expect(find.text('required'), findsOneWidget);
    expect(find.text('optional'), findsOneWidget);
  });

  testWidgets('lists only skills that bind the tool', (t) async {
    await pump(t);
    // The audit question is "who can call this?" — so a skill that does not
    // bind it must not appear.
    expect(find.widgetWithText(ActionChip, 'prospect-cart-management'),
        findsOneWidget);
    expect(find.widgetWithText(ActionChip, 'safety-and-refusal'), findsNothing);
  });

  testWidgets('says so when nothing binds the tool', (t) async {
    await pump(t, s: [skills[1]]);
    expect(find.textContaining('No skill in this agent declares'), findsOneWidget);
    expect(find.textContaining('this does not establish that it is unused'), findsOneWidget);
  });
}
