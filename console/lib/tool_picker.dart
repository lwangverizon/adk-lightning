import 'package:flutter/material.dart';

import 'api.dart';
import 'rail.dart' show iconForClass;
import 'theme.dart';

/// Tool picker over the generated catalog.
///
/// Three things it does that a plain checkbox list would not, each from a
/// specific finding:
///
/// 1. **Live token cost, per tool and in total.** Attaching a tool is a free
///    gesture but a recurring per-turn charge. Cost is shown on every row and
///    in a running total that warns past a threshold.
/// 2. **Side-effect class on every row**, with an "inferred" caveat. Write and
///    auth tools must be visually distinct from reads — that is the
///    deterministic-guard layer made visible at authoring time.
/// 3. **Suggestions from the skill body.** If the instructions say
///    "call `get_offers`" but it isn't bound, the picker surfaces it — the
///    exact class of bug found in three production skills.
Future<List<String>?> showToolPicker(
  BuildContext context, {
  required List<ToolInfo> catalog,
  required List<String> selected,
  required String bodyText,
}) {
  return showDialog<List<String>>(
    context: context,
    builder: (_) => _ToolPickerDialog(
      catalog: catalog,
      initial: selected,
      bodyText: bodyText,
    ),
  );
}

class _ToolPickerDialog extends StatefulWidget {
  const _ToolPickerDialog({
    required this.catalog,
    required this.initial,
    required this.bodyText,
  });

  final List<ToolInfo> catalog;
  final List<String> initial;
  final String bodyText;

  @override
  State<_ToolPickerDialog> createState() => _ToolPickerDialogState();
}

class _ToolPickerDialogState extends State<_ToolPickerDialog> {
  late Set<String> _sel;
  String _query = '';
  String _filter = 'all';

  static const _threshold = 3000;

  @override
  void initState() {
    super.initState();
    _sel = widget.initial.toSet();
  }

  /// Tools the instructions reference in an imperative position but that are
  /// not bound. Mirrors the linter's TOOL_USED_NOT_DECLARED, including its
  /// polarity rules — "Do NOT call X" and "X was already called" are correct
  /// as written and must not be suggested.
  Set<String> get _suggested {
    final out = <String>{};
    final negated = RegExp(
        r"\b(do\s+not|don't|never|avoid|without|instead\s+of|rather\s+than)\b",
        caseSensitive: false);
    final already = RegExp(
        r'\b(already|was|were|has\s+been|had\s+been|previously|earlier)\b',
        caseSensitive: false);
    final names = {for (final t in widget.catalog) t.name};

    for (final line in widget.bodyText.split('\n')) {
      if (already.hasMatch(line)) continue;
      for (final m in RegExp(r'`([a-z][a-z0-9_]{4,})`').allMatches(line)) {
        final name = m.group(1)!;
        if (!names.contains(name) || _sel.contains(name)) continue;
        if (negated.hasMatch(line.substring(0, m.start))) continue;
        out.add(name);
      }
    }
    return out;
  }

  int get _total => widget.catalog
      .where((t) => _sel.contains(t.name))
      .fold(0, (a, t) => a + t.estTokens);

  List<ToolInfo> get _visible {
    final q = _query.toLowerCase();
    return widget.catalog.where((t) {
      if (_filter == 'selected' && !_sel.contains(t.name)) return false;
      if (_filter != 'all' && _filter != 'selected' && t.sideEffect != _filter) {
        return false;
      }
      if (q.isEmpty) return true;
      return t.name.toLowerCase().contains(q) ||
          t.summary.toLowerCase().contains(q);
    }).toList();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final suggestions = _suggested;
    final heavy = _total > _threshold;

    return Dialog(
      insetPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 24),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 820, maxHeight: 720),
        child: Column(children: [
          // ---- header
          Padding(
            padding: const EdgeInsets.fromLTRB(24, 20, 16, 8),
            child: Row(children: [
              Expanded(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Tools',
                          style: Theme.of(context).textTheme.headlineSmall),
                      Text(
                          '${widget.catalog.length} in catalog · bound only while '
                          'this skill is retrieved',
                          style: TextStyle(
                              fontSize: 12, color: cs.onSurfaceVariant)),
                    ]),
              ),
              _totalChip(cs, heavy),
              IconButton(
                  tooltip: 'Close tool picker',
                  onPressed: () => Navigator.pop(context),
                  icon: const Icon(Icons.close)),
            ]),
          ),

          if (heavy)
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 0, 24, 8),
              child: Card.filled(
                color: cs.attentionContainer,
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Row(children: [
                    Icon(Icons.trending_up, size: 18, color: cs.onAttentionContainer),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                          'Over ~$_threshold tokens per turn, re-billed on every '
                          'turn this skill is active. Consider splitting the skill.',
                          style: TextStyle(
                              fontSize: 12, color: cs.onAttentionContainer)),
                    ),
                  ]),
                ),
              ),
            ),

          // ---- suggestions
          if (suggestions.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 0, 24, 8),
              child: Card.filled(
                color: cs.secondaryContainer,
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(children: [
                          Icon(Icons.lightbulb_outline,
                              size: 18, color: cs.onSecondaryContainer),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                                'Your instructions tell the agent to call these, '
                                'but they are not bound — it would be told to use '
                                'a tool it lacks.',
                                style: TextStyle(
                                    fontSize: 12,
                                    color: cs.onSecondaryContainer)),
                          ),
                        ]),
                        const SizedBox(height: 8),
                        Wrap(
                          spacing: 8,
                          children: suggestions
                              .map((n) => ActionChip(
                                    avatar: const Icon(Icons.add, size: 16),
                                    label: Text(n,
                                        style: const TextStyle(fontSize: 12)),
                                    onPressed: () =>
                                        setState(() => _sel.add(n)),
                                  ))
                              .toList(),
                        ),
                      ]),
                ),
              ),
            ),

          // ---- search + filters
          Padding(
            padding: const EdgeInsets.fromLTRB(24, 8, 24, 8),
            child: Column(children: [
              SearchBar(
                hintText: 'Search tools',
                leading: const Icon(Icons.search),
                onChanged: (v) => setState(() => _query = v),
                elevation: const WidgetStatePropertyAll(0),
              ),
              const SizedBox(height: 12),
              Align(
                alignment: Alignment.centerLeft,
                child: Wrap(spacing: 8, children: [
                  for (final f in const [
                    ['all', 'All'],
                    ['selected', 'Selected'],
                    ['read', 'Read'],
                    ['write', 'Write'],
                    ['auth', 'Auth'],
                    ['render', 'Render'],
                  ])
                    FilterChip(
                      label: Text(f[1]),
                      selected: _filter == f[0],
                      onSelected: (_) => setState(() => _filter = f[0]),
                    ),
                ]),
              ),
            ]),
          ),
          const Divider(height: 1),

          // ---- list
          Expanded(
            child: _visible.isEmpty
                ? const Center(child: Text('No matching tools'))
                : ListView.separated(
                    itemCount: _visible.length,
                    separatorBuilder: (_, _) => const Divider(height: 1),
                    itemBuilder: (c, i) => _row(_visible[i], cs),
                  ),
          ),

          // ---- actions
          const Divider(height: 1),
          Padding(
            padding: const EdgeInsets.all(16),
            child: Wrap(spacing: 8, runSpacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
              Text('${_sel.length} selected',
                  style: TextStyle(color: cs.onSurfaceVariant)),

              TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text('Cancel')),
              const SizedBox(width: 8),
              FilledButton(
                onPressed: () => Navigator.pop(context, _sel.toList()..sort()),
                child: const Text('Apply'),
              ),
            ]),
          ),
        ]),
      ),
    );
  }

  Widget _totalChip(ColorScheme cs, bool heavy) => Container(
        margin: const EdgeInsets.only(right: 8),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        decoration: BoxDecoration(
          color: heavy ? cs.attentionContainer : cs.surfaceContainerHigh,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(
              color: heavy ? cs.attention.withValues(alpha: 0.4) : cs.outlineVariant),
        ),
        child: Text('~$_total tok/turn',
            style: TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w600,
                color: heavy ? cs.onAttentionContainer : cs.onSurfaceVariant)),
      );

  Widget _row(ToolInfo t, ColorScheme cs) {
    final on = _sel.contains(t.name);
    final req = t.params.where((p) => p.required).length;

    return CheckboxListTile(
      value: on,
      onChanged: (v) => setState(() {
        if (v == true) {
          _sel.add(t.name);
        } else {
          _sel.remove(t.name);
        }
      }),
      controlAffinity: ListTileControlAffinity.leading,
      isThreeLine: t.summary.isNotEmpty,
      title: Text(t.name,
        style: const TextStyle(fontFamily: 'monospace', fontSize: 13,
          fontWeight: FontWeight.w600)),
      subtitle: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Wrap(spacing: 8, runSpacing: 4, crossAxisAlignment: WrapCrossAlignment.center, children: [
          Icon(iconForClass(t.sideEffect), size: 16, color: cs.onSurfaceVariant),
          _classChip(t, cs),
          if (t.requiresAuth) Tooltip(message: 'May require authentication',
            child: Icon(Icons.lock_outline, size: 16, color: cs.attention)),
          Text('~${t.estTokens}', style: TextStyle(fontSize: 11,
            color: t.estTokens > 800 ? cs.attention : cs.onSurfaceVariant)),
        ]),
        if (t.summary.isNotEmpty)
          Text(t.summary,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 12)),
        if (t.params.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Text(
                '${t.params.length} param${t.params.length == 1 ? '' : 's'}'
                '${req > 0 ? ' · $req required' : ''}'
                ' · ${t.params.map((p) => p.name).take(4).join(', ')}',
                style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
          ),
      ]),
    );
  }

  /// Class is inferred from the tool name, so the chip says so on hover rather
  /// than presenting a guess as fact.
  Widget _classChip(ToolInfo t, ColorScheme cs) {
    // A write or auth tool needs attention; it is not a failure. Red stays
    // reserved for brand actions and genuine errors (see AdklTheme).
    final (bg, fg) = switch (t.sideEffect) {
      'write' => (cs.attentionContainer, cs.onAttentionContainer),
      'auth' => (cs.attentionContainer, cs.onAttentionContainer),
      'render' => (cs.surfaceContainerHigh, cs.onSurfaceVariant),
      'read' => (cs.secondaryContainer, cs.onSecondaryContainer),
      _ => (cs.surfaceContainerHigh, cs.onSurfaceVariant),
    };
    return Tooltip(
      message: t.sideEffectInferred
          ? 'Inferred from the tool name — verify before relying on it'
          : 'Declared',
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
        decoration:
            BoxDecoration(color: bg, borderRadius: BorderRadius.circular(6)),
        child: Text(
            t.sideEffect + (t.sideEffectInferred ? '?' : ''),
            style: TextStyle(fontSize: 10, color: fg, fontWeight: FontWeight.w600)),
      ),
    );
  }
}
