import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:adkl_console/api.dart';
import 'package:adkl_console/tool_picker.dart';

ToolInfo _tool(String name,
        {String sideEffect = 'read', int tokens = 100, bool auth = false}) =>
    ToolInfo(
      name: name,
      summary: 'summary for $name',
      sideEffect: sideEffect,
      sideEffectInferred: true,
      requiresAuth: auth,
      estTokens: tokens,
      params: const [],
    );

/// Pumps a host page and opens the picker, returning once it is on screen.
Future<void> _openPicker(
  WidgetTester tester, {
  required List<ToolInfo> catalog,
  required List<String> selected,
  required String body,
}) async {
  await tester.pumpWidget(MaterialApp(
    theme: ThemeData(useMaterial3: true),
    home: Scaffold(
      body: Builder(
        builder: (context) => TextButton(
          onPressed: () => showToolPicker(context,
              catalog: catalog, selected: selected, bodyText: body),
          child: const Text('open'),
        ),
      ),
    ),
  ));
  await tester.tap(find.text('open'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('suggests a tool the instructions call but did not bind',
      (tester) async {
    await _openPicker(
      tester,
      catalog: [_tool('get_offers'), _tool('search_products')],
      selected: const [],
      body: '- [ ] Step 1: call `get_offers` and summarize',
    );

    // The TOOL_USED_NOT_DECLARED class of bug, surfaced at authoring time.
    expect(find.textContaining('not bound'), findsOneWidget);
    expect(find.widgetWithText(ActionChip, 'get_offers'), findsOneWidget);
  });

  testWidgets('does not suggest negated or historical references',
      (tester) async {
    await _openPicker(
      tester,
      catalog: [_tool('render_product_cta'), _tool('add_to_checkout_v2')],
      selected: const [],
      body: 'Do NOT call `render_product_cta` in the same turn.\n'
          'If `add_to_checkout_v2` was already called, skip ahead.',
    );

    // Both are correct as written; suggesting them would train the user to
    // ignore the banner.
    expect(find.byType(ActionChip), findsNothing);
    expect(find.textContaining('not bound'), findsNothing);
  });

  testWidgets('warns once the running token total crosses the threshold',
      (tester) async {
    await _openPicker(
      tester,
      catalog: [_tool('heavy_render', sideEffect: 'render', tokens: 3500)],
      selected: const ['heavy_render'],
      body: '',
    );

    expect(find.textContaining('~3500 tok/turn'), findsOneWidget);
    expect(find.textContaining('re-billed on every'), findsOneWidget);
  });

  testWidgets('marks inferred side-effect class as uncertain', (tester) async {
    await _openPicker(
      tester,
      catalog: [_tool('add_to_cart', sideEffect: 'write', tokens: 200)],
      selected: const [],
      body: '',
    );

    // Trailing '?' signals inference rather than a declared fact.
    expect(find.text('write?'), findsOneWidget);
  });

  testWidgets('filters by side-effect class', (tester) async {
    await _openPicker(
      tester,
      catalog: [
        _tool('get_thing', sideEffect: 'read'),
        _tool('update_thing', sideEffect: 'write'),
      ],
      selected: const [],
      body: '',
    );

    expect(find.text('get_thing'), findsOneWidget);
    expect(find.text('update_thing'), findsOneWidget);

    await tester.tap(find.widgetWithText(FilterChip, 'Write'));
    await tester.pumpAndSettle();

    expect(find.text('get_thing'), findsNothing);
    expect(find.text('update_thing'), findsOneWidget);
  });
}
