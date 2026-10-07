import 'package:flutter/material.dart';

import 'api.dart';
import 'theme.dart';

/// Structured editor for a skill body: one field per thing, no raw markdown.
///
/// Real skills are workflows containing ordered steps, plus labelled gotchas,
/// plus prose sections. Exposing that as a single markdown textarea asks a
/// business user to know both markdown and the house conventions, and makes
/// every save one whole-body diff.
///
/// Edits mutate the [SkillBody] in place and the whole doc is posted back. The
/// server diffs it against the on-disk parse, so a section nobody touched is
/// written byte-identical -- verified end to end: a step edit here produces a
/// one-line diff on disk.
///
/// Step numbers are deliberately *not* editable. They are positional, derived
/// from order at render time, so inserting a step renumbers the rest for free.
class SectionEditor extends StatefulWidget {
  const SectionEditor({
    super.key,
    required this.doc,
    required this.onChanged,
    required this.toolNames,
    this.readOnly = false,
  });

  final SkillBody doc;
  final bool readOnly;
  final VoidCallback onChanged;

  /// Catalog tool names, used to render `\`tool\`` mentions as chips and to flag
  /// a mention of something that is not a real tool.
  final Set<String> toolNames;

  @override
  State<SectionEditor> createState() => _SectionEditorState();
}

class _SectionEditorState extends State<SectionEditor> {
  // Keyed by identity path so controllers survive rebuilds without being
  // recreated (which would drop the cursor position on every keystroke).
  final _controllers = <String, TextEditingController>{};
  final _expanded = <String>{};

  @override
  void dispose() {
    for (final c in _controllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  TextEditingController _ctrl(String key, String initial) {
    final existing = _controllers[key];
    if (existing != null) return existing;
    return _controllers[key] = TextEditingController(text: initial);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    if (widget.doc.sections.isEmpty) {
      return _empty(cs);
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (var i = 0; i < widget.doc.sections.length; i++) ...[
          _section(cs, widget.doc.sections[i], i),
          const SizedBox(height: 22),
        ],
      ],
    );
  }

  Widget _empty(ColorScheme cs) => Container(
    padding: const EdgeInsets.all(14),
    decoration: BoxDecoration(
      color: cs.surfaceContainerLow,
      borderRadius: BorderRadius.circular(AdklTheme.rMd),
      border: Border.all(color: cs.outlineVariant),
    ),
    child: Text(
      'No structured sections found. This skill body does not match the '
      'expected shape (## Workflows / ## Gotchas), so it can only be '
      'edited as text.',
      style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
    ),
  );

  // ------------------------------------------------------------------ section

  Widget _section(ColorScheme cs, DocSection s, int idx) {
    final subtitle = switch (s.kind) {
      'workflows' =>
        '${s.workflows.length} workflow'
            '${s.workflows.length == 1 ? '' : 's'}, '
            '${s.workflows.fold(0, (a, w) => a + w.steps.length)} steps',
      'gotchas' => '${s.items.length} item${s.items.length == 1 ? '' : 's'}',
      _ => 'free text',
    };

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 6,
          runSpacing: 6,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Icon(_iconFor(s.kind), size: 14, color: cs.onSurfaceVariant),
            const SizedBox(width: 7),
            Text(
              s.title.toUpperCase(),
              style: TextStyle(
                fontSize: 10.5,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.9,
                color: cs.onSurfaceVariant,
              ),
            ),
            const SizedBox(width: 8),
            Text(
              subtitle,
              style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant),
            ),

            if (!widget.readOnly && s.kind == 'workflows')
              TextButton.icon(
                onPressed: () => setState(() {
                  s.workflows.add(
                    DocWorkflow(
                      name: 'New workflow',
                      when: '',
                      steps: [DocStep(text: '', done: false)],
                    ),
                  );
                  widget.onChanged();
                }),
                icon: const Icon(Icons.add, size: 14),
                label: const Text('Workflow'),
              ),
            if (!widget.readOnly && s.kind == 'gotchas')
              TextButton.icon(
                onPressed: () => setState(() {
                  s.items.add(GotchaItem(label: '', text: ''));
                  widget.onChanged();
                }),
                icon: const Icon(Icons.add, size: 14),
                label: const Text('Gotcha'),
              ),
          ],
        ),
        const SizedBox(height: 8),
        switch (s.kind) {
          'workflows' => _workflows(cs, s, idx),
          'gotchas' => _gotchas(cs, s, idx),
          _ => _prose(cs, s, idx),
        },
      ],
    );
  }

  IconData _iconFor(String kind) => switch (kind) {
    'workflows' => Icons.account_tree_outlined,
    'gotchas' => Icons.report_problem_outlined,
    _ => Icons.notes_outlined,
  };

  // ---------------------------------------------------------------- workflows

  Widget _workflows(ColorScheme cs, DocSection s, int si) => Column(
    children: [
      for (var wi = 0; wi < s.workflows.length; wi++) _workflow(cs, s, si, wi),
    ],
  );

  Widget _workflow(ColorScheme cs, DocSection s, int si, int wi) {
    final w = s.workflows[wi];
    final key = 's$si.w$wi';
    final open = _expanded.contains(key) || s.workflows.length == 1;

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      decoration: BoxDecoration(
        color: cs.surface,
        borderRadius: BorderRadius.circular(AdklTheme.rLg),
        border: Border.all(color: cs.outlineVariant),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // header: name is the intent, and it is what retrieval keys on
          InkWell(
            onTap: () => setState(
              () => open ? _expanded.remove(key) : _expanded.add(key),
            ),
            borderRadius: BorderRadius.vertical(
              top: Radius.circular(AdklTheme.rLg),
            ),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 10, 8, 10),
              child: Row(
                children: [
                  Icon(
                    open ? Icons.expand_less : Icons.expand_more,
                    size: 16,
                    color: cs.onSurfaceVariant,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      w.name.isEmpty ? '(unnamed workflow)' : w.name,
                      maxLines: open ? null : 1,
                      overflow: open ? null : TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w700,
                        fontStyle: w.name.isEmpty
                            ? FontStyle.italic
                            : FontStyle.normal,
                        color: cs.onSurface,
                      ),
                    ),
                  ),
                  Text(
                    '${w.steps.length} steps',
                    style: TextStyle(
                      fontSize: 10.5,
                      color: cs.onSurfaceVariant,
                    ),
                  ),
                  if (!widget.readOnly)
                    IconButton(
                      tooltip: 'Delete workflow ${w.name}',
                      onPressed: () => setState(() {
                        s.workflows.removeAt(wi);
                        widget.onChanged();
                      }),
                      icon: const Icon(Icons.delete_outline, size: 15),
                      constraints: const BoxConstraints(
                        minWidth: 48,
                        minHeight: 48,
                      ),
                    ),
                ],
              ),
            ),
          ),
          if (open) ...[
            Divider(height: 1, color: cs.outlineVariant),
            Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _label(cs, 'Name'),
                  _field(
                    cs,
                    '$key.name',
                    w.name,
                    (v) {
                      w.name = v;
                      widget.onChanged();
                    },
                    label: 'Workflow name',
                    hint: 'e.g. Explain a concept',
                  ),
                  const SizedBox(height: 12),
                  _label(cs, 'When to use'),
                  _field(
                    cs,
                    '$key.when',
                    w.when,
                    (v) {
                      w.when = v;
                      widget.onChanged();
                    },
                    label: 'When to use ${w.name}',
                    hint: 'Use when the user…',
                    maxLines: 3,
                  ),
                  const SizedBox(height: 14),
                  _label(cs, 'Steps'),
                  for (var i = 0; i < w.steps.length; i++) _step(cs, w, key, i),
                  const SizedBox(height: 6),
                  if (!widget.readOnly)
                    Align(
                      alignment: Alignment.centerLeft,
                      child: TextButton.icon(
                        onPressed: () => setState(() {
                          w.steps.add(DocStep(text: '', done: false));
                          widget.onChanged();
                        }),
                        icon: const Icon(Icons.add, size: 14),
                        label: const Text('Step'),
                      ),
                    ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _step(ColorScheme cs, DocWorkflow w, String wkey, int i) {
    final st = w.steps[i];
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Number is derived from position, never edited. Insert a step and the
          // rest renumber for free.
          Container(
            margin: const EdgeInsets.only(top: 9),
            width: 20,
            height: 20,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: cs.surfaceContainerHigh,
              shape: BoxShape.circle,
            ),
            child: Text(
              '${i + 1}',
              style: TextStyle(
                fontSize: 10,
                fontWeight: FontWeight.w700,
                color: cs.onSurfaceVariant,
              ),
            ),
          ),
          const SizedBox(width: 9),
          Expanded(
            child: _field(
              cs,
              '$wkey.s$i',
              st.text,
              (v) {
                st.text = v;
                widget.onChanged();
              },
              label: '${w.name}, step ${i + 1}',
              hint: 'Call `tool_name` and…',
              maxLines: 6,
              mono: true,
            ),
          ),
          if (!widget.readOnly)
            Column(
              children: [
                IconButton(
                  tooltip: 'Move ${w.name}, step ${i + 1} up',
                  onPressed: i == 0
                      ? null
                      : () => setState(() {
                          final s = w.steps.removeAt(i);
                          w.steps.insert(i - 1, s);
                          _dropStepControllers(wkey, w.steps.length);
                          widget.onChanged();
                        }),
                  icon: const Icon(Icons.arrow_upward, size: 13),
                  constraints: const BoxConstraints(
                    minWidth: 48,
                    minHeight: 48,
                  ),
                ),
                IconButton(
                  tooltip: 'Delete ${w.name}, step ${i + 1}',
                  onPressed: () => setState(() {
                    w.steps.removeAt(i);
                    _dropStepControllers(wkey, w.steps.length);
                    widget.onChanged();
                  }),
                  icon: const Icon(Icons.close, size: 13),
                  constraints: const BoxConstraints(
                    minWidth: 48,
                    minHeight: 48,
                  ),
                ),
              ],
            ),
        ],
      ),
    );
  }

  /// Reordering changes which text belongs to which index, so the cached
  /// controllers for this workflow's steps must go or the fields show stale text.
  void _dropStepControllers(String wkey, int count) {
    _controllers.keys
        .where((k) => k.startsWith('$wkey.s'))
        .toList()
        .forEach((k) => _controllers.remove(k)?.dispose());
  }

  // ------------------------------------------------------------------ gotchas

  Widget _gotchas(ColorScheme cs, DocSection s, int si) => Container(
    decoration: BoxDecoration(
      color: cs.surface,
      borderRadius: BorderRadius.circular(AdklTheme.rLg),
      border: Border.all(color: cs.outlineVariant),
    ),
    child: Column(
      children: [
        for (var i = 0; i < s.items.length; i++) ...[
          if (i > 0) Divider(height: 1, color: cs.outlineVariant),
          Padding(
            padding: const EdgeInsets.all(11),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  flex: 2,
                  child: _field(
                    cs,
                    's$si.g$i.label',
                    s.items[i].label,
                    (v) {
                      s.items[i].label = v;
                      widget.onChanged();
                    },
                    label: '${s.title}, item ${i + 1} label',
                    hint: 'Label',
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  flex: 5,
                  child: _field(
                    cs,
                    's$si.g$i.text',
                    s.items[i].text,
                    (v) {
                      s.items[i].text = v;
                      widget.onChanged();
                    },
                    label: '${s.title}, item ${i + 1} details',
                    hint: 'What to watch out for',
                    maxLines: 4,
                  ),
                ),
                if (!widget.readOnly)
                  IconButton(
                    tooltip: 'Delete ${s.title}, item ${i + 1}',
                    onPressed: () => setState(() {
                      s.items.removeAt(i);
                      _controllers.keys
                          .where((k) => k.startsWith('s$si.g'))
                          .toList()
                          .forEach((k) => _controllers.remove(k)?.dispose());
                      widget.onChanged();
                    }),
                    icon: const Icon(Icons.close, size: 13),
                    constraints: const BoxConstraints(
                      minWidth: 48,
                      minHeight: 48,
                    ),
                  ),
              ],
            ),
          ),
        ],
      ],
    ),
  );

  // -------------------------------------------------------------------- prose

  Widget _prose(ColorScheme cs, DocSection s, int si) => _field(
    cs,
    's$si.prose',
    s.prose,
    (v) {
      s.prose = v;
      widget.onChanged();
    },
    label: '${s.title} instructions',
    hint: 'Free text',
    maxLines: 14,
    mono: true,
  );

  // ------------------------------------------------------------------ shared

  Widget _label(ColorScheme cs, String text) => Padding(
    padding: const EdgeInsets.only(bottom: 5),
    child: Text(
      text.toUpperCase(),
      style: TextStyle(
        fontSize: 9.5,
        fontWeight: FontWeight.w700,
        letterSpacing: 0.8,
        color: cs.onSurfaceVariant,
      ),
    ),
  );

  Widget _field(
    ColorScheme cs,
    String key,
    String value,
    ValueChanged<String> onChanged, {
    String? hint,
    required String label,
    int maxLines = 1,
    bool mono = false,
  }) {
    final c = _ctrl(key, value);
    // Keep the controller in sync when the doc is replaced under us (e.g. after
    // a reload) without clobbering an in-progress edit.
    if (c.text != value && !c.selection.isValid) c.text = value;

    return TextField(
      controller: c,
      readOnly: widget.readOnly,
      onChanged: widget.readOnly ? null : onChanged,
      maxLines: maxLines == 1 ? 1 : null,
      minLines: maxLines == 1 ? 1 : 2,
      style: TextStyle(
        fontSize: mono ? AdklTheme.fsMono : AdklTheme.fsBody,
        height: 1.5,
        fontFamily: mono ? AdklTheme.mono : null,
      ),
      decoration: InputDecoration(
        labelText: label,
        hintText: hint,
        isDense: true,
        contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
      ),
    );
  }
}
