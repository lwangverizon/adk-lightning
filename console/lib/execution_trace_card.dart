import 'dart:convert';

import 'package:flutter/material.dart';

import 'theme.dart';

/// Read-only projection of observed events, never an execution graph or plan.
class ExecutionTraceCard extends StatelessWidget {
  const ExecutionTraceCard({super.key, this.execution, required this.toolCalls});

  final Map<String, dynamic>? execution;
  final List<Map<String, dynamic>> toolCalls;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final supported = execution?['schemaVersion'] == 1;
    final events = supported
        ? ((execution?['events'] as List?) ?? []).whereType<Map>().toList()
        : <Map>[];
    final groups = _groups(events);
    final selections = events.where((e) => e['type'] == 'retrieval').toList();
    final requests = execution?['preparedModelRequests'];
    final omitted = execution?['droppedEvents'] ?? 0;
    return Card(
      margin: const EdgeInsets.only(bottom: 14),
      elevation: 0,
      color: cs.surfaceContainerLow,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(
        side: BorderSide(color: cs.outlineVariant),
        borderRadius: BorderRadius.circular(12),
      ),
      clipBehavior: Clip.antiAlias,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Padding(
          padding: const EdgeInsets.all(14),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Icon(Icons.route_outlined, size: 18, color: cs.onSurfaceVariant),
              const SizedBox(width: 8),
              const Expanded(child: Text('Execution trace',
                  style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600))),
            ]),
            const SizedBox(height: 6),
            Text(supported
                ? '${groups.length} observed ${groups.length == 1 ? 'capability' : 'capabilities'}'
                    '  ·  ${requests ?? 0} prepared requests'
                : 'Observed tool activity',
                style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
          ]),
        ),
        if (execution != null && !supported)
          const Padding(padding: EdgeInsets.symmetric(horizontal: 14),
              child: Text('Execution trace version unsupported by this Console.')),
        for (final group in groups) _CapabilityCard(group: group),
        if (supported && groups.isEmpty)
          Padding(padding: const EdgeInsets.fromLTRB(14, 0, 14, 10),
              child: Text('No capability dispatch observed.',
                  style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant))),
        if (omitted is num && omitted > 0)
          Padding(padding: const EdgeInsets.all(14),
              child: Text('$omitted events omitted — trace is incomplete',
                  style: TextStyle(fontSize: 11, color: cs.attention))),
        if (selections.isNotEmpty)
          _details(context, 'Retrieval shortlist',
              '${selections.length} ${selections.length == 1 ? 'snapshot' : 'snapshots'} · not execution',
              selections.expand<Widget>((event) => [
                if (event['fallback'] == true)
                  Text('Fallback selection', style: TextStyle(color: cs.attention)),
                Wrap(spacing: 6, runSpacing: 6, children: [
                  for (final candidate in ((event['candidates'] as List?) ?? []).whereType<Map>())
                    Chip(visualDensity: VisualDensity.compact,
                        avatar: Icon(candidate['kind'] == 'workflow'
                            ? Icons.account_tree_outlined : Icons.description_outlined,
                            size: 14),
                        label: Text('${candidate['name']}',
                            style: const TextStyle(fontSize: 10))),
                ]),
                if (event['candidatesTruncated'] == true)
                  const Text('Additional candidates omitted.'),
                const SizedBox(height: 8),
              ]).toList()),
        if (toolCalls.isNotEmpty || events.isNotEmpty)
          _details(context, 'Technical details',
              '${toolCalls.length} tool-call records · raw event data', [
            const Text('Prepared requests are not provider-call or token counts. '
                'Private model calls are excluded. Native guards may reject '
                'before capability dispatch.',
                style: TextStyle(fontSize: 11, height: 1.5)),
            const SizedBox(height: 10),
            SelectableText(const JsonEncoder.withIndent('  ').convert({
              'events': events,
              'toolCalls': toolCalls,
            }), style: const TextStyle(fontFamily: AdklTheme.mono, fontSize: 10)),
          ]),
      ]),
    );
  }

  Widget _details(BuildContext context, String title, String subtitle,
      List<Widget> children) => Theme(
    data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
    child: ExpansionTile(
      tilePadding: const EdgeInsets.symmetric(horizontal: 14),
      childrenPadding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
      expandedCrossAxisAlignment: CrossAxisAlignment.start,
      title: Text(title, style: const TextStyle(fontSize: 12)),
      subtitle: Text(subtitle, style: const TextStyle(fontSize: 10)),
      children: children,
    ),
  );
}

class _Run {
  _Run(this.event);
  Map event;
  final List<String> nodes = [];
  String get status => '${event['status'] ?? 'unknown'}';
}

class _Group {
  _Group(this.name, this.kind, this.agent);
  final String name;
  final String kind;
  final String agent;
  final List<_Run> runs = [];
}

List<_Group> _groups(List<Map> events) {
  final groups = <_Group>[];
  final runs = <String, _Run>{};
  for (var i = 0; i < events.length; i++) {
    final event = events[i];
    if (event['type'] == 'capability') {
      final name = '${event['capability'] ?? 'Unknown capability'}';
      final kind = '${event['kind'] ?? 'capability'}';
      final agent = '${event['agent'] ?? ''}';
      final call = event['call_id'];
      // Without correlation metadata, keep observations separate rather than
      // merging two unrelated calls and fabricating a successful lifecycle.
      final key = jsonEncode([agent, name, event['operation'],
        call == null || call == '' ? 'uncorrelated-$i' : call]);
      final existing = runs[key];
      if (existing != null) {
        existing.event = event;
      } else {
        final matching = groups.where((g) =>
            g.name == name && g.kind == kind && g.agent == agent);
        final group = matching.isEmpty ? _Group(name, kind, agent) : matching.first;
        if (matching.isEmpty) groups.add(group);
        final run = _Run(event);
        group.runs.add(run);
        runs[key] = run;
      }
    } else if (event['type'] == 'node_output') {
      final path = '${event['path'] ?? ''}';
      final candidates = groups.where((g) => g.kind == 'workflow' &&
          (path == g.name || path.startsWith('${g.name}/')))
          .expand((g) => g.runs).where((r) => r.status == 'started').toList();
      // Node events do not carry a capability call ID. Attach only when exactly
      // one matching workflow is in flight; raw events retain ambiguous outputs.
      if (candidates.length == 1) candidates.single.nodes.add(path);
    }
  }
  return groups;
}

class _CapabilityCard extends StatelessWidget {
  const _CapabilityCard({required this.group});
  final _Group group;

  static const _failures = {'denied', 'failed', 'cancelled'};
  String get _status {
    final statuses = group.runs.map((r) => r.status).toList();
    for (final status in ['failed', 'denied', 'cancelled']) {
      if (statuses.contains(status)) return status;
    }
    return statuses.every((s) => s == 'completed') ? 'completed' : 'unconfirmed';
  }

  String _title(String value) => value.replaceAll('_', ' ').replaceAll('-', ' ')
      .split(' ').where((s) => s.isNotEmpty)
      .map((s) => '${s[0].toUpperCase()}${s.substring(1)}').join(' ');

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final workflow = group.kind == 'workflow';
    final failed = _failures.contains(_status);
    final color = failed ? cs.failure : cs.onSurfaceVariant;
    return Container(
      margin: const EdgeInsets.fromLTRB(10, 0, 10, 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: cs.surface,
        borderRadius: BorderRadius.circular(9),
        border: Border.all(color: failed ? cs.failure : cs.outlineVariant),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(workflow ? Icons.account_tree_outlined : Icons.description_outlined,
              size: 18, color: color),
          const SizedBox(width: 8),
          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(_title(group.name),
                style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
            const SizedBox(height: 3),
            Text(_title(group.kind), style: TextStyle(fontSize: 10, color: cs.onSurfaceVariant)),
          ])),
        ]),
        const SizedBox(height: 9),
        Row(children: [
          Icon(failed ? Icons.error_outline : _status == 'completed'
              ? Icons.check_circle_outline : Icons.help_outline, size: 14, color: color),
          const SizedBox(width: 5),
          Text(_title(_status), style: TextStyle(fontSize: 11,
              fontWeight: FontWeight.w600, color: color)),
        ]),
        const SizedBox(height: 12),
        for (var i = 0; i < group.runs.length; i++) ...[
          for (final node in group.runs[i].nodes)
            _step(cs, _title(node.split('/').last), 'Output observed',
                Icons.subdirectory_arrow_right, cs.onSurfaceVariant),
          _step(cs, workflow
              ? (group.runs.length > 1 ? 'Run ${i + 1} outcome' : 'Workflow outcome')
              : _title('${group.runs[i].event['operation'] ?? 'Operation'}'),
              '${_title(group.runs[i].status)}'
                  '${_failures.contains(group.runs[i].status) ? ' · ${group.runs[i].event['reason'] ?? 'unknown reason'}' : ''}',
              _failures.contains(group.runs[i].status) ? Icons.error_outline
                  : group.runs[i].status == 'completed' ? Icons.check_circle_outline
                  : Icons.help_outline,
              _failures.contains(group.runs[i].status) ? cs.failure : cs.onSurfaceVariant),
        ],
        if (workflow)
          Text('Steps show observed outputs, not timings or business success.',
              style: TextStyle(fontSize: 10, height: 1.4, color: cs.onSurfaceVariant)),
      ]),
    );
  }

  Widget _step(ColorScheme cs, String title, String subtitle, IconData icon, Color color) =>
      IntrinsicHeight(child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        SizedBox(width: 20, child: Column(children: [
          Icon(icon, size: 14, color: color),
          Expanded(child: Container(width: 1, margin: const EdgeInsets.symmetric(vertical: 4),
              color: cs.outlineVariant)),
        ])),
        const SizedBox(width: 7),
        Expanded(child: Padding(padding: const EdgeInsets.only(bottom: 12),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: const TextStyle(fontSize: 12)),
              const SizedBox(height: 2),
              Text(subtitle, style: TextStyle(fontSize: 10, height: 1.4, color: color)),
            ]))),
      ]));
}
