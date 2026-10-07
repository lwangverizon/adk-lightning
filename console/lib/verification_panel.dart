import 'package:flutter/material.dart';

/// Saved host evaluation evidence, separate from static content findings.
class VerificationPanel extends StatelessWidget {
  const VerificationPanel({super.key, required this.status, required this.busy,
    required this.onRun, required this.onRefresh});
  final Map<String, dynamic> status;
  final bool busy;
  final void Function(String) onRun;
  final VoidCallback onRefresh;

  String _measurement(Map values, String metric) {
    final value = values[metric];
    if (value is! num) return 'Unavailable';
    if (metric == 'p95_latency_ms') return value.toStringAsFixed(1);
    if (metric == 'total_cost_usd') return value.toStringAsFixed(4);
    return '$value';
  }

  @override
  Widget build(BuildContext context) {
    final state = status['state'] ?? 'unconfigured';
    final configured = status['configured'] == true;
    final disabled = busy || state == 'running' || !configured;
    final comparison = status['comparison'] as Map?;
    final baselineOnly = status['mode'] == 'baseline';
    final labels = {
      'unconfigured': 'Verification not configured',
      'unavailable': 'Verification unavailable',
      'not_run': 'No verification run yet',
      'running': 'Verification running…',
      'baseline_recorded': 'Baseline recorded',
      'passed': 'Saved changes passed',
      'regressed': 'Saved changes failed comparison',
      'failed': 'Verification did not complete',
      'stale': 'Saved changes need verification',
    };
    return Card.outlined(child: Padding(padding: const EdgeInsets.all(16), child: Column(
      crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('Verify skill changes', style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 8),
        const Text('Save your edits first. Run the host’s test cases and compare them with a recorded baseline.'),
        const SizedBox(height: 12),
        Text(labels[state] ?? '$state', style: Theme.of(context).textTheme.titleMedium),
        if (status['message'] != null) Text('${status['message']}'),
        if (!configured) const Text('Your developer can connect a host evaluator with verification.yaml. See docs/verification.md.'),
        if (configured) Text('Execution profile: ${status['profile']}'),
        const SizedBox(height: 12),
        Wrap(spacing: 8, runSpacing: 8, children: [
          OutlinedButton(onPressed: disabled ? null : () => onRun('baseline'),
            child: Text(status['baseline'] == null ? 'Record baseline' : 'Record new baseline')),
          FilledButton(onPressed: disabled || status['baseline'] == null ? null : () => onRun('candidate'),
            child: const Text('Verify saved changes')),
          TextButton(onPressed: busy ? null : onRefresh, child: const Text('Refresh verification')),
        ]),
        if (status['baseline'] != null) const Padding(padding: EdgeInsets.only(top: 8),
          child: Text('A new baseline becomes the reference for future comparisons. Earlier runs remain in the project.')),
        if (status['changes'] case final List changes when changes.isNotEmpty)
          ExpansionTile(title: Text('${changes.length} changed input ${changes.length == 1 ? 'file' : 'files'}'), children: [
            for (final change in changes) ListTile(dense: true,
              title: Text('${change['path']}'), subtitle: Text('${change['change']}')),
          ]),
        if (comparison != null) ...[
          const SizedBox(height: 12),
          Text(state == 'stale' ? 'Previous result — saved inputs have changed' :
            state == 'baseline_recorded' ? 'Baseline measurements' : 'Comparison measurements'),
          const SizedBox(height: 8),
          SingleChildScrollView(scrollDirection: Axis.horizontal, child: DataTable(
            columns: [const DataColumn(label: Text('Measure')), const DataColumn(label: Text('Baseline')),
              if (!baselineOnly) const DataColumn(label: Text('Candidate'))],
            rows: [
              for (final entry in {'successes': 'Passed observations', 'observations': 'Total observations',
                'p95_latency_ms': 'p95 latency (ms)', 'total_cost_usd': 'Total cost (USD)'}.entries)
                DataRow(cells: [DataCell(Text(entry.value)),
                  DataCell(Text(_measurement(comparison['baseline'] as Map, entry.key))),
                  if (!baselineOnly) DataCell(Text(_measurement(comparison['candidate'] as Map, entry.key)))]),
            ])),
          for (final failure in (comparison['failures'] as List? ?? [])) Text('$failure'),
          if ((comparison['regressed_cases'] as List? ?? []).isNotEmpty)
            Text('Regressed cases: ${(comparison['regressed_cases'] as List).join(', ')}'),
        ],
        if (status['latest'] != null) Padding(padding: const EdgeInsets.only(top: 8),
          child: SelectableText('Evidence: .adkl/verification/${status['latest']}.json')),
        const SizedBox(height: 8),
        const Text('Results cover the declared inputs and host grading. Publication remains a separate host release step.'),
      ])));
  }
}
