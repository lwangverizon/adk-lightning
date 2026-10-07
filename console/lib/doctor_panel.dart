import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'api.dart';

/// Rules and fix guidance come from the same report used by the CLI.
class DoctorPanel extends StatefulWidget {
  const DoctorPanel({super.key, required this.api, this.onReport, this.initialReport});
  final Map<String, dynamic>? initialReport;
  final AdklApi api;
  final ValueChanged<Map<String, dynamic>>? onReport;

  @override
  State<DoctorPanel> createState() => _DoctorPanelState();
}

class _DoctorPanelState extends State<DoctorPanel> {
  Map<String, dynamic>? _report;
  final _selected = <String>{};
  bool _busy = false, _inspect = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _report = widget.initialReport;
    // Opening always runs static checks; factory construction stays explicit.
    _run();
  }

  Future<void> _run({bool? inspect, bool export = false}) async {
    final native = inspect ?? _inspect;
    setState(() { _busy = true; _error = null; });
    try {
      final result = await widget.api.projectAction('doctor', {
        'inspect': native,
        if (_report != null) 'previous': _report,
        if (export) 'selected': _selected.toList(),
      });
      if (!mounted) return;
      final report = result['report'] as Map<String, dynamic>;
      setState(() {
        _report = report;
        _inspect = native;
        if (!export) {
          _selected.clear();
          _selected.addAll((report['findings'] as List).map((f) => f['id'] as String));
        }
      });
      widget.onReport?.call(report);
      if (export) {
        setState(() => _busy = false);
        await showDialog<void>(context: context, builder: (context) => AlertDialog(
          title: const Text('Coding-agent fix brief'),
          content: SizedBox(width: 720, child: SingleChildScrollView(
            child: SelectableText(result['brief'] as String))),
          actions: [
            TextButton(onPressed: () async {
              await Clipboard.setData(ClipboardData(text: result['brief'] as String));
              if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Fix brief copied')));
              }
            }, child: const Text('Copy Markdown')),
            TextButton(onPressed: () => Navigator.pop(context), child: const Text('Close')),
          ],
        ));
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = '$e';
          if (_report != null) {
            _report = {..._report!, 'freshness': 'unknown', 'passed': false};
          }
        });
        if (_report != null) widget.onReport?.call(_report!);
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final report = _report;
    return AlertDialog(
      title: const Text('ADKL doctor'),
      content: SizedBox(width: 760, height: 520, child: ListView(
        children: [
          const Text('Checks saved base files. Unsaved edits are excluded. Native inspection constructs trusted factory code in a fresh local worker.'),
          const SizedBox(height: 8),
          Wrap(spacing: 8, children: [
            OutlinedButton(onPressed: _busy ? null : () => _run(), child: const Text('Rerun checks')),
            OutlinedButton(onPressed: _busy ? null : () => _run(inspect: true), child: const Text('Inspect native checks')),
            OutlinedButton(onPressed: _busy || report == null ? null : () => _run(export: true), child: const Text('Export fix brief')),
          ]),
          if (_busy) const LinearProgressIndicator(),
          if (_error != null) Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
          if (report != null) ...[
            Text('Evidence: ${report['freshness']} · ${report['passed'] == true ? 'No blocking findings in completed checks' : 'Review required'}'),
            const Text('Unchecked areas are not a pass. Export reruns checks against saved source.'),
            for (final check in report['checks'] as List)
              ListTile(dense: true, title: Text('${check['id']}: ${check['status']}'), subtitle: Text(check['detail'] as String)),
            if (report['comparison'] case final Map comparison)
              Text(comparison.entries.map((e) => '${e.key}: ${(e.value as List).length}').join(' · ')),
            for (final note in (report['comparisonNotes'] as List?) ?? [])
              Text(note as String),
            if ((report['findings'] as List).isEmpty) const Text('No findings from completed checks.'),
            for (final finding in report['findings'] as List)
              CheckboxListTile(
                value: _selected.contains(finding['id']),
                onChanged: _busy ? null : (value) => setState(() {
                  if (value == true) { _selected.add(finding['id'] as String); }
                  else { _selected.remove(finding['id']); }
                }),
                title: Text('${finding['severity']} ${finding['title'] ?? finding['rule']}'),
                subtitle: Text('${finding['location']?['path'] ?? finding['skill'] ?? 'Project'}\n${finding['message']}\n${finding['remediation']}'),
                isThreeLine: false,
              ),
          ],
        ],
      )),
      actions: [TextButton(onPressed: () => Navigator.pop(context), child: const Text('Close'))],
    );
  }
}
