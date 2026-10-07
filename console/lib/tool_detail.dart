import 'package:flutter/material.dart';

import 'api.dart';
import 'theme.dart';

/// Read-only inspector for one tool from the generated catalog.
///
/// This is the "review each tool" surface. It is deliberately read-only: the
/// catalog is generated from Python source by AST, so the tool's real definition
/// lives in code. Editing it here would create a second source of truth — the
/// exact drift the text-on-disk rule exists to prevent.
///
/// It also names which skills bind the tool, because that is the question you
/// actually have when auditing: *who can call this?*
class ToolDetail extends StatelessWidget {
  const ToolDetail({
    super.key,
    required this.tool,
    required this.skills,
    required this.onOpenSkill,
  });

  final ToolInfo tool;
  final List<SkillSummary> skills;
  final ValueChanged<String> onOpenSkill;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final users = skills.where((s) => s.tools.contains(tool.name)).toList();
    final required = tool.params.where((p) => p.required).toList();

    return Column(children: [
      // header
      Container(
        padding: const EdgeInsets.fromLTRB(28, 18, 24, 16),
        decoration: BoxDecoration(
          border: Border(bottom: BorderSide(color: cs.outlineVariant)),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(tool.name, style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 8),
          Wrap(spacing: 6, runSpacing: 6, children: [
            _badge(cs, label: '~${tool.estTokens} tok/turn',
              tip: 'Estimated input tokens for the tool declaration when bound.'),
            _badge(cs,
                label: tool.sideEffect == 'unknown' ? 'Effect not declared' : tool.sideEffect +
                    (tool.sideEffectInferred ? ' (inferred)' : ''),
                tone: (tool.sideEffect == 'write' || tool.sideEffect == 'auth')
                    ? cs.attention
                    : null,
                tip: tool.sideEffectInferred
                    ? 'Inferred from the tool name, not declared. Verify before '
                        'relying on it for a safety decision.'
                    : tool.sideEffect == 'unknown' ? 'The host has not declared this tool’s effects.' : 'Declared in the catalog.'),
            if (tool.requiresAuth)
              _badge(cs,
                  label: 'may need auth',
                  tone: cs.attention,
                  icon: Icons.lock_outline,
                  tip: 'The docstring mentions identity or credentials '
                      '(inferred). Gate this behind authentication in the '
                      'skills that bind it.'),
            _badge(cs,
                label: !tool.schemaKnown ? 'Schema unavailable' : '${tool.params.length} params'
                    '${required.isEmpty ? '' : ' · ${required.length} required'}'),
          ]),
        ]),
      ),

      Expanded(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
          children: [
            _section(cs, 'SUMMARY'),
            if (tool.summary.isEmpty)
              _note(cs,
                  'No docstring. ADK sends the docstring to the model as the '
                  'tool description, so the agent has nothing to go on — this '
                  'is worth fixing in the Python source.',
                  tone: cs.attention)
            else ...[
              SelectableText(tool.summary.length > 400 ? tool.summary.split('\n\n').first : tool.summary,
                style: TextStyle(fontSize: AdklTheme.fsBody, height: 1.6, color: cs.onSurface)),
              if (tool.summary.length > 400) ExpansionTile(
                title: const Text('Full tool documentation'),
                children: [Padding(padding: const EdgeInsets.all(12), child: SelectableText(tool.summary))]),
            ],

            const SizedBox(height: 26),
            _section(cs, 'PARAMETERS'),
            if (!tool.schemaKnown)
              _note(cs, 'Parameter schema was not available from this declaration.')
            else if (tool.params.isEmpty)
              _note(cs, 'Takes no parameters.')
            else
              Card.outlined(
                child: Column(children: [
                  for (var i = 0; i < tool.params.length; i++) ...[
                    if (i > 0) Divider(height: 1, color: cs.outlineVariant),
                    _param(cs, tool.params[i]),
                  ],
                ]),
              ),

            const SizedBox(height: 26),
            _section(cs, 'BOUND BY',
                hint: users.isEmpty
                    ? null
                    : 'Declared skill links; the host may also bind tools directly.'),
            if (tool.source == 'native_factory') _note(cs, 'Native scope: ${tool.scope}'),
            if (users.isEmpty)
              _note(cs,
                  'No skill in this agent declares this tool. The host may bind '
                  'or call it directly. Check registration and callers before '
                  'removing it; this does not establish that it is unused.')
            else
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: users
                    .map((s) => ActionChip(
                          avatar: Icon(
                              s.errors > 0
                                  ? Icons.error_outline
                                  : s.warnings > 0
                                      ? Icons.warning_amber_rounded
                                      : Icons.description_outlined,
                              size: 13,
                              color: s.errors > 0
                                  ? cs.failure
                                  : s.warnings > 0
                                      ? cs.attention
                                      : cs.onSurfaceVariant),
                          label: Text(s.name),
                          onPressed: () => onOpenSkill(s.name),
                        ))
                    .toList(),
              ),

            const SizedBox(height: 26),
            _section(cs, 'SOURCE'),
            _note(cs, tool.source == 'native_factory'
                ? 'Declared by the native host factory. Edit the tool in its host repository.'
                : 'Defined in Python and generated into the catalog by AST. Edit '
                'the source to change it — the catalog is a build artifact, not '
                'a place to author.'),
          ],
        ),
      ),
    ]);
  }

  Widget _param(ColorScheme cs, ParamInfo p) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Wrap(spacing: 8, runSpacing: 4, children: [
            Text(p.name,
                style: TextStyle(
                    fontFamily: AdklTheme.mono,
                    fontSize: AdklTheme.fsMono,
                    fontWeight: FontWeight.w600,
                    color: cs.onSurface)),
            const SizedBox(width: 8),
            if (p.type.isNotEmpty)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                decoration: BoxDecoration(
                  color: cs.surfaceContainerHigh,
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(p.type,
                    style: TextStyle(
                        fontFamily: AdklTheme.mono,
                        fontSize: 10.5,
                        color: cs.onSurfaceVariant)),
              ),
            Text(p.required ? 'required' : 'optional',
                style: TextStyle(
                    fontSize: 10,
                    fontWeight: p.required ? FontWeight.w700 : FontWeight.w400,
                    letterSpacing: 0.3,
                    color: p.required ? cs.onSurface : cs.onSurfaceVariant)),
          ]),
          if (p.description.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text(p.description,
                style: TextStyle(
                    fontSize: 11.5, height: 1.5, color: cs.onSurfaceVariant)),
          ] else ...[
            const SizedBox(height: 5),
            Text('undocumented — the model sees no description for this param',
                style: TextStyle(
                    fontSize: 11,
                    fontStyle: FontStyle.italic,
                    color: cs.attention)),
          ],
        ]),
      );

  Widget _section(ColorScheme cs, String label, {String? hint}) => Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label,
              style: TextStyle(
                  fontSize: 10.5,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.9,
                  color: cs.onSurfaceVariant)),
          if (hint != null) ...[
            const SizedBox(height: 3),
            Text(hint,
                style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
          ],
        ]),
      );

  Widget _note(ColorScheme cs, String text, {Color? tone}) => Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: tone == null
              ? cs.surfaceContainerLow
              : cs.attentionContainer.withValues(alpha: 0.5),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(
              color: tone == null
                  ? cs.outlineVariant
                  : cs.attention.withValues(alpha: 0.35)),
        ),
        child: Text(text,
            style: TextStyle(
                fontSize: 11.5,
                height: 1.55,
                color: tone == null ? cs.onSurfaceVariant : cs.onSurface)),
      );

  Widget _badge(ColorScheme cs,
      {required String label, Color? tone, IconData? icon, String? tip}) {
    final w = Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 4),
      decoration: BoxDecoration(
        color: tone == null
            ? cs.surfaceContainerHigh
            : cs.attentionContainer.withValues(alpha: 0.55),
        borderRadius: BorderRadius.circular(5),
        border: Border.all(
            color: tone == null
                ? cs.outlineVariant
                : cs.attention.withValues(alpha: 0.35)),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        if (icon != null) ...[
          Icon(icon, size: 11, color: tone ?? cs.onSurfaceVariant),
          const SizedBox(width: 4),
        ],
        Flexible(child: Text(label,
            style: TextStyle(
                fontSize: 10.5,
                fontWeight: FontWeight.w600,
                color: tone == null ? cs.onSurfaceVariant : cs.onSurface))),
      ]),
    );
    return tip == null ? w : Tooltip(message: tip, child: w);
  }
}
