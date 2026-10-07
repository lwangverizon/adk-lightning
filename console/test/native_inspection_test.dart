import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:adkl_console/project_panel.dart';

void main() {
  testWidgets('native inspection exposes skills, scoped tools and observed request', (tester) async {
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: SingleChildScrollView(
      child: NativeInspectionReport(report: {
        'agent': {'name': 'host', 'type': 'LlmAgent', 'model': 'fixture', 'children': [], 'adkl': [{
          'features': ['staged_widgets'], 'delivery': 'host_sink',
          'skills': [{'name': 'teach', 'description': 'Teach supplied facts',
            'instructions': 'Use the supplied curriculum.', 'tools': ['lookup']}],
          'tools': [{'name': 'lookup', 'scope': 'skill', 'description': 'Read curriculum'}],
          'audit': {'passed': true, 'evidence': 'native_declarations'},
        }]},
        'lastRequest': {'instruction': 'Observed prompt'},
        'authoringFindings': [{'skill': 'old-name', 'message': 'Mapped skill is absent from the native factory'}],
      }),
    ))));
    expect(find.text('host · LlmAgent'), findsOneWidget);
    expect(find.textContaining('old-name: Mapped skill is absent'), findsOneWidget);
    expect(find.textContaining('staged_widgets'), findsOneWidget);
    await tester.tap(find.text('teach'));
    await tester.pumpAndSettle();
    expect(find.text('Use the supplied curriculum.'), findsOneWidget);
    await tester.ensureVisible(find.text('Tools and scope'));
    await tester.tap(find.text('Tools and scope'));
    await tester.pumpAndSettle();
    expect(find.text('lookup · skill'), findsOneWidget);
    await tester.ensureVisible(find.text('Last local test request'));
    await tester.tap(find.text('Last local test request'));
    await tester.pumpAndSettle();
    expect(find.text('Observed prompt'), findsOneWidget);
  });

  testWidgets('staged widget specs and companion delivery are visible', (tester) async {
    // The server reported these specs all along and this panel rendered none of
    // them, so a host's whole staged surface was reachable only by expanding the
    // raw JSON. The role is the reply contract; a spec whose payload type is
    // decided by its build declares none, which must not read as a blank field.
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: SingleChildScrollView(
      child: NativeInspectionReport(report: {
        'agent': {'name': 'host', 'type': 'LlmAgent', 'model': 'fixture', 'children': [], 'adkl': [{
          'features': ['staged_widgets'], 'delivery': 'host_sink',
          'companionDelivery': 'host_sink',
          'widgets': [
            {'key': 'plan_card', 'register': 'temp:cards', 'payloadType': 'plan',
             'role': 'data_primary'},
            {'key': 'nav', 'register': '', 'payloadType': null, 'role': null},
          ],
          'skills': [], 'tools': [],
        }]},
      }),
    ))));
    expect(find.textContaining('companion: host_sink'), findsOneWidget);
    await tester.tap(find.text('Staged widgets (2)'));
    await tester.pumpAndSettle();
    expect(find.text('plan_card · data_primary'), findsOneWidget);
    expect(find.textContaining('payload: plan · register: temp:cards'), findsOneWidget);
    expect(find.text('nav · no reply contract'), findsOneWidget);
    expect(find.textContaining('payload: decided by build · register: derived'), findsOneWidget);
  });
}
