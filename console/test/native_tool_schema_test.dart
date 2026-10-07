import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:adkl_console/api.dart';
import 'package:adkl_console/tool_detail.dart';
import 'package:adkl_console/theme.dart';

void main() {
  for (final known in [true, false]) {
    testWidgets('native schema and provenance at phone width: $known', (t) async {
      t.view.physicalSize = const Size(390, 844);
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.resetPhysicalSize);
      addTearDown(t.view.resetDevicePixelRatio);
      final tool = ToolInfo.fromJson({
        'name': 'render_quiz', 'summary': 'Stage a question.', 'schemaKnown': known,
        'source': 'native_factory', 'scope': 'core', 'sideEffectInferred': false,
        'params': known ? [{'name': 'question', 'type': 'string', 'required': true,
          'description': 'The question to ask.'}] : [],
      });
      await t.pumpWidget(MaterialApp(theme: AdklTheme.light(),
        builder: (context, child) => MediaQuery(data: MediaQuery.of(context).copyWith(
          textScaler: TextScaler.linear(2)), child: child!),
        home: Scaffold(body: ToolDetail(tool: tool, skills: const [], onOpenSkill: (_) {}))));
      await t.pumpAndSettle();
      expect(find.text(known ? '1 params · 1 required' : 'Schema unavailable'), findsOneWidget);
      expect(find.text('Takes no parameters.'), findsNothing);
      await t.scrollUntilVisible(find.text('Native scope: core'), 150,
        scrollable: find.byType(Scrollable).first);
      expect(find.text('Native scope: core'), findsOneWidget);
      await t.scrollUntilVisible(find.textContaining('Declared by the native host factory'), 150,
        scrollable: find.byType(Scrollable).first);
      expect(t.takeException(), isNull);
    });
  }
}
