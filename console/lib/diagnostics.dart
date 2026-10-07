import 'package:flutter/material.dart';

import 'api.dart';
import 'theme.dart';

/// Presentation of existing static findings, not another checker.
extension FindingPresentation on Finding {
  Color tone(ColorScheme cs) => isError ? cs.failure
      : severity == 'WARN' ? cs.attention : cs.onSurfaceVariant;
  IconData get icon => isError ? Icons.error_outline
      : severity == 'WARN' ? Icons.warning_amber_rounded : Icons.info_outline;
}

/// Two compact badges, with the rest accessible through an overflow button.
class FindingBadges extends StatelessWidget {
  const FindingBadges({super.key, required this.findings});
  final List<Finding> findings;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final sorted = [...findings]..sort((a, b) {
      const rank = {'ERROR': 0, 'WARN': 1, 'INFO': 2};
      return (rank[a.severity] ?? 2).compareTo(rank[b.severity] ?? 2);
    });
    // Multiple occurrences of a rule need only one badge; retain all details.
    final rules = <String, Finding>{};
    for (final f in sorted) { rules.putIfAbsent(f.rule, () => f); }
    final badges = rules.values.toList();
    return Wrap(spacing: 4, runSpacing: 4, children: [
      for (final f in badges.take(2))
        ActionChip(
          visualDensity: VisualDensity.compact,
          materialTapTargetSize: MaterialTapTargetSize.padded,
          avatar: Icon(f.icon, size: 12, color: f.tone(cs)),
          label: Text(f.title, overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 10, color: f.tone(cs))),
          tooltip: '${f.severity} · ${f.title} · Show explanation',
          onPressed: () => showFindingDetails(context,
              sorted.where((item) => item.rule == f.rule).toList()),
        ),
      if (badges.length > 2)
        ActionChip(
          visualDensity: VisualDensity.compact,
          materialTapTargetSize: MaterialTapTargetSize.padded,
          label: Text('+${badges.length - 2}'),
          tooltip: 'Show all findings for this item',
          onPressed: () => showFindingDetails(context, sorted),
        ),
    ]);
  }
}

void showFindingDetails(BuildContext context, List<Finding> findings) {
  showDialog<void>(context: context, builder: (context) => AlertDialog(
    title: const Text('Static checks'),
    content: SizedBox(width: 480, child: SingleChildScrollView(child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        const Text('Declaration checks, not proof of tool execution or widget delivery.'),
        for (final f in findings) ...[
          const SizedBox(height: 16),
          Text(f.title, style: Theme.of(context).textTheme.titleSmall),
          Text('${f.severity} · ${f.skill}'),
          const SizedBox(height: 6),
          Text(f.message),
          const SizedBox(height: 8),
          Text(f.nextStep),
          ExpansionTile(title: const Text('Technical details'),
            children: [SelectableText(f.rule)]),
        ],
      ],
    ))),
    actions: [TextButton(onPressed: () => Navigator.pop(context), child: const Text('Close'))],
  ));
}
