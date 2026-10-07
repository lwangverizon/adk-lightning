import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

import 'theme.dart';

/// Minimal markdown renderer for agent replies.
///
/// Deliberately hand-written rather than `flutter_markdown`. The subset agents
/// actually emit is small -- bold, italic, inline code, headings, bullets,
/// numbered lists, checkboxes, links, horizontal rules, tables -- and a full
/// CommonMark implementation brings a dependency plus its own theming surface
/// for output already constrained by the response-standards block.
///
/// Two rules from that block are honoured here rather than left to chance:
/// bold is used heavily for product and price emphasis, so `**` has to render;
/// and inline `code` marks tool and field names, which read badly in prose font.
///
/// Tables are rendered even though that block tells the agent not to use them on
/// mobile -- a live gateway turn emitted one anyway when asked to compare device
/// prices. Rendering it is what lets a reviewer see the rule was broken; pipe
/// soup reads as a renderer bug and gets blamed on the console, not the prompt.
///
/// Anything still unsupported degrades to visible text rather than being
/// swallowed, because a silently dropped line teaches a reviewer nothing.
class MarkdownText extends StatelessWidget {
  const MarkdownText(this.text, {super.key, this.fontSize = 12});

  final String text;
  final double fontSize;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final blocks = <Widget>[];
    final lines = text.split('\n');

    for (var i = 0; i < lines.length; i++) {
      final raw = lines[i];
      final line = raw.trimRight();
      final trimmed = line.trimLeft();
      final indent = line.length - trimmed.length;

      if (trimmed.isEmpty) {
        // Collapse runs of blank lines: models emit them unevenly and a
        // faithful gap wastes vertical space in a 380px panel.
        if (blocks.isNotEmpty) blocks.add(const SizedBox(height: 7));
        continue;
      }

      // horizontal rule
      if (RegExp(r'^(-{3,}|\*{3,}|_{3,})$').hasMatch(trimmed)) {
        blocks.add(Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Divider(height: 1, color: cs.outlineVariant),
        ));
        continue;
      }

      // headings
      final heading = RegExp(r'^(#{1,6})\s+(.*)$').firstMatch(trimmed);
      if (heading != null) {
        final level = heading.group(1)!.length;
        blocks.add(Padding(
          padding: EdgeInsets.only(top: blocks.isEmpty ? 0 : 6, bottom: 3),
          child: _inline(cs, heading.group(2)!,
              size: fontSize + (level <= 2 ? 2 : 1),
              weight: FontWeight.w700),
        ));
        continue;
      }

      // Tables. The response-standards block tells the agent not to use them on
      // mobile, but a live turn against the gateway emitted one anyway when
      // asked to compare device prices -- so the panel has to render what the
      // model actually produces, not what it was told to produce. Rendering it
      // is also how a reviewer notices the rule was broken; pipe soup reads as a
      // renderer bug and gets blamed on the console instead of the prompt.
      //
      // A table is a pipe row followed by a dash separator row. Requiring the
      // separator is what keeps a prose sentence containing a pipe from being
      // swallowed as a one-column table.
      if (trimmed.contains('|') && i + 1 < lines.length) {
        final next = lines[i + 1].trim();
        if (next.contains('-') && RegExp(r'^[\s:|-]+$').hasMatch(next)) {
          final header = _cells(trimmed);
          final rows = <List<String>>[];
          var j = i + 2;
          while (j < lines.length && lines[j].trim().contains('|')) {
            rows.add(_cells(lines[j].trim()));
            j++;
          }
          blocks.add(_table(cs, header, rows));
          i = j - 1;
          continue;
        }
      }

      // Checkbox FIRST: '- [ ] text' also matches the bullet pattern below,
      // so testing bullets first would make this branch unreachable.
      final check = RegExp(r'^[-*]\s*\[( |x|X)\]\s+(.*)$').firstMatch(trimmed);
      if (check != null) {
        blocks.add(Padding(
          padding: const EdgeInsets.only(bottom: 2),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(
                check.group(1)!.trim().isEmpty
                    ? Icons.check_box_outline_blank
                    : Icons.check_box,
                size: fontSize + 2,
                color: cs.onSurfaceVariant),
            const SizedBox(width: 6),
            Expanded(child: _inline(cs, check.group(2)!)),
          ]),
        ));
        continue;
      }

      // bullets, including nested
      final bullet = RegExp(r'^[-*+]\s+(.*)$').firstMatch(trimmed);
      if (bullet != null) {
        blocks.add(_listItem(cs, '•', bullet.group(1)!, indent));
        continue;
      }

      // numbered
      final numbered = RegExp(r'^(\d+)[.)]\s+(.*)$').firstMatch(trimmed);
      if (numbered != null) {
        blocks.add(_listItem(
            cs, '${numbered.group(1)}.', numbered.group(2)!, indent));
        continue;
      }

      blocks.add(Padding(
        padding: const EdgeInsets.only(bottom: 2),
        child: _inline(cs, trimmed),
      ));
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: blocks,
    );
  }

  /// Splits a `| a | b |` row. The outer pipes are optional in the wild, so
  /// empty edge cells are dropped rather than rendered as blank columns.
  List<String> _cells(String row) {
    var s = row.trim();
    if (s.startsWith('|')) s = s.substring(1);
    if (s.endsWith('|')) s = s.substring(0, s.length - 1);
    return s.split('|').map((c) => c.trim()).toList();
  }

  /// Tables render as a bordered grid with the header emphasised.
  ///
  /// `Table`, not a Column of Rows. That was the first attempt and a snapshot of
  /// a real reply showed why it fails: each Row sizes its own children, so every
  /// row got different column widths and nothing lined up vertically -- the exact
  /// misalignment a comparison table exists to avoid. `Table` shares one set of
  /// column widths across all rows, which is the whole reason it exists.
  ///
  /// Horizontally scrollable, because in a 380px panel a four-column comparison
  /// will not fit. Scrolling keeps a price column readable instead of wrapping it
  /// to two lines or clipping it.
  Widget _table(ColorScheme cs, List<String> header, List<List<String>> rows) {
    final width = [header.length, ...rows.map((r) => r.length)]
        .reduce((a, b) => a > b ? a : b);

    // Every row must have exactly `width` cells: Table throws on a ragged row,
    // and models do drop trailing cells.
    TableRow buildRow(List<String> cells, {required bool head}) => TableRow(
          decoration:
              head ? BoxDecoration(color: cs.surfaceContainerHigh) : null,
          children: [
            for (var c = 0; c < width; c++)
              Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
                // Cap the cell so a long spec string wraps instead of stretching
                // the column off the far edge of the scroll. Without this the
                // intrinsic width of a 60-character cell becomes the column
                // width, and the reply's own table has several.
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 150),
                  child: _inline(cs, c < cells.length ? cells[c] : '',
                      weight: head ? FontWeight.w700 : null),
                ),
              ),
          ],
        );

    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: DecoratedBox(
          decoration: BoxDecoration(
            border: Border.all(color: cs.outlineVariant),
            borderRadius: BorderRadius.circular(AdklTheme.rSm),
          ),
          child: Table(
            // IntrinsicColumnWidth so a column is as wide as its longest cell
            // rather than an equal share: "Feature" against a 60-character spec
            // string should not get half the table.
            defaultColumnWidth: const IntrinsicColumnWidth(),
            defaultVerticalAlignment: TableCellVerticalAlignment.top,
            border: TableBorder(
                horizontalInside: BorderSide(color: cs.outlineVariant)),
            children: [
              buildRow(header, head: true),
              for (final r in rows) buildRow(r, head: false),
            ],
          ),
        ),
      ),
    );
  }

  Widget _listItem(ColorScheme cs, String marker, String content, int indent) {
    // Two spaces per level is the common convention; clamp so a deeply indented
    // line cannot push content off a narrow panel.
    final depth = (indent ~/ 2).clamp(0, 3);
    return Padding(
      padding: EdgeInsets.only(left: depth * 12.0, bottom: 2),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(
          width: 16,
          child: Text(marker,
              style: TextStyle(fontSize: fontSize, color: cs.onSurfaceVariant)),
        ),
        Expanded(child: _inline(cs, content)),
      ]),
    );
  }

  /// Inline spans: `**bold**`, `*italic*`, `` `code` ``, `[text](url)`.
  Widget _inline(ColorScheme cs, String source,
      {double? size, FontWeight? weight}) {
    final base = TextStyle(
      fontSize: size ?? fontSize,
      height: 1.55,
      fontWeight: weight,
      color: cs.onSurface,
    );

    // One pass, longest markers first so ** is not mistaken for two *.
    final pattern = RegExp(
        r'(\*\*|__)(.+?)\1'          // bold
        r'|(\*|_)(.+?)\3'            // italic
        r'|`([^`]+)`'                // code
        r'|\[([^\]]+)\]\(([^)]+)\)', // link
        dotAll: true);

    final spans = <InlineSpan>[];
    var last = 0;
    for (final m in pattern.allMatches(source)) {
      if (m.start > last) {
        spans.add(TextSpan(text: source.substring(last, m.start)));
      }
      if (m.group(2) != null) {
        spans.add(TextSpan(
            text: m.group(2),
            style: const TextStyle(fontWeight: FontWeight.w700)));
      } else if (m.group(4) != null) {
        spans.add(TextSpan(
            text: m.group(4),
            style: const TextStyle(fontStyle: FontStyle.italic)));
      } else if (m.group(5) != null) {
        spans.add(TextSpan(
          text: m.group(5),
          style: TextStyle(
            fontFamily: AdklTheme.mono,
            fontSize: (size ?? fontSize) - 0.5,
            backgroundColor: cs.surfaceContainerHigh,
          ),
        ));
      } else if (m.group(6) != null) {
        // Rendered as a link but not launched: the console has no business
        // opening a URL from model output, and showing the target is
        // what a reviewer needs anyway.
        spans.add(TextSpan(
          text: m.group(6),
          style: TextStyle(
              color: cs.info, decoration: TextDecoration.underline),
          recognizer: TapGestureRecognizer(),
        ));
      }
      last = m.end;
    }
    if (last < source.length) {
      spans.add(TextSpan(text: source.substring(last)));
    }

    return SelectableText.rich(TextSpan(style: base, children: spans));
  }
}
