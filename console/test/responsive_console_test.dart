import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:adkl_console/theme.dart';
import 'package:adkl_console/configuration.dart';
import 'package:adkl_console/project_panel.dart';
import 'package:adkl_console/doctor_panel.dart';
import 'package:adkl_console/tool_detail.dart';
import 'package:adkl_console/api.dart';
import 'package:adkl_console/tool_picker.dart';
import 'project_panel_test.dart' show FakeApi;
import 'doctor_panel_test.dart' show DoctorApi;

void main() {
  for (final scale in [1.0, 2.0]) {
    for (final surface in ['project', 'doctor', 'tool', 'create']) {
      testWidgets('$surface at 390 with text scale $scale', (t) async {
        t.view.physicalSize = const Size(390, 844);
        t.view.devicePixelRatio = 1;
        addTearDown(t.view.resetPhysicalSize);
        addTearDown(t.view.resetDevicePixelRatio);
        final child = switch (surface) {
          'project' => ProjectPanel(api: FakeApi(), onRuntimeChanged: () {}),
          'doctor' => DoctorPanel(api: DoctorApi()),
          'create' => CreateAgentDialog(api: FakeApi()),
          _ => ToolDetail(tool: ToolInfo.fromJson({'name': 'a_long_tool_name_for_layout',
            'summary': 'Read supplied facts', 'params': []}), skills: const [], onOpenSkill: (_) {}),
        };
        await t.pumpWidget(MaterialApp(theme: AdklTheme.dark(),
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(context).copyWith(textScaler: TextScaler.linear(scale)), child: child!),
          home: Scaffold(body: child)));
        await t.pumpAndSettle();
        expect(t.takeException(), isNull);
      });
    }
  }
  testWidgets('mobile tool picker keeps complete tool names visible', (t) async {
    t.view.physicalSize = const Size(390, 844);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    await t.pumpWidget(MaterialApp(theme: AdklTheme.light(), home: Scaffold(
      body: Builder(builder: (context) => TextButton(onPressed: () => showToolPicker(
        context, catalog: [ToolInfo.fromJson({'name': 'render_celebration',
          'summary': 'Celebrate a completed achievement.'})], selected: const [], bodyText: ''),
        child: const Text('Pick tools'))))));
    await t.tap(find.text('Pick tools'));
    await t.pumpAndSettle();
    expect(t.takeException(), isNull);
    expect(t.widget<Text>(find.text('render_celebration')).overflow, isNot(TextOverflow.ellipsis));
    await t.tap(find.byTooltip('Close tool picker'));
    await t.pumpAndSettle();
    expect(find.text('Pick tools'), findsOneWidget);
  });
  testWidgets('compact configuration supports detail and return', (t) async {
    t.view.physicalSize = const Size(390, 844);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    await t.pumpWidget(MaterialApp(theme: AdklTheme.light(), home: Scaffold(
      body: ConfigurationScreen(agent: const {'agent': {'name': 'test'}},
        onSaveFeatures: (_) async {}))));
    await t.tap(find.text('Brand'));
    await t.pumpAndSettle();
    expect(t.takeException(), isNull);
    await t.tap(find.text('All configuration'));
    await t.pumpAndSettle();
    expect(find.text('System prompt'), findsOneWidget);
  });
}
