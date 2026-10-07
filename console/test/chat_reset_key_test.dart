import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// Changing features must give the chat panel a NEW identity.
///
/// The server rebuilds the agent on save, but the worker keeps the ADK session it
/// created under the old configuration. Reusing the panel therefore runs the
/// previous pipeline while the UI reports the new one -- and the transcript on
/// screen came from a third. So the panel is keyed on the agent AND a revision
/// counter, and bumping the counter discards the element.
///
/// This is pinned as a key-identity test rather than through ConsoleShell, which
/// needs a live server to build. That is a real limit: it proves the mechanism
/// resets state, not that main.dart bumps the counter on save. The counter was
/// silently lost in a merge with every test still green, which is why the
/// mechanism at least is nailed down here.
void main() {
  /// Rebuilt with the same key, a StatefulWidget keeps its State; with a
  /// different key Flutter disposes the old element and its conversation.
  Widget panel(String agent, int revision) => MaterialApp(
        home: _Counter(key: ValueKey('chat-$agent-$revision')),
      );

  testWidgets('bumping the revision discards the panel state', (t) async {
    await t.pumpWidget(panel('prospect', 0));
    _CounterState state() => t.state<_CounterState>(find.byType(_Counter));
    state().value = 7;   // stands in for an accumulated transcript

    await t.pumpWidget(panel('prospect', 0));
    expect(state().value, 7, reason: 'same key must not reset the conversation');

    await t.pumpWidget(panel('prospect', 1));
    expect(state().value, 0,
        reason: 'a feature change must not leave the old session running');
  });

  testWidgets('switching agent also resets, independently of the revision',
      (t) async {
    await t.pumpWidget(panel('prospect', 0));
    t.state<_CounterState>(find.byType(_Counter)).value = 3;

    await t.pumpWidget(panel('clawtutor', 0));
    // Otherwise one agent's transcript stays on screen under another's name.
    expect(t.state<_CounterState>(find.byType(_Counter)).value, 0);
  });
}

class _Counter extends StatefulWidget {
  const _Counter({super.key});

  @override
  State<_Counter> createState() => _CounterState();
}

class _CounterState extends State<_Counter> {
  int value = 0;

  @override
  Widget build(BuildContext context) => const SizedBox.shrink();
}
