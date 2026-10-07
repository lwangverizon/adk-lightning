import 'dart:convert';

import 'package:flutter/material.dart';

import 'a2ui_view.dart';
import 'theme.dart';

/// Renders an A2UI widget emitted by a tool during a turn, three ways.
///
/// **rendered** walks the component tree the agent repo's own converter emitted
/// (see `adkl/a2ui.py` and `A2uiView`), so it shows approximately what a customer
/// sees. This is the default, because "does this card read well and say the right
/// thing" is the question a reviewer actually has.
///
/// **fields** and **raw** are the structural views: payload keys, then the JSON.
/// They answer the debugging questions -- did the tool pass the right data -- and
/// remain one tap away. They are also the fallback when no converter is available
/// (no `tools.repo` configured), which is better than an empty box.
///
/// The converters are NOT reimplemented here. That would be ~5,400 lines for 78
/// types, and a copy that drifts from the real one is worse than none: it would
/// show a card that reads well while production renders something else.
///
/// One production rule is enforced rather than assumed: **every payload needs a
/// `type`**. A typeless payload is dropped on the `/run_sse` path and
/// generic-flattened on `/api/ag-ui`, so it would vanish in the real channel.
/// Here it is called out in red instead.
class WidgetCard extends StatefulWidget {
  const WidgetCard({super.key, required this.widget_});

  /// {id, provider, type, payload}
  final Map<String, dynamic> widget_;

  @override
  State<WidgetCard> createState() => _WidgetCardState();
}

/// Which view of a widget the card is showing.
///
/// `rendered` leads because it answers the question a reviewer actually has --
/// does this card read well and say the right thing. `fields` and `raw` answer
/// the debugging questions, and stay one tap away.
enum _View { rendered, fields, raw }

class _WidgetCardState extends State<WidgetCard> {
  _View _view = _View.rendered;

  Map<String, dynamic> get _payload =>
      (widget.widget_['payload'] as Map<String, dynamic>?) ?? const {};

  String? get _type => widget.widget_['type'] as String?;

  /// Converter output from adkl/a2ui.py: the component tree a client renders.
  /// Absent when the agent repo is not configured, in which case the card falls
  /// back to the structural view rather than showing an empty box.
  Map<String, dynamic>? get _a2ui =>
      (widget.widget_['a2ui'] as Map?)?.cast<String, dynamic>();

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final typeless = _type == null || _type!.isEmpty;

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      decoration: BoxDecoration(
        color: cs.surface,
        borderRadius: BorderRadius.circular(AdklTheme.rLg),
        border: Border.all(
            color: typeless
                ? cs.failure.withValues(alpha: 0.5)
                : cs.info.withValues(alpha: 0.4)),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        // header: type is the dispatch key, so it leads
        Container(
          padding: const EdgeInsets.fromLTRB(10, 8, 6, 8),
          decoration: BoxDecoration(
            // Not secondaryContainer for the valid case: it is derived from the
            // brand-red seed, so it renders as a pink wash that reads as an
            // error on a perfectly good widget -- a snapshot of the real
            // Button_CTA payload showed exactly that. The theme de-tints its
            // surface ramp for the same reason. Tint with the info blue the
            // border already uses, so valid and typeless are unmistakable.
            color: typeless
                ? cs.failureContainer.withValues(alpha: 0.4)
                : cs.info.withValues(alpha: 0.09),
            borderRadius: BorderRadius.vertical(
                top: Radius.circular(AdklTheme.rLg)),
          ),
          child: Row(children: [
            Icon(typeless ? Icons.error_outline : Icons.widgets_outlined,
                size: 13, color: typeless ? cs.failure : cs.info),
            const SizedBox(width: 7),
            Expanded(
              child: Text(typeless ? 'widget with no type' : _type!,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                      fontFamily: AdklTheme.mono,
                      fontSize: 10.5,
                      fontWeight: FontWeight.w700,
                      color: typeless ? cs.failure : cs.onSurface)),
            ),
            // Rendered / fields / raw. Only offered when there is something to
            // render: with no converter the toggle would promise a view that
            // shows nothing.
            if (_a2ui != null)
              _viewButton(cs, _View.rendered, Icons.visibility_outlined,
                  'Show the rendered card'),
            _viewButton(cs, _View.fields, Icons.list_alt, 'Show payload fields'),
            _viewButton(cs, _View.raw, Icons.data_object, 'Show raw JSON'),
          ]),
        ),

        if (typeless)
          Padding(
            padding: const EdgeInsets.all(10),
            child: Text(
                'A payload without a `type` is dropped on the production SSE '
                'path and generic-flattened on /api/ag-ui, so this widget would '
                'not reach the customer. Set a type in the emitting tool.',
                style: TextStyle(
                    fontSize: 10.5, height: 1.45, color: cs.onSurface)),
          ),

        Padding(
          padding: const EdgeInsets.all(10),
          child: switch (_effectiveView) {
            _View.rendered => A2uiView(a2ui: _a2ui!),
            _View.fields => _fields(cs),
            _View.raw => _rawJson(cs),
          },
        ),
      ]),
    );
  }

  /// The view actually shown. Falls back to fields when `rendered` is selected
  /// but no converter output arrived, so a missing tools.repo degrades to the
  /// structural view instead of a blank card.
  _View get _effectiveView =>
      (_view == _View.rendered && _a2ui == null) ? _View.fields : _view;

  Widget _viewButton(ColorScheme cs, _View v, IconData icon, String tip) {
    final on = _effectiveView == v;
    return IconButton(
      tooltip: tip,
      onPressed: () => setState(() => _view = v),
      icon: Icon(icon, size: 13, color: on ? cs.primary : cs.onSurfaceVariant),
      constraints: const BoxConstraints(minWidth: 48, minHeight: 48),
      padding: EdgeInsets.zero,
    );
  }

  /// Structural view. Scalars inline; nested objects and lists summarized by
  /// shape, because a 40-row product list would bury the panel and the count is
  /// the part worth checking.
  Widget _fields(ColorScheme cs) {
    final entries = _payload.entries.where((e) => e.key != 'type').toList();
    if (entries.isEmpty) {
      return Text('empty payload',
          style: TextStyle(
              fontSize: 10.5,
              fontStyle: FontStyle.italic,
              color: cs.onSurfaceVariant));
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: entries.map((e) => _row(cs, e.key, e.value)).toList(),
    );
  }

  Widget _row(ColorScheme cs, String key, dynamic value) {
    final (text, muted) = _summarize(value);
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(
          width: 108,
          child: Text(key,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                  fontFamily: AdklTheme.mono,
                  fontSize: 10,
                  color: cs.onSurfaceVariant)),
        ),
        const SizedBox(width: 6),
        Expanded(
          child: Text(text,
              style: TextStyle(
                  fontSize: 10.5,
                  height: 1.4,
                  fontStyle: muted ? FontStyle.italic : FontStyle.normal,
                  color: muted ? cs.onSurfaceVariant : cs.onSurface)),
        ),
      ]),
    );
  }

  /// (display text, isMuted). Muted means "shape, not value".
  (String, bool) _summarize(dynamic value) {
    if (value == null) return ('null', true);
    if (value is bool || value is num) return ('$value', false);
    if (value is String) {
      final one = value.replaceAll('\n', ' ');
      return (one.length > 140 ? '${one.substring(0, 140)}…' : one, false);
    }
    if (value is List) {
      if (value.isEmpty) return ('empty list', true);
      final first = value.first;
      if (first is Map) {
        return ('${value.length} item${value.length == 1 ? '' : 's'} · keys: '
            '${(first.keys.take(4)).join(', ')}', true);
      }
      return (value.take(6).join(', ') + (value.length > 6 ? ' …' : ''), false);
    }
    if (value is Map) {
      if (value.isEmpty) return ('empty object', true);
      return ('{${value.keys.take(5).join(', ')}'
          '${value.length > 5 ? ', …' : ''}}', true);
    }
    return ('$value', false);
  }

  Widget _rawJson(ColorScheme cs) => Container(
        width: double.infinity,
        padding: const EdgeInsets.all(9),
        decoration: BoxDecoration(
          color: cs.surfaceContainerLow,
          borderRadius: BorderRadius.circular(AdklTheme.rSm),
          border: Border.all(color: cs.outlineVariant),
        ),
        child: SelectableText(
          const JsonEncoder.withIndent('  ').convert(_payload),
          style: const TextStyle(
              fontFamily: AdklTheme.mono, fontSize: 9.5, height: 1.45),
        ),
      );
}
