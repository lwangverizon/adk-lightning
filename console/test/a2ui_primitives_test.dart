import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:adkl_console/a2ui_examples.dart';
import 'package:adkl_console/a2ui_view.dart';

Future<void> pump(
  WidgetTester tester,
  Map<String, dynamic> snapshot, {
  ValueChanged<Map<String, dynamic>>? onAction,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: Center(
          child: SizedBox(
            width: 380,
            child: SingleChildScrollView(
              child: A2uiView(a2ui: snapshot, onAction: onAction),
            ),
          ),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  for (final version in ['v0.9', 'v1.0']) {
    for (final kind in a2uiExamples.keys) {
      testWidgets('$version $kind has a basic renderer', (tester) async {
        await pump(tester, a2uiExample(kind, version));
        expect(tester.takeException(), isNull);
        expect(find.text('client component'), findsNothing);
        expect(find.textContaining('invalid or unsupported'), findsNothing);
        if (kind == 'Icon') expect(find.byIcon(Icons.home), findsOneWidget);
        if (kind == 'List') {
          expect(find.text('First item'), findsOneWidget);
          expect(find.text('Second item'), findsOneWidget);
        }
        if (kind == 'TextField') {
          expect(find.widgetWithText(TextFormField, 'Ada'), findsOneWidget);
        }
        if (kind == 'Slider') {
          final slider = tester.widget<Slider>(find.byType(Slider));
          expect(slider.value, 25);
          expect(slider.divisions, version == 'v1.0' ? 10 : null);
        }
      });
    }

    testWidgets('$version tab and modal interaction changes visible content', (
      tester,
    ) async {
      await pump(tester, a2uiExample('Tabs', version));
      expect(find.text('First content'), findsOneWidget);
      expect(find.text('Second content'), findsNothing);
      await tester.tap(find.text('Second tab'));
      await tester.pumpAndSettle();
      expect(find.text('First content'), findsNothing);
      expect(find.text('Second content'), findsOneWidget);
      await pump(tester, a2uiExample('Modal', version));
      expect(find.text('Modal subscription'), findsNothing);
      await tester.tap(find.byType(InkWell).first);
      await tester.pumpAndSettle();
      expect(find.text('Modal subscription'), findsOneWidget);
      await tester.tap(find.byType(Checkbox));
      await tester.pumpAndSettle();
      expect(tester.widget<Checkbox>(find.byType(Checkbox)).value, isTrue);
      expect(find.text('true'), findsOneWidget);
      await tester.tap(find.text('Close'));
      await tester.pumpAndSettle();
      expect(find.text('Modal subscription'), findsNothing);
    });

    testWidgets('$version controls update bindings and action context', (
      tester,
    ) async {
      final form = a2uiExample('TextField', version);
      form['components'] = [
        {
          'id': 'root',
          'component': 'Column',
          'children': [
            'input',
            'mirror',
            'check',
            'choices',
            'slider',
            'button',
          ],
        },
        {
          'id': 'input',
          'component': 'TextField',
          ...a2uiExamples['TextField']!,
        },
        {
          'id': 'mirror',
          'component': 'Text',
          'text': {'path': '/name'},
        },
        {'id': 'check', 'component': 'CheckBox', ...a2uiExamples['CheckBox']!},
        {
          'id': 'choices',
          'component': 'ChoicePicker',
          ...a2uiExamples['ChoicePicker']!,
        },
        {'id': 'slider', 'component': 'Slider', ...a2uiExamples['Slider']!},
        {
          'id': 'button',
          'component': 'Button',
          'child': 'label',
          'action': {
            'event': {
              'name': 'save',
              'context': {
                'name': {'path': '/name'},
                'subscribed': {'path': '/subscribed'},
                'topics': {'path': '/topics'},
                'volume': {'path': '/volume'},
              },
            },
          },
        },
        {'id': 'label', 'component': 'Text', 'text': 'Save'},
      ];
      Map<String, dynamic>? event;
      await pump(tester, form, onAction: (v) => event = v);
      await tester.enterText(find.byType(TextFormField), 'Grace');
      await tester.pump();
      expect(find.text('Grace'), findsNWidgets(2));
      await tester.tap(find.byType(Checkbox));
      await tester.tap(find.text('Math'));
      await tester.drag(find.byType(Slider), const Offset(60, 0));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(OutlinedButton));
      await tester.pumpAndSettle();
      expect(event?['version'], version);
      final values = event?['event']['context'] as Map;
      expect(values['name'], 'Grace');
      expect(values['subscribed'], isTrue);
      expect(values['topics'], ['math']);
      expect(values['volume'], greaterThan(25));
      expect(
        (form['dataModel'] as Map)['name'],
        'Ada',
        reason: 'local edits must not mutate host snapshots',
      );
    });
  }

  testWidgets(
    'relative bindings in repeated controls do not leak between items',
    (tester) async {
      final form = a2uiExample('List', 'v1.0');
      form['components'] = [
        {
          'id': 'root',
          'component': 'List',
          'children': {'componentId': 'row', 'path': '/items'},
        },
        {
          'id': 'row',
          'component': 'Column',
          'children': ['input', 'text'],
        },
        {
          'id': 'input',
          'component': 'TextField',
          'label': 'Item',
          'value': {'path': 'name'},
        },
        {
          'id': 'text',
          'component': 'Text',
          'text': {'path': 'name'},
        },
      ];
      await pump(tester, form);
      await tester.enterText(find.byType(TextFormField).first, 'Changed');
      await tester.pumpAndSettle();
      expect(find.text('Changed'), findsNWidgets(2));
      expect(find.text('Second item'), findsNWidgets(2));
      final updated = {
        ...form,
        'dataModel': {
          'items': [
            {'name': 'Server update'},
          ],
        },
      };
      await pump(tester, updated);
      expect(find.text('Changed'), findsNothing);
      expect(find.text('Server update'), findsNWidgets(2));
      expect(find.text('Second item'), findsNothing);
    },
  );

  testWidgets(
    'choice filter and single selection preserve the string-list contract',
    (tester) async {
      final snapshot = a2uiExample('ChoicePicker', 'v1.0');
      (snapshot['components'] as List).first['variant'] = 'mutuallyExclusive';
      await pump(tester, snapshot);
      await tester.tap(find.text('Math'));
      await tester.tap(find.text('Science'));
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<FilterChip>(find.widgetWithText(FilterChip, 'Math'))
            .selected,
        isFalse,
      );
      expect(
        tester
            .widget<FilterChip>(find.widgetWithText(FilterChip, 'Science'))
            .selected,
        isTrue,
      );
      await tester.enterText(find.byType(TextField), 'sci');
      await tester.pumpAndSettle();
      expect(find.text('Math'), findsNothing);
      expect(find.text('Science'), findsOneWidget);
    },
  );

  testWidgets('bad version, function and media URL produce findings', (
    tester,
  ) async {
    await pump(tester, {...a2uiExample('Text', 'v1.0'), 'version': 'v2.0'});
    expect(find.textContaining('Unsupported A2UI version'), findsOneWidget);
    await pump(tester, {
      'root': 'root',
      'components': [
        {
          'id': 'root',
          'component': 'Text',
          'text': {'call': 'unregistered', 'args': {}},
        },
      ],
    });
    expect(find.textContaining('Unsupported catalog function'), findsOneWidget);
    await pump(tester, {
      'root': 'root',
      'components': [
        {'id': 'root', 'component': 'Video', 'url': 'javascript:alert(1)'},
      ],
    });
    expect(find.textContaining('Media requires an HTTP(S)'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('date input opens a picker and validates bounds', (tester) async {
    await pump(tester, a2uiExample('DateTimeInput', 'v1.0'));
    await tester.enterText(find.byType(TextFormField), '2020-01-01T12:00:00');
    await tester.pumpAndSettle();
    expect(find.text('Before minimum'), findsOneWidget);
    await tester.enterText(find.byType(TextFormField), '2026-02-30T12:00:00');
    await tester.pumpAndSettle();
    expect(find.text('Enter a valid calendar date'), findsOneWidget);
    await tester.tap(find.byTooltip('Choose date/time'));
    await tester.pumpAndSettle();
    expect(find.byType(DatePickerDialog), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
  });
}
