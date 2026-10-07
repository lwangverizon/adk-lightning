import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:adkl_console/api.dart';
import 'package:adkl_console/rail.dart';
import 'package:adkl_console/theme.dart';

ToolInfo _t(String n, String cls, int tok) => ToolInfo(
    name: n, summary: 'does $n', sideEffect: cls, sideEffectInferred: true,
    requiresAuth: false, estTokens: tok, params: const []);

void main() {
  final tools = [
    _t('add_to_checkout_v2', 'write', 1338),
    _t('twofa_initialize', 'auth', 300),
    _t('get_offers', 'read', 400),
    _t('render_product_cta', 'render', 900),
  ];
  final skills = [
    SkillSummary(name: 'prospect-cart-management', path: 'p', overridden: false,
        description: '', tools: const ['add_to_checkout_v2'], estTokens: 4173,
        errors: 0, warnings: 3),
  ];

  Future<void> pump(WidgetTester t, {int tab = 0}) => t.pumpWidget(MaterialApp(
        theme: AdklTheme.light(),
        home: Scaffold(
          body: SizedBox(
            width: 320, height: 800,
            child: NavRail(
              skills: skills, tools: tools, tab: tab, onTab: (_) {},
              selectedSkill: 'prospect-cart-management', selectedTool: null,
              onSkill: (_) {}, onTool: (_) {}, query: '', onQuery: (_) {},
              env: null,
            ),
          ),
        ),
      ));

  testWidgets('both collections are in the rail with counts', (t) async {
    await pump(t);
    expect(find.text('Skills'), findsOneWidget);
    expect(find.text('Tools'), findsOneWidget);
    expect(find.text('4'), findsWidgets); // 4 tools
  });

  testWidgets('skill row shows cost and finding count', (t) async {
    await pump(t);
    expect(find.text('prospect-cart-management'), findsOneWidget);
    expect(find.text('~4173'), findsOneWidget);
    expect(find.text('3'), findsWidgets);
  });

  testWidgets('tools tab groups by side-effect, write and auth first',
      (t) async {
    await pump(t, tab: 1);
    // Grouping front-loads the safety signal: write/auth are what you audit.
    expect(find.text('WRITE'), findsOneWidget);
    expect(find.text('AUTH'), findsOneWidget);
    expect(find.text('READ'), findsOneWidget);
    expect(find.text('RENDER'), findsOneWidget);
    expect(find.text('add_to_checkout_v2'), findsOneWidget);

    final write = t.getTopLeft(find.text('WRITE')).dy;
    final read = t.getTopLeft(find.text('READ')).dy;
    expect(write, lessThan(read));
  });
}
