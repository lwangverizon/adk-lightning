import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:adkl_console/main.dart';
import 'package:adkl_console/theme.dart';
import 'package:adkl_console/chat_panel.dart';
import 'native_editor_test.dart' show EditorApi;

void main() {
  for (final width in [390.0, 768.0, 1024.0, 1280.0, 1600.0]) {
    for (final scale in [1.0, 2.0]) {
      testWidgets('editor usable at $width with text scale $scale', (t) async {
        t.view.physicalSize = Size(width, 1000);
        t.view.devicePixelRatio = 1;
        addTearDown(t.view.resetPhysicalSize);
        addTearDown(t.view.resetDevicePixelRatio);
        await t.pumpWidget(MaterialApp(theme: AdklTheme.light(),
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(context).copyWith(textScaler: TextScaler.linear(scale)), child: child!),
          home: ConsoleShell(api: EditorApi())));
        await t.pumpAndSettle();
        final description = find.widgetWithText(TextField, 'Teach math');
        expect(t.getSize(description).width, greaterThan(250));
        await t.enterText(description, 'Draft retained across views');
        await t.pump(const Duration(milliseconds: 600));
        if (find.byType(NavigationBar).evaluate().isNotEmpty) {
          await t.tap(find.descendant(of: find.byType(NavigationBar), matching: find.text('Test')));
          await t.pumpAndSettle();
          expect(find.text('Chat is not available'), findsOneWidget);
          await t.tap(find.descendant(of: find.byType(NavigationBar), matching: find.text('Editor')));
          await t.pumpAndSettle();
        }
        expect(find.widgetWithText(TextField, 'Draft retained across views'), findsOneWidget);
        await t.tap(find.text('Save'));
        await t.pumpAndSettle();
        expect(t.takeException(), isNull);
      });
    }
  }
  testWidgets('landscape keeps navigation and findings reachable', (t) async {
    t.view.physicalSize = const Size(844, 390);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    await t.pumpWidget(MaterialApp(theme: AdklTheme.light(), home: ConsoleShell(api: EditorApi())));
    await t.pumpAndSettle();
    await t.tap(find.descendant(of: find.byType(NavigationBar), matching: find.text('Findings')));
    await t.pumpAndSettle();
    expect(find.text('Entire current agent'), findsOneWidget);
    expect(t.takeException(), isNull);
  });
  testWidgets('resize preserves draft and chat element', (t) async {
    t.view.physicalSize = const Size(1600, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    await t.pumpWidget(MaterialApp(theme: AdklTheme.light(), home: ConsoleShell(api: EditorApi())));
    await t.pumpAndSettle();
    final chat = t.state(find.byType(ChatPanel, skipOffstage: false));
    await t.enterText(find.widgetWithText(TextField, 'Teach math'), 'Keep this draft');
    await t.pump(const Duration(milliseconds: 600));
    t.view.physicalSize = const Size(390, 844);
    await t.pumpAndSettle();
    expect(t.state(find.byType(ChatPanel, skipOffstage: false)), same(chat));
    expect(find.widgetWithText(TextField, 'Keep this draft'), findsOneWidget);
    await t.tap(find.byTooltip('Open library'));
    await t.pumpAndSettle();
    expect(find.text('Agent library'), findsOneWidget);
    await t.tap(find.byTooltip('Close library'));
    await t.pumpAndSettle();
    t.view.physicalSize = const Size(1600, 1000);
    await t.pumpAndSettle();
    expect(t.state(find.byType(ChatPanel, skipOffstage: false)), same(chat));
    expect(find.widgetWithText(TextField, 'Keep this draft'), findsOneWidget);
    expect(t.takeException(), isNull);
  });
}
