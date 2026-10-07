import 'package:adkl_console/api.dart';
import 'package:adkl_console/main.dart';
import 'package:adkl_console/rail.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'native_editor_test.dart' show EditorApi;

class TypingApi extends EditorApi {
  int lintCalls = 0;
  String? lastDescription;
  @override
  Future<LintResult> lint(String name, {required String description,
    required List<String> tools, SkillBody? doc, String? body}) async {
    lintCalls++;
    lastDescription = description;
    return LintResult([], 42, [issue]);
  }
}

void main() {
  testWidgets('typing burst does not rebuild the whole workspace on each key', (t) async {
    t.view.physicalSize = const Size(1600, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final api = TypingApi();
    await t.pumpWidget(MaterialApp(home: ConsoleShell(api: api)));
    await t.pumpAndSettle();
    var library = t.widget<NavRail>(find.byType(NavRail));
    var workspaceUpdates = 0;
    final field = find.widgetWithText(TextField, 'Skill description');
    var text = 'Teach math';
    for (var i = 0; i < 20; i++) {
      text += 'x';
      await t.enterText(field, text);
      await t.pump(const Duration(milliseconds: 10));
      final next = t.widget<NavRail>(find.byType(NavRail));
      if (!identical(library, next)) workspaceUpdates++;
      library = next;
    }
    expect(api.lintCalls, 0);
    expect(workspaceUpdates, 1, reason: 'Only the clean-to-dirty transition needs a workspace rebuild');
    await t.pump(const Duration(milliseconds: 500));
    expect(api.lintCalls, 1);
    expect(api.lastDescription, text);
    await t.tap(find.text('Save'));
    await t.pumpAndSettle();
    expect(api.description, text);
    expect(t.takeException(), isNull);
  });
}
