import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:adkl_console/markdown_text.dart';
import 'package:adkl_console/theme.dart';
import 'package:adkl_console/widget_card.dart';

Future<void> _pump(WidgetTester t, Widget child) => t.pumpWidget(MaterialApp(
      theme: AdklTheme.light(),
      home: Scaffold(
        body: SizedBox(width: 380, child: SingleChildScrollView(child: child)),
      ),
    ));

/// Collects the rendered text of every span, so bold/code/link spans can be
/// asserted on rather than just the presence of a string.
List<InlineSpan> _spans(WidgetTester t) {
  final out = <InlineSpan>[];
  for (final w in t.widgetList<SelectableText>(find.byType(SelectableText))) {
    final span = w.textSpan;
    if (span != null) {
      span.visitChildren((s) {
        out.add(s);
        return true;
      });
    }
  }
  return out;
}

void main() {
  group('markdown', () {
    testWidgets('bold survives as a span, not literal asterisks', (t) async {
      // The response-standards block mandates bold product names, so raw
      // asterisks would misrepresent what the customer sees.
      await _pump(t, const MarkdownText('An **activation fee** is \$40.'));
      final bold = _spans(t).whereType<TextSpan>().where(
          (s) => s.style?.fontWeight == FontWeight.w700);
      expect(bold.map((s) => s.text), contains('activation fee'));
      expect(find.textContaining('**'), findsNothing);
    });

    testWidgets('inline code renders monospace', (t) async {
      await _pump(t, const MarkdownText('Call `search_supports` first.'));
      final code = _spans(t).whereType<TextSpan>().where(
          (s) => s.style?.fontFamily == AdklTheme.mono);
      expect(code.map((s) => s.text), contains('search_supports'));
    });

    testWidgets('italic renders italic', (t) async {
      await _pump(t, const MarkdownText('This is *emphasis* here.'));
      final it = _spans(t).whereType<TextSpan>().where(
          (s) => s.style?.fontStyle == FontStyle.italic);
      expect(it.map((s) => s.text), contains('emphasis'));
    });

    testWidgets('double asterisk is not read as two italics', (t) async {
      await _pump(t, const MarkdownText('**bold** not *it*'));
      final spans = _spans(t).whereType<TextSpan>();
      expect(
          spans.where((s) => s.style?.fontWeight == FontWeight.w700)
              .map((s) => s.text),
          contains('bold'));
      expect(
          spans.where((s) => s.style?.fontStyle == FontStyle.italic)
              .map((s) => s.text),
          contains('it'));
    });

    testWidgets('bullets get a marker and drop the dash', (t) async {
      await _pump(t, const MarkdownText('- first\n- second'));
      expect(find.text('•'), findsNWidgets(2));
      expect(find.textContaining('- first'), findsNothing);
    });

    testWidgets('numbered lists keep their numbers', (t) async {
      await _pump(t, const MarkdownText('1. one\n2. two'));
      expect(find.text('1.'), findsOneWidget);
      expect(find.text('2.'), findsOneWidget);
    });

    testWidgets('headings render larger and bolder', (t) async {
      await _pump(t, const MarkdownText('## Key facts\nbody text'));
      final heading = _spans(t).whereType<TextSpan>().where(
          (s) => s.text == 'Key facts');
      expect(heading, isNotEmpty);
    });

    testWidgets('links show the text, and are not launchable', (t) async {
      // The console has no business opening a URL from model output; showing
      // the label is what a reviewer needs.
      await _pump(t, const MarkdownText('see [the FAQ](https://example.com/faq)'));
      final link = _spans(t).whereType<TextSpan>().where(
          (s) => s.style?.decoration == TextDecoration.underline);
      expect(link.map((s) => s.text), contains('the FAQ'));
      expect(find.textContaining('https://'), findsNothing);
    });

    testWidgets('checkbox lines render a box', (t) async {
      await _pump(t, const MarkdownText('- [ ] Step 1: call a tool'));
      expect(find.byIcon(Icons.check_box_outline_blank), findsOneWidget);
    });

    testWidgets('a pipe row with no separator stays literal text', (t) async {
      // Requiring the dash separator is what stops a prose sentence containing
      // a pipe from being swallowed as a one-column table.
      await _pump(t, const MarkdownText('| a | b |'));
      expect(find.textContaining('|'), findsWidgets);
    });

    testWidgets('tables render as a grid, not pipe soup', (t) async {
      // A live gateway turn emitted this shape when asked to compare device
      // prices, despite the response-standards block discouraging tables. The
      // panel has to render what the model produces, not what it was told to.
      await _pump(
          t,
          const MarkdownText('| Model | Price |\n'
              '| :--- | :--- |\n'
              '| **iPhone 15** | \$729.99 |\n'
              '| **iPhone 14** | \$629.99 |'));
      expect(find.textContaining('|'), findsNothing);
      // Header and both data rows survive as cells.
      expect(find.textContaining('Model'), findsOneWidget);
      expect(find.textContaining('729.99'), findsOneWidget);
      expect(find.textContaining('629.99'), findsOneWidget);
      // Bold inside a cell still renders as a span.
      final bold = _spans(t).whereType<TextSpan>().where(
          (s) => s.style?.fontWeight == FontWeight.w700);
      expect(bold.map((s) => s.text), contains('iPhone 15'));
    });

    testWidgets('a ragged row does not throw', (t) async {
      // Models drop trailing cells; a RangeError here would blank the panel.
      await _pump(
          t,
          const MarkdownText('| a | b | c |\n'
              '| --- | --- | --- |\n'
              '| only one |'));
      expect(find.textContaining('only one'), findsOneWidget);
    });

    testWidgets('empty input does not throw', (t) async {
      await _pump(t, const MarkdownText(''));
    });
  });

  group('A2UI widget card', () {
    testWidgets('type leads, because it is the dispatch key', (t) async {
      await _pump(t, const WidgetCard(widget_: {
        'id': 'Button_CTA-1',
        'provider': 'example-host',
        'type': 'Button_CTA',
        'payload': {'type': 'Button_CTA', 'buttonText': 'Continue'},
      }));
      expect(find.text('Button_CTA'), findsOneWidget);
      expect(find.text('Continue'), findsOneWidget);
    });

    testWidgets('a typeless payload is flagged, not quietly shown', (t) async {
      // Production drops a typeless payload on the SSE path, so it would never
      // reach the customer. Silence here would hide that.
      await _pump(t, const WidgetCard(widget_: {
        'id': 'x', 'provider': 'example-host', 'payload': {'label': 'hi'},
      }));
      expect(find.text('widget with no type'), findsOneWidget);
      expect(find.textContaining('would not reach the customer'),
          findsOneWidget);
    });

    testWidgets('lists are summarized by shape, not dumped', (t) async {
      // A 40-row product list would bury a 380px panel; the count is the part
      // worth checking.
      await _pump(t, const WidgetCard(widget_: {
        'type': 'Product_Card',
        'payload': {
          'type': 'Product_Card',
          'items': [
            {'productId': 'a', 'name': 'iPhone', 'price': 1},
            {'productId': 'b', 'name': 'Pixel', 'price': 2},
          ],
        },
      }));
      expect(find.textContaining('2 items'), findsOneWidget);
      expect(find.textContaining('productId'), findsOneWidget);
    });

    testWidgets('raw JSON is available on demand', (t) async {
      await _pump(t, const WidgetCard(widget_: {
        'type': 'Button_CTA',
        'payload': {'type': 'Button_CTA', 'buttonText': 'Go'},
      }));
      await t.tap(find.byIcon(Icons.data_object));
      await t.pumpAndSettle();
      expect(find.textContaining('"buttonText": "Go"'), findsOneWidget);
    });

    testWidgets('a sign-in CTA payload renders', (t) async {
      // Contract fixture; this test does not execute a business tool or sign in.
      await _pump(t, const WidgetCard(widget_: {
        'id': 'button-cta-abc123',
        'provider': 'example-host',
        'type': 'Button_CTA',
        'payload': {
          'type': 'Button_CTA',
          'buttonText': 'Sign in',
          'buttonAction': 'sign-in',
          'buttonType': 'primary',
          'initiator': 'example-host',
          'target': '_top',
        },
      }));
      expect(find.text('Button_CTA'), findsOneWidget);
      expect(find.text('Sign in'), findsOneWidget);
      expect(find.textContaining('sign-in'), findsOneWidget);
    });

    testWidgets('a nested payload.payload is summarized by shape', (t) async {
      // The appointment branch nests a whole appointmentDetails tree under a
      // second `payload` key. Flattening it would bury a 380px panel, so the
      // card shows its keys and leaves the detail to the raw view.
      await _pump(t, const WidgetCard(widget_: {
        'type': 'Button_CTA',
        'payload': {
          'type': 'Button_CTA',
          'buttonText': 'Find a store',
          'buttonAction': 'findastore',
          'payload': {
            'success': true,
            'appointmentDetails': {'storeId': '', 'contactInformation': {}},
          },
        },
      }));
      expect(find.textContaining('appointmentDetails'), findsOneWidget);
      // Summarized, not expanded: the leaf values stay in the raw view.
      expect(find.textContaining('storeId'), findsNothing);
    });

    testWidgets('an empty payload says so', (t) async {
      await _pump(t, const WidgetCard(widget_: {
        'type': 'Button_CTA', 'payload': {'type': 'Button_CTA'},
      }));
      expect(find.text('empty payload'), findsOneWidget);
    });
  });
}
