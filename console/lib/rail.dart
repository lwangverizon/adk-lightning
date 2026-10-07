import 'package:flutter/material.dart';

import 'api.dart';
import 'diagnostics.dart';
import 'theme.dart';

/// Left navigation rail — Skills and Tools as sibling collections.
///
/// Sierra's shell is a single left rail at 19.5–20.0% of viewport plus a content
/// pane (measured across three product screenshots; there is no second divider
/// left of 400px, so this is not a three-column app). Their rail lists
/// collections — Journeys, Tools, Knowledge, Personas, Glossary — so Tools
/// belonging beside Skills matches the source design, not just convenience.
///
/// Each row carries its own status: token cost and finding count for skills,
/// side-effect class and cost for tools. The point is that the rail is scannable
/// on its own — you can see which skill is expensive or broken without opening
/// anything.
class NavRail extends StatelessWidget {
  const NavRail({
    super.key,
    required this.skills,
    required this.tools,
    this.workflows = const [],
    this.selectedWorkflow,
    this.onWorkflow,
    required this.tab,
    required this.onTab,
    required this.selectedSkill,
    required this.selectedTool,
    required this.onSkill,
    required this.onTool,
    required this.query,
    required this.onQuery,
    required this.env,
    this.findings = const [],
    this.needsAttention = false,
    this.onNeedsAttention,
    this.findingsStale = false,
    this.scopeLabel = 'Workspace lint preview · not runtime verification',
    this.authoritativeFindings = false,
  });

  final List<SkillSummary> skills;
  final List<ToolInfo> tools;
  final List<NativeWorkflow> workflows;
  final String? selectedWorkflow;
  final ValueChanged<String>? onWorkflow;
  final int tab; // 0 = skills, 1 = tools, 2 = native workflows
  final ValueChanged<int> onTab;
  final String? selectedSkill;
  final String? selectedTool;
  final ValueChanged<String> onSkill;
  final ValueChanged<String> onTool;
  final String query;
  final ValueChanged<String> onQuery;
  final String? env;
  final List<Finding> findings;
  final bool needsAttention;
  final ValueChanged<bool>? onNeedsAttention;
  final bool findingsStale;
  final String scopeLabel;
  final bool authoritativeFindings;

  List<Finding> _issues(String name, {bool tool = false}) => findings
      .where((f) => f.skill == name && f.targetsTool == tool).toList();

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final q = query.toLowerCase();

    final visSkills = skills
        .where((s) => (!needsAttention || _issues(s.name).isNotEmpty) &&
            (q.isEmpty || s.name.toLowerCase().contains(q)))
        .toList();
    final visTools = tools
        .where((t) =>
            (!needsAttention || _issues(t.name, tool: true).isNotEmpty) &&
            (q.isEmpty ||
            t.name.toLowerCase().contains(q) ||
            t.summary.toLowerCase().contains(q)))
        .toList();

    final visWorkflows = workflows.where((w) => q.isEmpty ||
        w.name.toLowerCase().contains(q) ||
        w.description.toLowerCase().contains(q)).toList();

    return Container(
      decoration: BoxDecoration(
        color: cs.surfaceContainerLow,
        border: Border(right: BorderSide(color: cs.outlineVariant)),
      ),
      child: Column(children: [
        _tabs(cs),
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
          child: TextField(
            onChanged: onQuery,
            style: const TextStyle(fontSize: AdklTheme.fsBody),
            decoration: InputDecoration(
              hintText: tab == 0 ? 'Search skills' : tab == 1 ? 'Search tools' : 'Search workflows',
              prefixIcon: Icon(Icons.search, size: 16, color: cs.onSurfaceVariant),
              prefixIconConstraints:
                  const BoxConstraints(minWidth: 34, minHeight: 34),
              contentPadding:
                  const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
              fillColor: cs.surface,
            ),
          ),
        ),
        if (onNeedsAttention != null && tab != 2)
          FilterChip(label: const Text('Needs attention'),
            selected: needsAttention, onSelected: onNeedsAttention),
        Padding(padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
          child: Text(findingsStale
              ? 'Edits pending · findings may be outdated'
              : scopeLabel,
            style: TextStyle(fontSize: 10, color: cs.onSurfaceVariant))),
        Expanded(
          child: tab == 0
              ? _skillList(cs, visSkills)
              : tab == 1 ? _toolList(cs, visTools)
              : _workflowList(cs, visWorkflows),
        ),
      ]),
    );
  }

  Widget _tabs(ColorScheme cs) => Container(
        constraints: const BoxConstraints(minHeight: 48),
        decoration: BoxDecoration(
          border: Border(bottom: BorderSide(color: cs.outlineVariant)),
        ),
        child: Row(children: [
          _tab(cs, 0, 'Skills', skills.length),
          _tab(cs, 1, 'Tools', tools.length),
          if (onWorkflow != null) _tab(cs, 2, 'Workflows', workflows.length),
        ]),
      );

  Widget _tab(ColorScheme cs, int i, String label, int count) {
    final on = tab == i;
    return Expanded(
      child: Semantics(selected: on, child: InkWell(
        onTap: () => onTab(i),
        child: Container(
          decoration: BoxDecoration(
            border: Border(
              bottom: BorderSide(
                  color: on ? cs.onSurface : Colors.transparent, width: 2),
            ),
          ),
          alignment: Alignment.center,
          child: Wrap(alignment: WrapAlignment.center, crossAxisAlignment: WrapCrossAlignment.center, children: [
            Text(label,
                style: TextStyle(
                    fontSize: 12,
                    fontWeight: on ? FontWeight.w700 : FontWeight.w500,
                    color: on ? cs.onSurface : cs.onSurfaceVariant)),
            const SizedBox(width: 6),
            Text('$count',
                style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
          ]),
        ),
      )),
    );
  }

  Widget _workflowList(ColorScheme cs, List<NativeWorkflow> items) {
    if (items.isEmpty) return _empty(cs, 'No matching workflows');
    return ListView.builder(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      itemCount: items.length,
      itemBuilder: (context, index) {
        final workflow = items[index];
        return _row(cs, selected: '${workflow.agent}:${workflow.name}' == selectedWorkflow,
          onTap: () => onWorkflow?.call('${workflow.agent}:${workflow.name}'), title: workflow.name,
          mono: false,
          leading: Icon(Icons.account_tree_outlined, size: 14,
            color: cs.onSurfaceVariant),
          subtitle: Text('${workflow.agent} · ${workflow.description}', maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)));
      },
    );
  }

  // ---------------------------------------------------------------- skills

  Widget _skillList(ColorScheme cs, List<SkillSummary> items) {
    if (items.isEmpty) return _empty(cs, 'No matching skills');
    return ListView.builder(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      itemCount: items.length,
      itemBuilder: (c, i) {
        final s = items[i];
        final sel = s.name == selectedSkill;
        final issues = _issues(s.name);
        final errors = authoritativeFindings ? issues.where((f) => f.isError).length : s.errors;
        final warnings = authoritativeFindings ? issues.where((f) => f.severity == 'WARN').length : s.warnings;
        final broken = errors > 0;
        final warn = warnings > 0;

        return _row(
          cs,
          selected: sel,
          onTap: () => onSkill(s.name),
          title: s.name,
          mono: false,
          leading: Icon(
            broken
                ? Icons.error_outline
                : warn
                    ? Icons.warning_amber_rounded
                    : Icons.description_outlined,
            size: 14,
            color: broken
                ? cs.failure
                : warn
                    ? cs.attention
                    : cs.onSurfaceVariant,
          ),
          subtitle: Wrap(crossAxisAlignment: WrapCrossAlignment.center, children: [
            Text('${s.tools.length} tools',
                style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
            Text(' · ', style: TextStyle(fontSize: 10.5, color: cs.outline)),
            Text('~${s.estTokens}',
                style: TextStyle(
                    fontSize: 10.5,
                    color: s.estTokens > 3000
                        ? cs.attention
                        : cs.onSurfaceVariant,
                    fontWeight: s.estTokens > 3000
                        ? FontWeight.w600
                        : FontWeight.w400)),
            if (s.overridden && env != null) ...[
              Text(' · ', style: TextStyle(fontSize: 10.5, color: cs.outline)),
              Text(env!,
                  style: TextStyle(
                      fontSize: 10.5,
                      color: cs.info,
                      fontWeight: FontWeight.w600)),
            ],
          ]),
          badges: FindingBadges(findings: _issues(s.name)),
          trailing: (errors + warnings) == 0
              ? null
              : Text('${errors + warnings}',
                  style: TextStyle(
                      fontSize: 10.5,
                      fontWeight: FontWeight.w700,
                      color: broken ? cs.failure : cs.attention)),
        );
      },
    );
  }

  // ----------------------------------------------------------------- tools

  /// Grouped by side-effect class. Sierra groups its rail with a bold header
  /// plus indented children, and grouping here also front-loads the safety
  /// signal: write and auth tools are the ones worth auditing.
  Widget _toolList(ColorScheme cs, List<ToolInfo> items) {
    if (items.isEmpty) return _empty(cs, 'No matching tools');

    const order = ['write', 'auth', 'read', 'render', 'unknown'];
    final groups = <String, List<ToolInfo>>{};
    for (final t in items) {
      groups.putIfAbsent(t.sideEffect, () => []).add(t);
    }
    final keys = order.where(groups.containsKey).toList()
      ..addAll(groups.keys.where((k) => !order.contains(k)));

    return ListView(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      children: [
        for (final k in keys) ...[
          Padding(
            padding: const EdgeInsets.fromLTRB(8, 12, 8, 6),
            child: Row(children: [
              Text(k.toUpperCase(),
                  style: TextStyle(
                      fontSize: 10,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 0.7,
                      color: _classColor(cs, k))),
              const SizedBox(width: 6),
              Text('${groups[k]!.length}',
                  style: TextStyle(fontSize: 10, color: cs.onSurfaceVariant)),
              if (k == 'write' || k == 'auth') ...[
                const SizedBox(width: 5),
                Tooltip(
                  message: 'Inferred from the tool name — verify before '
                      'relying on it for a safety decision',
                  child: Icon(Icons.help_outline,
                      size: 11, color: cs.onSurfaceVariant),
                ),
              ],
            ]),
          ),
          ...groups[k]!.map((t) {
            final sel = t.name == selectedTool;
            return _row(
              cs,
              selected: sel,
              onTap: () => onTool(t.name),
              title: t.name,
              mono: true,
              leading: Icon(iconForClass(t.sideEffect),
                  size: 13, color: _classColor(cs, t.sideEffect)),
              subtitle: Text(
                t.summary.isEmpty ? 'no docstring' : t.summary,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                    fontSize: 10.5,
                    color: cs.onSurfaceVariant,
                    fontStyle:
                        t.summary.isEmpty ? FontStyle.italic : FontStyle.normal),
              ),
              badges: FindingBadges(findings: _issues(t.name, tool: true)),
              trailing: Text('~${t.estTokens}',
                  style: TextStyle(
                      fontSize: 10,
                      color: t.estTokens > 800
                          ? cs.attention
                          : cs.onSurfaceVariant)),
            );
          }),
        ],
        const SizedBox(height: 16),
      ],
    );
  }

  // ----------------------------------------------------------------- shared

  Widget _row(
    ColorScheme cs, {
    required bool selected,
    required VoidCallback onTap,
    required String title,
    required bool mono,
    required Widget leading,
    required Widget subtitle,
    Widget? trailing,
    Widget? badges,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 2),
      child: Semantics(selected: selected, child: Material(
        color: selected ? cs.surfaceContainerHigh : Colors.transparent,
        borderRadius: BorderRadius.circular(7),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(7),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 10),
            child: Row(children: [
              SizedBox(width: 18, child: Center(child: leading)),
              const SizedBox(width: 6),
              Expanded(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(title,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            fontSize: 12,
                            height: 1.3,
                            fontFamily: mono ? AdklTheme.mono : null,
                            fontWeight:
                                selected ? FontWeight.w700 : FontWeight.w500,
                            color: cs.onSurface,
                          )),
                      const SizedBox(height: 1),
                      subtitle,
                      ?badges,
                    ]),
              ),
              if (trailing != null) ...[const SizedBox(width: 6), trailing],
            ]),
          ),
        ),
      )),
    );
  }

  Widget _empty(ColorScheme cs, String msg) => Center(
        child: Text(msg,
            style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
      );
}

Color _classColor(ColorScheme cs, String k) => switch (k) {
      'write' => cs.attention,
      'auth' => cs.attention,
      'read' => cs.onSurfaceVariant,
      'render' => cs.info,
      _ => cs.onSurfaceVariant,
    };

IconData iconForClass(String sideEffect) => switch (sideEffect) {
      'write' => Icons.edit_outlined,
      'read' => Icons.search,
      'render' => Icons.widgets_outlined,
      'auth' => Icons.lock_outline,
      _ => Icons.help_outline,
    };
