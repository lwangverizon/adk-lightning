import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:adkl_console/a2ui_view.dart';
import 'package:adkl_console/theme.dart';
import 'package:adkl_console/widget_card.dart';

/// Navigation-card contract fixture: single-child and multi-child components,
/// separate button labels, and an unreferenced root that arrives last.
const _navCard = {
  'widgetType': 'Navigation_Card_CTA',
  'root': 'root',
  'roots': ['root'],
  'warnings': <String>[],
  'components': [
    {'id': 'nav-header', 'component': 'Text', 'text': 'Transfer your number',
     'variant': 'h3'},
    {'id': 'nav-body', 'component': 'Text',
     'text': 'Bring your existing number to ExampleCo.', 'variant': 'body'},
    {'id': 'nav-div', 'component': 'Divider'},
    {'id': 'nav-btn-lbl', 'component': 'Text', 'text': 'Start transfer',
     'variant': 'body'},
    {'id': 'nav-btn', 'component': 'Button', 'child': 'nav-btn-lbl',
     'variant': 'primary',
     'action': {
       'event': {
         'name': 'navigate',
         'context': {'url': 'https://www.example.com/switch/'},
       }
     }},
    {'id': 'root-body', 'component': 'Column',
     'children': ['nav-header', 'nav-body', 'nav-div', 'nav-btn']},
    {'id': 'root', 'component': 'Card', 'child': 'root-body'},
  ],
};

Future<void> _pump(WidgetTester t, Widget child) async {
  await t.pumpWidget(MaterialApp(
    theme: AdklTheme.light(),
    home: Scaffold(
      body: Center(
        child: SizedBox(
          width: 380,
          child: SingleChildScrollView(
            child: Padding(padding: const EdgeInsets.all(12), child: child),
          ),
        ),
      ),
    ),
  ));
  await t.pumpAndSettle();
}

void main() {
  group('A2UI tree rendering', () {
    testWidgets('the real nav card renders its copy in order', (t) async {
      await _pump(t, const A2uiView(a2ui: _navCard));
      expect(t.takeException(), isNull);

      // Every piece of customer-visible copy survived the walk.
      expect(find.textContaining('Transfer your number'), findsOneWidget);
      expect(find.textContaining('Bring your existing number'), findsOneWidget);
      expect(find.textContaining('Start transfer'), findsOneWidget);

      // Order matters: a card whose button precedes its body reads wrong even
      // when every string is present.
      final header = t.getTopLeft(find.textContaining('Transfer your')).dy;
      final body = t.getTopLeft(find.textContaining('Bring your')).dy;
      final button = t.getTopLeft(find.textContaining('Start transfer')).dy;
      expect(header, lessThan(body));
      expect(body, lessThan(button));
    });

    testWidgets('the container structure is real, not flattened', (t) async {
      // Card -> Column -> [Text, Text, Divider, Button]. Rendering the leaves
      // without their containers would show the same strings with none of the
      // grouping a customer sees.
      await _pump(t, const A2uiView(a2ui: _navCard));
      expect(find.byType(Divider), findsOneWidget);
      expect(find.byType(Column), findsWidgets);
    });

    testWidgets('a heading renders larger than body copy', (t) async {
      // Approximate by design -- production themes differ -- but the hierarchy
      // has to hold or a reviewer cannot judge whether the card reads well.
      await _pump(t, const A2uiView(a2ui: _navCard));
      double sizeOf(String needle) {
        final w = t.widget<SelectableText>(
            find.ancestor(
                of: find.textContaining(needle),
                matching: find.byType(SelectableText)).first);
        return w.style?.fontSize ?? 0;
      }
      expect(sizeOf('Transfer your number'),
          greaterThan(sizeOf('Bring your existing')));
    });

    testWidgets('a button inspects its action without dispatching', (t) async {
      // The event name and target are what a reviewer checks ("does this go to
      // the right place"); firing it is not the console's business.
      await _pump(t, const A2uiView(a2ui: _navCard));
      expect(find.textContaining('navigate'), findsOneWidget);
      await t.tap(find.byType(FilledButton));
      await t.pumpAndSettle();
      expect(find.text('Action preview — not sent'), findsOneWidget);
      expect(find.textContaining('example.com/switch'), findsOneWidget);
    });

    testWidgets('a custom composite is named, not approximated', (t) async {
      // 45 catalog composites are real client components. A hand-drawn chart
      // that looks convincing and renders differently in production is worse
      // than an honest placeholder.
      await _pump(t, const A2uiView(a2ui: {
        'root': 'r',
        'roots': ['r'],
        'components': [
          {'id': 'r', 'component': 'ExampleChart', 'title': 'Bill trend',
           'series': [1, 2, 3]},
        ],
        'warnings': ['composite component(s) shown as placeholders, not '
            'rendered: ExampleChart'],
      }));
      expect(find.text('ExampleChart'), findsOneWidget);
      expect(find.textContaining('Rendered by the client'), findsOneWidget);
      expect(find.textContaining('Bill trend'), findsOneWidget);
      // Shape, not a dump: three numbers is a count, not three rows.
      expect(find.textContaining('3 items'), findsOneWidget);
    });

    testWidgets('a product card renders its badges and prices', (t) async {
      // Shape taken from a real Product_CTA conversion: 53 components mixing
      // Image, Badge, Row and Column. Badge was missing from the first switch
      // and fell through to the composite placeholder -- promo copy is exactly
      // what a reviewer checks, so it must read as a badge.
      await _pump(t, const A2uiView(a2ui: {
        'root': 'card',
        'roots': ['card'],
        'warnings': <String>[],
        'components': [
          {'id': 'name', 'component': 'Text',
           'text': 'Apple iPhone 17 Pro Max', 'variant': 'h3'},
          {'id': 'spec', 'component': 'Text',
           'text': '256 GB  ·  Cosmic Orange', 'variant': 'caption'},
          {'id': 'dpp-l', 'component': 'Text', 'text': 'DPP',
           'variant': 'caption'},
          {'id': 'dpp-v', 'component': 'Text', 'text': '\$5.55/mo',
           'variant': 'body'},
          {'id': 'dpp', 'component': 'Column', 'children': ['dpp-l', 'dpp-v']},
          {'id': 'ret-l', 'component': 'Text', 'text': 'Retail',
           'variant': 'caption'},
          {'id': 'ret-v', 'component': 'Text', 'text': '\$1199.99',
           'variant': 'body'},
          {'id': 'ret', 'component': 'Column', 'children': ['ret-l', 'ret-v']},
          {'id': 'metrics', 'component': 'Row', 'children': ['dpp', 'ret']},
          {'id': 'promo', 'component': 'Badge', 'text': 'Save \$200'},
          {'id': 'img', 'component': 'Image', 'url': 'https://example.com/a.png'},
          {'id': 'body', 'component': 'Column',
           'children': ['img', 'name', 'spec', 'metrics', 'promo']},
          {'id': 'card', 'component': 'Card', 'child': 'body'},
        ],
      }));
      expect(t.takeException(), isNull);
      expect(find.textContaining('iPhone 17 Pro Max'), findsOneWidget);
      expect(find.textContaining('5.55/mo'), findsOneWidget);
      expect(find.textContaining('Save \$200'), findsOneWidget);
      // A badge is NOT a composite placeholder.
      expect(find.textContaining('Rendered by the client'), findsNothing);
      // The image shows its URL rather than being fetched.
      expect(find.textContaining('example.com/a.png'), findsOneWidget);
      // Two metrics side by side, so a price comparison lines up.
      final dpp = t.getTopLeft(find.textContaining('5.55/mo'));
      final ret = t.getTopLeft(find.textContaining('1199.99'));
      expect(dpp.dy, closeTo(ret.dy, 1.0));
      expect(ret.dx, greaterThan(dpp.dx));
    });

    testWidgets('a dangling child reference is reported', (t) async {
      // Silent omission would look like a design choice rather than a converter
      // emitting an id it never defined.
      await _pump(t, const A2uiView(a2ui: {
        'root': 'r',
        'roots': ['r'],
        'components': [
          {'id': 'r', 'component': 'Card', 'child': 'missing-id'},
        ],
        'warnings': <String>[],
      }));
      expect(find.textContaining('unresolved component reference'),
          findsOneWidget);
    });

    testWidgets('a cyclic tree does not hang', (t) async {
      // Two components referencing each other would recurse forever; the depth
      // guard turns a hang into a message.
      await _pump(t, const A2uiView(a2ui: {
        'root': 'a',
        'roots': ['a'],
        'components': [
          {'id': 'a', 'component': 'Card', 'child': 'b'},
          {'id': 'b', 'component': 'Card', 'child': 'a'},
        ],
        'warnings': <String>[],
      }));
      expect(find.textContaining('deeper than 24 levels'), findsOneWidget);
    });

    testWidgets('no components shows the warning, not a blank', (t) async {
      await _pump(t, const A2uiView(a2ui: {
        'components': <Map<String, dynamic>>[],
        'root': null,
        'warnings': ['no converter registered for \'Mystery_CTA\''],
      }));
      expect(find.textContaining('no converter registered'), findsOneWidget);
    });

    testWidgets('several roots all render', (t) async {
      // A forest should not silently lose all but the first subtree.
      await _pump(t, const A2uiView(a2ui: {
        'root': 'one',
        'roots': ['one', 'two'],
        'components': [
          {'id': 'one', 'component': 'Text', 'text': 'first root'},
          {'id': 'two', 'component': 'Text', 'text': 'second root'},
        ],
        'warnings': <String>[],
      }));
      expect(find.textContaining('first root'), findsOneWidget);
      expect(find.textContaining('second root'), findsOneWidget);
    });
  });

  group('widget card views', () {
    testWidgets('with converter output, the rendered view leads', (t) async {
      // The default answers the question a reviewer has, rather than making them
      // hunt for it behind a toggle.
      await _pump(t, const WidgetCard(widget_: {
        'id': 'nav-card-staged',
        'type': 'Navigation_Card_CTA',
        'payload': {'type': 'Navigation_Card_CTA', 'header': 'H'},
        'a2ui': _navCard,
      }));
      expect(find.textContaining('Transfer your number'), findsOneWidget);
    });

    testWidgets('without converter output it falls back to fields', (t) async {
      // No tools.repo configured. A blank "rendered" tab would look broken; the
      // structural view still answers the debugging question.
      await _pump(t, const WidgetCard(widget_: {
        'id': 'x',
        'type': 'Button_CTA',
        'payload': {'type': 'Button_CTA', 'buttonText': 'Sign in'},
      }));
      expect(find.textContaining('Sign in'), findsOneWidget);
      expect(find.byIcon(Icons.visibility_outlined), findsNothing);
    });

    testWidgets('raw JSON is still reachable', (t) async {
      await _pump(t, const WidgetCard(widget_: {
        'type': 'Navigation_Card_CTA',
        'payload': {'type': 'Navigation_Card_CTA', 'header': 'Hello'},
        'a2ui': _navCard,
      }));
      await t.tap(find.byIcon(Icons.data_object));
      await t.pumpAndSettle();
      expect(find.textContaining('"header": "Hello"'), findsOneWidget);
    });
  });
}
