// Layout regressions for a real gateway reply, asserted geometrically.
//
// This file started as a screenshot harness -- RepaintBoundary.toImage into a
// PNG for a human to look at. That was worth doing once: the pictures found two
// bugs nothing else had caught (a table built from Rows gave every row its own
// column widths, and a valid widget card was painted with a brand-red-seeded
// container that read as an error). But toImage deadlocks flutter_tools here,
// one test per run, so the harness was replaced with what the pictures were
// really being used for.
//
// Asserting positions is also stronger than looking. "Do the columns line up"
// is a claim about x-offsets, and comparing them directly cannot be fooled by a
// glance -- the test font renders every glyph as a box, so a misalignment of a
// few pixels is exactly the thing an eye would miss in a PNG.
//
// The fixture is a verbatim reply from a live UAT turn, kept so these assert
// against real model output rather than something convenient. It is the turn
// that showed the agent emits markdown tables despite the response-standards
// block telling it not to, which is why table rendering exists at all.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:adkl_console/markdown_text.dart';
import 'package:adkl_console/theme.dart';
import 'package:adkl_console/widget_card.dart';

/// Verbatim from a live gateway turn: "compare the iPhone 15 and iPhone 15 Pro
/// side by side" from the former application snapshot on gemini-3.6-flash.
const _liveReply = '''
Here is a side-by-side comparison of the **iPhone 15** and **iPhone 15 Pro** to help you choose the right device for your needs.

### iPhone 15 vs. iPhone 15 Pro

| Feature | **iPhone 15** | **iPhone 15 Pro** |
| :--- | :--- | :--- |
| **Display** | 6.1-inch Super Retina XDR (60Hz) | 6.1-inch Super Retina XDR with ProMotion (up to 120Hz) |
| **Processor** | A16 Bionic chip | A17 Pro chip |
| **Build Material** | Aluminum with color-infused glass back | Titanium design with textured matte glass back |
| **Action Button** | Ring/Silent Switch | Customizable Action button |

### Key Differences
* **Performance & Gaming:** The **iPhone 15 Pro** features the **A17 Pro** chip, delivering faster graphics performance.
* **Camera Capabilities:** The **iPhone 15 Pro** adds a dedicated 3x Telephoto lens for detailed zoom photos.

*Note: Live inventory, current pricing, and promotional offers cannot be retrieved directly in this session.*
''';

/// 380px is the real panel width. Rendering wider would hide exactly the
/// overflow and clipping these tests exist to catch.
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
  group('a real gateway reply', () {
    testWidgets('renders without overflowing the panel', (t) async {
      await _pump(t, const MarkdownText(_liveReply));
      // Overflow is a painted error rather than a thrown one, but flutter_test
      // surfaces it as an exception, so this is the check that matters.
      expect(t.takeException(), isNull);

      // The table became a grid rather than pipe text.
      expect(find.textContaining('|'), findsNothing);
      // Twice, not once: this reply names the A17 Pro in a table cell and again
      // in a bullet below it. findsOneWidget failed here on real output -- the
      // fixture is the model's, not mine to tidy into something convenient.
      expect(find.textContaining('A17 Pro'), findsNWidgets(2));

      // Headings, bold and bullets all survived alongside it.
      expect(find.textContaining('Key Differences'), findsOneWidget);
      expect(find.text('•'), findsNWidgets(2));
    });

    testWidgets('table columns line up across every row', (t) async {
      // The bug this pins: built from a Column of Rows, each row sized its own
      // cells, so column 2 started at a different x on every row. A comparison
      // table whose columns do not line up is worse than no table -- the reader
      // is comparing values that are not visually paired.
      await _pump(t, const MarkdownText(_liveReply));

      // Column 1 holds the row labels; column 2 holds the iPhone 15 values.
      const col1 = ['Display', 'Processor', 'Build Material', 'Action Button'];
      const col2 = [
        '6.1-inch Super Retina XDR (60Hz)',
        'A16 Bionic chip',
        'Aluminum with color-infused glass back',
        'Ring/Silent Switch',
      ];

      double leftOf(String text) =>
          t.getTopLeft(find.textContaining(text).first).dx;

      for (final column in [col1, col2]) {
        final lefts = column.map(leftOf).toList();
        for (var i = 1; i < lefts.length; i++) {
          expect(lefts[i], closeTo(lefts[0], 0.5),
              reason: 'cell "${column[i]}" starts at x=${lefts[i]} but '
                  '"${column[0]}" starts at x=${lefts[0]} -- the column is '
                  'not aligned, so this is a Column-of-Rows regression');
        }
      }

      // And the two columns are genuinely distinct positions, so the assertion
      // above is not passing because everything collapsed to x=0.
      expect(leftOf(col2[0]), greaterThan(leftOf(col1[0]) + 20));
    });

    testWidgets('a wide table scrolls to reach its last column', (t) async {
      // A three-column comparison cannot fit 380px. It either overflows, clips
      // the last column silently, or scrolls. Only the third is acceptable, and
      // only if the content is really reachable -- which "is there a scroll
      // view" would not prove.
      await _pump(t, const MarkdownText(_liveReply));

      final horizontal = find.byWidgetPredicate((w) =>
          w is SingleChildScrollView && w.scrollDirection == Axis.horizontal);
      expect(horizontal, findsOneWidget);

      final scrollable = Scrollable.of(t.element(find.byType(Table)));
      expect(scrollable.position.maxScrollExtent, greaterThan(0),
          reason: 'the table should overflow 380px; if it no longer does, the '
              'fixture stopped being representative of a real reply');

      await t.drag(horizontal, const Offset(-400, 0));
      await t.pumpAndSettle();
      expect(t.takeException(), isNull);
    });

    testWidgets('a ragged row does not crash the Table', (t) async {
      // Table throws on rows of unequal length, and models do drop trailing
      // cells. The renderer pads; this proves it.
      await _pump(
          t,
          const MarkdownText('| a | b | c |\n'
              '| --- | --- | --- |\n'
              '| only one |'));
      expect(t.takeException(), isNull);
      expect(find.textContaining('only one'), findsOneWidget);
    });
  });

  group('widget card', () {
    testWidgets('a valid widget is not tinted like an error', (t) async {
      // The bug this pins: the header used secondaryContainer, which is derived
      // from the brand-red seed, so a perfectly good Button_CTA got a pink wash
      // that reads as a failure. Asserting on hue rather than an exact color so
      // a theme tweak does not break it for no reason.
      await _pump(
          t,
          const WidgetCard(widget_: {
            'id': 'button-cta-abc123',
            'provider': 'example-host',
            'type': 'Button_CTA',
            'payload': {
              'type': 'Button_CTA',
              'buttonText': 'Sign in',
              'buttonAction': 'sign-in',
            },
          }));
      expect(t.takeException(), isNull);
      expect(find.text('Button_CTA'), findsOneWidget);

      final cs = AdklTheme.light().colorScheme;
      // The header band is the first decorated Container inside the card.
      final headers = t
          .widgetList<Container>(find.byType(Container))
          .where((c) => c.decoration is BoxDecoration)
          .map((c) => (c.decoration as BoxDecoration).color)
          .whereType<Color>()
          .toList();
      expect(headers, isNotEmpty);

      // No part of a valid card may be painted in the error hue.
      final errorHue = HSLColor.fromColor(cs.error).hue;
      for (final c in headers) {
        if (c.a < 0.02) continue;   // fully transparent carries no hue
        final hue = HSLColor.fromColor(c).hue;
        final gap = (hue - errorHue).abs();
        expect(gap > 25 || HSLColor.fromColor(c).saturation < 0.05, isTrue,
            reason: 'a valid widget card is painted $c, only ${gap.toStringAsFixed(1)}° '
                'from the error hue -- it will read as a failure');
      }
    });

    testWidgets('a typeless payload IS tinted like an error', (t) async {
      // The other half: production drops a typeless payload on the SSE path, so
      // it never reaches the customer. That one should look alarming.
      await _pump(
          t,
          const WidgetCard(widget_: {
            'id': 'x',
            'provider': 'example-host',
            'payload': {'label': 'hi'},
          }));
      expect(find.text('widget with no type'), findsOneWidget);
      expect(find.byIcon(Icons.error_outline), findsOneWidget);
    });
  });
}
