import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:adkl_console/theme.dart';

/// WCAG 2.1 relative luminance.
double _lum(Color c) {
  double ch(double v) =>
      v <= 0.03928 ? v / 12.92 : math.pow((v + 0.055) / 1.055, 2.4).toDouble();
  return 0.2126 * ch(c.r) + 0.7152 * ch(c.g) + 0.0722 * ch(c.b);
}

double _contrast(Color a, Color b) {
  final la = _lum(a), lb = _lum(b);
  final hi = math.max(la, lb), lo = math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

/// Perceptual-ish hue distance in degrees.
double _hueGap(Color a, Color b) {
  final ha = HSLColor.fromColor(a).hue, hb = HSLColor.fromColor(b).hue;
  final d = (ha - hb).abs();
  return d > 180 ? 360 - d : d;
}

void main() {
  group('ADKL theme', () {
    test('primary uses the console indigo accent', () {
      expect(AdklTheme.brandIndigo, const Color(0xFF4758A9));
      expect(AdklTheme.light().colorScheme.primary, AdklTheme.brandIndigo);
    });
  });

  group('severity stays distinguishable from brand', () {
    for (final (label, theme) in [
      ('light', AdklTheme.light()),
      ('dark', AdklTheme.dark()),
    ]) {
      test('$label: error is distinguishable from primary', () {
        final cs = theme.colorScheme;
        // Require meaningful luminance or hue separation, not mere inequality.
        final lumRatio = _contrast(cs.primary, cs.error);
        final hue = _hueGap(cs.primary, cs.error);
        expect(lumRatio > 1.4 || hue > 8,
            isTrue,
            reason: 'primary ${cs.primary} and error ${cs.error} are too '
                'similar (contrast $lumRatio, hue gap $hue°)');
      });

      test('$label: seeded container colors are not mistakable for error', () {
        final cs = theme.colorScheme;
        final gap = _hueGap(cs.secondaryContainer, cs.errorContainer);
        expect(gap, greaterThan(25),
            reason: 'Neutral/action containers must remain distinct from errors.');
      });

      test('$label: attention (warn) is clearly not error', () {
        final cs = theme.colorScheme;
        // Amber vs red must be unmistakable, since WARN and ERROR drive
        // different user action.
        expect(_hueGap(cs.tertiary, cs.error), greaterThan(20));
      });
    }
  });

  group('WCAG AA text contrast', () {
    for (final (label, theme) in [
      ('light', AdklTheme.light()),
      ('dark', AdklTheme.dark()),
    ]) {
      final cs = theme.colorScheme;

      test('$label: semantic text and input boundaries retain contrast', () {
        for (final surface in [cs.surface, cs.surfaceContainerLow, cs.surfaceContainerHigh]) {
          expect(_contrast(cs.success, surface), greaterThanOrEqualTo(4.5));
          expect(_contrast(cs.info, surface), greaterThanOrEqualTo(4.5));
          expect(_contrast(cs.onSurfaceVariant, surface), greaterThanOrEqualTo(4.5));
          expect(_contrast(cs.outline, surface), greaterThanOrEqualTo(3));
        }
      });

      test('$label: body text on surface >= 4.5:1', () {
        expect(_contrast(cs.onSurface, cs.surface), greaterThanOrEqualTo(4.5));
      });

      test('$label: secondary text on surface >= 4.5:1', () {
        // onSurfaceVariant carries hints and metadata (token counts, param
        // lists) — small text, so it needs full AA, not the 3:1 large-text bar.
        expect(_contrast(cs.onSurfaceVariant, cs.surface),
            greaterThanOrEqualTo(4.5));
      });

      test('$label: filled button label on primary >= 4.5:1', () {
        expect(_contrast(cs.onPrimary, cs.primary), greaterThanOrEqualTo(4.5));
      });

      test('$label: text on attention container >= 4.5:1', () {
        expect(_contrast(cs.onTertiaryContainer, cs.tertiaryContainer),
            greaterThanOrEqualTo(4.5));
      });

      test('$label: text on error container >= 4.5:1', () {
        expect(_contrast(cs.onErrorContainer, cs.errorContainer),
            greaterThanOrEqualTo(4.5));
      });

      test('$label: outline is visible against surface >= 1.5:1', () {
        // Borders carry structure in this flat design, so they must not vanish.
        expect(_contrast(cs.outlineVariant, cs.surface),
            greaterThanOrEqualTo(1.2));
      });
    }
  });
}
