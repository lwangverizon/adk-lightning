import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'api.dart';

/// Native graph declarations only. Runtime traversal and outcomes live in Test.
class WorkflowGraphView extends StatelessWidget {
  const WorkflowGraphView({super.key, required this.graph});
  final NativeWorkflowGraph? graph;

  @override
  Widget build(BuildContext context) {
    final data = graph;
    if (data == null || !data.available || data.nodes.isEmpty) {
      return const Text('Graph topology unavailable for this workflow.');
    }
    final layout = _GraphLayout(data);
    final cs = Theme.of(context).colorScheme;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text('Declared graph · ${data.nodes.length} nodes · ${data.edges.length} edges',
        style: Theme.of(context).textTheme.titleMedium),
      const SizedBox(height: 8),
      Text('Pan and zoom to explore. Branch labels show declared routes; '
        'this view does not show which path ran.',
        style: TextStyle(color: cs.onSurfaceVariant)),
      const SizedBox(height: 12),
      SizedBox(height: math.min(560, math.max(280, layout.height)),
        child: ClipRect(child: InteractiveViewer(
          constrained: false, minScale: 0.4, maxScale: 2.5,
          child: SizedBox(width: layout.width, height: layout.height,
            child: Stack(children: [
              CustomPaint(size: Size(layout.width, layout.height),
                painter: _EdgePainter(layout, cs.outline, cs.primary)),
              for (final edge in data.edges)
                if (edge.label.isNotEmpty && layout.points.containsKey(edge.from)
                    && layout.points.containsKey(edge.to))
                  Positioned(
                    left: layout.labelPosition(edge).dx,
                    top: layout.labelPosition(edge).dy,
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
                      decoration: BoxDecoration(color: cs.surfaceContainerHighest,
                        borderRadius: BorderRadius.circular(5)),
                      child: Text(edge.label, style: Theme.of(context).textTheme.labelSmall)),
                  ),
              for (final node in data.nodes)
                if (layout.points[node.name] case final point?)
                  Positioned(left: point.dx, top: point.dy,
                    child: Container(width: _GraphLayout.nodeWidth,
                      height: _GraphLayout.nodeHeight,
                      padding: const EdgeInsets.all(10),
                      decoration: BoxDecoration(
                        color: node.name == '__START__' ? cs.primaryContainer : cs.surface,
                        border: Border.all(color: cs.outline),
                        borderRadius: BorderRadius.circular(10)),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisAlignment: MainAxisAlignment.center, children: [
                          Text(node.name == '__START__' ? 'START' : node.name,
                            maxLines: 2, overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontWeight: FontWeight.w600)),
                          Text(node.kind, maxLines: 1, overflow: TextOverflow.ellipsis,
                            style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
                          if (node.description.isNotEmpty)
                            Text(node.description, maxLines: 1, overflow: TextOverflow.ellipsis,
                              style: TextStyle(fontSize: 10, color: cs.onSurfaceVariant)),
                        ]))),
            ])),
        )),
      ),
    ]);
  }
}

class _GraphLayout {
  _GraphLayout(NativeWorkflowGraph graph) : edges = graph.edges {
    // Mark edges that close a depth-first cycle as return paths. The
    // remaining edges form a DAG, so every join can follow all predecessors.
    final names = graph.nodes.map((n) => n.name).toSet();
    final outgoing = {for (final name in names) name: <NativeWorkflowEdge>[]};
    for (final edge in graph.edges) {
      if (names.contains(edge.from) && names.contains(edge.to)) {
        outgoing[edge.from]!.add(edge);
      }
    }
    final visiting = <String>{}, visited = <String>{};
    void visit(String name) {
      visiting.add(name);
      for (final edge in outgoing[name]!) {
        if (visiting.contains(edge.to)) {
          returnEdges.add(edge);
        } else if (!visited.contains(edge.to)) {
          visit(edge.to);
        }
      }
      visiting.remove(name);
      visited.add(name);
    }
    for (final node in graph.nodes) {
      if (!visited.contains(node.name)) visit(node.name);
    }
    final incoming = {for (final name in names) name: 0};
    for (final edge in graph.edges) {
      if (names.contains(edge.from) && names.contains(edge.to)
          && !returnEdges.contains(edge)) {
        incoming[edge.to] = incoming[edge.to]! + 1;
      }
    }
    final depth = {for (final name in names) name: 0};
    final ready = graph.nodes.where((n) => incoming[n.name] == 0)
      .map((n) => n.name).toList();
    for (var i = 0; i < ready.length; i++) {
      final from = ready[i];
      for (final edge in outgoing[from]!) {
        if (returnEdges.contains(edge)) continue;
        depth[edge.to] = math.max(depth[edge.to]!, depth[from]! + 1);
        incoming[edge.to] = incoming[edge.to]! - 1;
        if (incoming[edge.to] == 0) ready.add(edge.to);
      }
    }
    final layers = <int, List<String>>{};
    for (final node in graph.nodes) {
      layers.putIfAbsent(depth[node.name]!, () => []).add(node.name);
    }
    final maxRows = layers.values.map((v) => v.length).fold<int>(0, math.max);
    width = (layers.keys.reduce(math.max) + 1) * columnGap + 50;
    height = maxRows * rowGap + 90;
    for (final entry in layers.entries) {
      for (var row = 0; row < entry.value.length; row++) {
        points[entry.value[row]] = Offset(25 + entry.key * columnGap,
          50 + row * rowGap + (maxRows - entry.value.length) * rowGap / 2);
      }
    }
  }

  static const nodeWidth = 175.0, nodeHeight = 92.0;
  static const columnGap = 255.0, rowGap = 156.0;
  final List<NativeWorkflowEdge> edges;
  final returnEdges = <NativeWorkflowEdge>{};
  final points = <String, Offset>{};
  bool isReturnEdge(NativeWorkflowEdge edge) => returnEdges.contains(edge);
  late final double width, height;

  Offset labelPosition(NativeWorkflowEdge edge) {
    final from = points[edge.from]!, to = points[edge.to]!;
    if (isReturnEdge(edge)) {
      return Offset((from.dx + to.dx + nodeWidth) / 2 - 25,
        math.min(from.dy, to.dy) - 38);
    }
    return Offset((from.dx + nodeWidth + to.dx) / 2 - 18,
      (from.dy + to.dy) / 2 + nodeHeight / 2 - 20);
  }
}

class _EdgePainter extends CustomPainter {
  _EdgePainter(this.layout, this.color, this.arrowColor);
  final _GraphLayout layout;
  final Color color, arrowColor;

  @override
  void paint(Canvas canvas, Size size) {
    final stroke = Paint()..color = color..strokeWidth = 2
      ..style = PaintingStyle.stroke;
    final arrow = Paint()..color = arrowColor..style = PaintingStyle.fill;
    for (final edge in layout.edges) {
      final source = layout.points[edge.from], target = layout.points[edge.to];
      if (source == null || target == null) continue;
      final start = Offset(source.dx + _GraphLayout.nodeWidth,
        source.dy + _GraphLayout.nodeHeight / 2);
      final end = Offset(target.dx, target.dy + _GraphLayout.nodeHeight / 2);
      if (layout.isReturnEdge(edge)) {
        final top = math.min(source.dy, target.dy) - 22;
        final returnEnd = Offset(target.dx + _GraphLayout.nodeWidth / 2, target.dy);
        final path = Path()..moveTo(start.dx, start.dy)
          ..cubicTo(start.dx + 30, top, returnEnd.dx + 30, top,
            returnEnd.dx, returnEnd.dy);
        canvas.drawPath(path, stroke);
        canvas.drawPath(Path()..moveTo(returnEnd.dx, returnEnd.dy)
          ..lineTo(returnEnd.dx - 5, returnEnd.dy - 8)
          ..lineTo(returnEnd.dx + 5, returnEnd.dy - 8)..close(), arrow);
      } else {
        final control = math.max(35.0, (end.dx - start.dx).abs() / 2);
        final path = Path()..moveTo(start.dx, start.dy)
          ..cubicTo(start.dx + control, start.dy,
            end.dx - control, end.dy, end.dx, end.dy);
        canvas.drawPath(path, stroke);
        canvas.drawPath(Path()..moveTo(end.dx, end.dy)
          ..lineTo(end.dx - 8, end.dy - 5)
          ..lineTo(end.dx - 8, end.dy + 5)..close(), arrow);
      }
    }
  }

  @override
  bool shouldRepaint(covariant _EdgePainter old) =>
    old.layout != layout || old.color != color || old.arrowColor != arrowColor;
}
