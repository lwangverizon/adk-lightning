import 'package:flutter/material.dart';
import 'dart:convert';

import 'api.dart';
import 'execution_trace_card.dart';
import 'markdown_text.dart';
import 'theme.dart';
import 'widget_card.dart';

/// Test window, pinned to the right of every screen.
///
/// Sierra keeps a chat panel there with Clear/Replay; this is the equivalent for
/// a retrieved-skill agent, with one difference that matters: **the trace is the
/// point**. The question is never only "what did it say" but "why did it pick
/// that skill", so every turn renders the full ranking rather than the winner.
///
/// That is not decoration. Live probes against the real gateway measured margins
/// of 0.09-0.13 on a three-skill agent -- close enough that showing only the top
/// match would hide a coin flip, which is exactly the failure a reviewer needs to
/// catch.
class ChatPanel extends StatefulWidget {
  const ChatPanel({super.key, required this.api});
  final AdklApi api;

  @override
  State<ChatPanel> createState() => _ChatPanelState();
}

class _ChatPanelState extends State<ChatPanel> {
  final _input = TextEditingController();
  final _scroll = ScrollController();
  final _turns = <ChatTurn>[];

  ChatStatus? _status;
  ApiException? _recovery;
  bool _sending = false;
  String _progress = 'Preparing…';
  int _generation = 0;
  List<Map<String, dynamic>> _profiles = [];
  String? _profile;


  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    widget.api.cancelChat();
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final execution = await widget.api.execution();
      final s = await widget.api.chatStatus();
      if (mounted) {
        setState(() {
          _status = s;
          _recovery = s.failure?.canClear == true ? s.failure : null;
          _profiles = ((execution['profiles'] ?? []) as List)
              .map((p) => (p as Map).cast<String, dynamic>()).toList();
          _profile = execution['selected'] as String?;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() => _status = ChatStatus(
            available: false, problems: ['$e'], model: null));
      }
    }
  }

  Future<void> _selectProfile(String? profile) async {
    if (profile == null || _sending) return;
    setState(() => _sending = true);
    try {
      await widget.api.selectExecution(profile);
      if (mounted) setState(() { _turns.clear(); _status = null; });
      await _load();
    } catch (e) {
      if (mounted) {
        setState(() => _status = ChatStatus(
            available: false, problems: ['$e']));
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  Future<void> _send() async {
    final text = _input.text.trim();
    if (text.isEmpty || _sending || _status?.available != true || _recovery != null) return;
    _input.clear();
    setState(() {
      _sending = true;
      _progress = 'Preparing…';
      _turns.add(ChatTurn(userText: text));
    });
    _toBottom();

    final generation = ++_generation;
    try {
      await for (final event in widget.api.chatStream(text)) {
        if (!mounted || generation != _generation) return;
        setState(() {
          if (event['type'] == 'progress') {
            _progress = switch (event['phase']) {
              'tool_call' => 'Calling ${event['name']}…',
              'tool_result' => 'Tool returned; preparing reply…',
              'running' => 'Generating reply…',
              _ => 'Preparing…',
            };
          } else if (event['type'] == 'text') {
            _turns.last = ChatTurn(userText: text, reply: event['text'] as String);
          } else if (event['type'] == 'done') {
            _turns.last = ChatTurn.fromJson(text, event['result']);
          }
        });
        _toBottom();
      }
    } catch (e) {
      if (mounted && generation == _generation) {
        setState(() {
          _turns.last = ChatTurn(userText: text, error: '$e');
          if (e is ApiException && e.canClear) _recovery = e;
        });
      }
    } finally {
      if (mounted && generation == _generation) setState(() => _sending = false);
    }
    _toBottom();
  }

  Future<void> _editTestContext() async {
    final state = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => _TestContextDialog(initial: widget.api.testState ?? {}),
    );
    if (state == null || !mounted) return;
    try {
      await _clear();
      if (mounted) setState(() => widget.api.testState = state.isEmpty ? null : state);
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not reset test conversation: $error')),
        );
      }
    }
  }

  void _cancel() {
    ++_generation;
    widget.api.cancelChat();
    setState(() {
      _sending = false;
      if (_turns.isNotEmpty) {
        _turns.last = ChatTurn(userText: _turns.last.userText, error: 'Turn cancelled. Clear to start a new conversation.');
      }
      _status = ChatStatus(available: false, problems: ['Turn cancelled. Clear to restart.']);
    });
  }

  void _toBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && _scroll.hasClients) {
        if (MediaQuery.disableAnimationsOf(context)) {
          _scroll.jumpTo(_scroll.position.maxScrollExtent);
          return;
        }
        _scroll.animateTo(_scroll.position.maxScrollExtent,
            duration: AdklTheme.springSpatialDuration,
            curve: Curves.easeOutCubic);
      }
    });
  }

  Future<void> _clear() async {
    if (_sending) return;
    setState(() => _sending = true);
    try {
      await widget.api.chatClear();
      if (mounted) setState(() { _turns.clear(); _status = null; _recovery = null; });
      await _load();
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  Future<void> _restart() async {
    try {
      await _clear();
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not clear conversation: $error')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      decoration: BoxDecoration(
        color: cs.surfaceContainerLow,
        border: Border(left: BorderSide(color: cs.outlineVariant)),
      ),
      child: Column(children: [
        _header(cs),
        if (_recovery != null)
          MaterialBanner(
            content: Text(_recovery!.message),
            actions: [TextButton(onPressed: _sending ? null : _restart,
              child: const Text('Clear conversation'))],
          ),
        if (_profiles.isNotEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10),
            child: DropdownButton<String>(
              isExpanded: true,
              value: _profile,
              hint: const Text('Execution environment'),
              onChanged: _sending ? null : _selectProfile,
              items: _profiles.map((p) => DropdownMenuItem<String>(
                value: p['name'] as String,
                child: Text('${p['name']} · ${p['environment']} · ${p['target']}',
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 11)),
              )).toList(),
            ),
          ),
        if (_status?.available == true && _status!.warnings.isNotEmpty)
          Padding(
            padding: const EdgeInsets.all(10),
            child: Text('Limited preview: ${_status!.warnings.join(' ')}',
                maxLines: 6, overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 11, color: cs.attention)),
          ),
        if (_status != null && !_status!.available)
          _unavailable(cs)
        else
          Expanded(child: _transcript(cs)),
        _composer(cs),
      ]),
    );
  }

  Widget _header(ColorScheme cs) => Container(
        constraints: const BoxConstraints(minHeight: 56),
        padding: const EdgeInsets.symmetric(horizontal: 14),
        decoration: BoxDecoration(
          border: Border(bottom: BorderSide(color: cs.outlineVariant)),
        ),
        child: Wrap(spacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
          Text('Test',
              style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  color: cs.onSurface)),
          const SizedBox(width: 8),
          if (_status?.model != null)
            Text(_status!.model!,
                style: TextStyle(
                    fontFamily: AdklTheme.mono,
                    fontSize: 10.5,
                    color: cs.onSurfaceVariant)),

          IconButton(tooltip: widget.api.testState == null ? 'Set test context' : 'Edit test context (active)',
            onPressed: _sending ? null : _editTestContext,
            icon: const Icon(Icons.data_object, size: 16)),
          TextButton.icon(
            onPressed: _sending ? null : _restart,
            icon: const Icon(Icons.refresh, size: 13),
            label: const Text('Clear'),
          ),
        ]),
      );

  /// Preflight failures are shown before a turn is attempted, with the fix,
  /// because "chat does not work" is useless next to "run adkl embed".
  Widget _unavailable(ColorScheme cs) => Expanded(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: ListView(
              children: [
                Row(children: [
                  Icon(Icons.info_outline, size: 15, color: cs.attention),
                  const SizedBox(width: 8),
                  Expanded(child: Text('Chat is not available',
                      style: TextStyle(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w700,
                          color: cs.onSurface))),
                ]),
                const SizedBox(height: 10),
                ...(_status?.problems ?? []).map((p) => Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Text(p,
                          style: TextStyle(
                              fontSize: 11.5,
                              height: 1.5,
                              color: cs.onSurfaceVariant)),
                    )),
                const SizedBox(height: 6),
                OutlinedButton(onPressed: _load, child: const Text('Retry')),
              ]),
        ),
      );

  Widget _transcript(ColorScheme cs) {
    if (_turns.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(Icons.forum_outlined, size: 22, color: cs.onSurfaceVariant),
            const SizedBox(height: 10),
            Text('Send a message to test the agent',
                style: TextStyle(fontSize: 12, color: cs.onSurface)),
            const SizedBox(height: 6),
            Text('See which skills and workflows ran, with details when you need them.',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
          ]),
        ),
      );
    }
    return ListView.builder(
      controller: _scroll,
      padding: const EdgeInsets.fromLTRB(12, 14, 12, 14),
      itemCount: _turns.length,
      itemBuilder: (c, i) => _turn(cs, _turns[i], i == _turns.length - 1),
    );
  }

  Widget _turn(ColorScheme cs, ChatTurn turn, bool isLast) {
    final pending = isLast && _sending;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      // user
      Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 8),
        decoration: BoxDecoration(
          color: cs.surfaceContainerHigh,
          borderRadius: BorderRadius.circular(AdklTheme.rMd),
        ),
        child: Text(turn.userText,
            style: TextStyle(fontSize: 12, height: 1.45, color: cs.onSurface)),
      ),

      if (turn.matches.isNotEmpty) _trace(cs, turn),
      if (turn.execution != null || turn.toolCalls.isNotEmpty)
        ExecutionTraceCard(execution: turn.execution, toolCalls: turn.toolCalls),

      if (pending)
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 10),
          child: Row(children: [
            SizedBox(
                width: 12,
                height: 12,
                child: CircularProgressIndicator(
                    strokeWidth: 2, color: cs.onSurfaceVariant)),
            const SizedBox(width: 9),
            Flexible(child: Text(_progress,
                style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant))),
            TextButton(onPressed: _cancel, child: const Text('Stop')),
          ]),
        ),

      if (turn.error != null)
        Container(
          margin: const EdgeInsets.only(bottom: 14),
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
            color: cs.failureContainer.withValues(alpha: 0.45),
            borderRadius: BorderRadius.circular(AdklTheme.rMd),
            border: Border.all(color: cs.failure.withValues(alpha: 0.35)),
          ),
          child: Text(turn.error!,
              style: TextStyle(
                  fontSize: 11, height: 1.45, color: cs.onSurface)),
        ),

      // Replies are markdown: the response-standards block mandates bold
      // product names and bullet lists, so raw asterisks would misrepresent
      // what the customer sees.
      if (turn.reply != null)
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: MarkdownText(turn.reply!),
        ),

      // Widgets after the text, which is the production contract: companion
      // text first, then the widget. A widget with no preceding text is a
      // violation the panel makes visible by showing them in that order.
      ...turn.widgets.map((w) => WidgetCard(widget_: w)),

      // A widget tool ran and rendered nothing. Now a genuine signal rather than
      // a standing disclaimer: with tools bound, this means the tool declined,
      // or it staged a payload and no turn-boundary emitter drained it. The
      // second failed silently once -- the tool returned {"status": "staged"},
      // the turn completed clean, and no widget ever appeared.
      if (turn.widgets.isEmpty &&
          turn.widgetTools.isNotEmpty &&
          turn.executableTools.isNotEmpty)
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(Icons.widgets_outlined, size: 12, color: cs.onSurfaceVariant),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                  'A widget tool ran (${turn.widgetTools.join(', ')}) but no '
                  'widget was emitted. Either it declined, or it staged a '
                  'payload and no turn-boundary emitter drained it — check '
                  'tools.widget_specs.',
                  style: TextStyle(
                      fontSize: 9.5, height: 1.4, color: cs.onSurfaceVariant)),
            ),
          ]),
        ),

      if (turn.reply != null || turn.widgets.isNotEmpty)
        const SizedBox(height: 6),
    ]);
  }

  /// One widget spec's gate decision.
  ///
  /// The gate name is deliberately shown raw rather than prosified: it is the
  /// same string the library and its tests use, so a reviewer can grep for it.
  Widget _widgetGate(ColorScheme cs, Map<String, dynamic> g) {
    final passed = g['passed'] == true;
    final gate = (g['gate'] ?? '') as String;
    final revived = g['revived'] == true;

    // `built_nothing` is the one decline worth alarming on: every other gate is
    // a decision someone declared, while this means a builder ran and produced
    // nothing without saying it expected to.
    final color = passed
        ? cs.onSurface
        : (gate == 'built_nothing' || gate == 'build_failed'
            ? cs.failure
            : cs.onSurfaceVariant);

    return Padding(
      padding: const EdgeInsets.only(bottom: 2),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(passed ? Icons.check : Icons.remove, size: 10, color: color),
        const SizedBox(width: 5),
        Expanded(
          child: Tooltip(
            message: (g['detail'] ?? '') as String,
            child: Text(
                '${g['key']}'
                '${passed ? " emitted" : "  $gate"}'
                '${revived ? "  (revived)" : ""}',
                style: TextStyle(
                    fontSize: 9.5,
                    fontFamily: AdklTheme.mono,
                    height: 1.35,
                    color: color)),
          ),
        ),
      ]),
    );
  }

  /// The retrieval trace. Every candidate with its score and source, because a
  /// thin margin between the top two is the failure mode that a reply cannot
  /// show you.
  Widget _trace(ColorScheme cs, ChatTurn turn) {
    final margin = turn.margin;
    final thin = margin != null && margin < 0.05;
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: cs.surface,
        borderRadius: BorderRadius.circular(AdklTheme.rMd),
        border: Border.all(color: cs.outlineVariant),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(Icons.travel_explore, size: 12, color: cs.onSurfaceVariant),
          const SizedBox(width: 6),
          Text('RETRIEVAL',
              style: TextStyle(
                  fontSize: 9.5,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.8,
                  color: cs.onSurfaceVariant)),
          const Spacer(),
          if (turn.fallback)
            Tooltip(
              message: 'Embedding failed, so every skill was exposed. Ranking '
                  'degraded rather than failing the turn.',
              child: Text('fallback',
                  style: TextStyle(
                      fontSize: 9.5,
                      fontWeight: FontWeight.w700,
                      color: cs.attention)),
            )
          else
            Tooltip(
              message: margin == null
                  ? 'The host did not report a ranking margin.'
                  : thin
                      ? 'Gap to the runner-up is under 0.05 -- effectively a tie. '
                          'Sharpen the descriptions or the exclusions.'
                      : 'Gap between the top two scores.',
              child: Text(margin == null ? 'margin unavailable' : 'margin ${margin.toStringAsFixed(3)}',
                  style: TextStyle(
                      fontSize: 9.5,
                      fontFamily: AdklTheme.mono,
                      fontWeight: thin ? FontWeight.w700 : FontWeight.w400,
                      color: thin ? cs.attention : cs.onSurfaceVariant)),
            ),
        ]),
        const SizedBox(height: 7),
        ...turn.matches.map((m) {
          final picked = m.skill == turn.picked;
          return Padding(
            padding: const EdgeInsets.only(bottom: 3),
            child: Row(children: [
              SizedBox(
                width: 34,
                child: Text(m.score.toStringAsFixed(3),
                    style: TextStyle(
                        fontSize: 10,
                        fontFamily: AdklTheme.mono,
                        fontWeight: picked ? FontWeight.w700 : FontWeight.w400,
                        color: picked ? cs.onSurface : cs.onSurfaceVariant)),
              ),
              const SizedBox(width: 7),
              Expanded(
                child: Text(m.skill,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                        fontSize: 10.5,
                        fontWeight: picked ? FontWeight.w700 : FontWeight.w400,
                        color: picked ? cs.onSurface : cs.onSurfaceVariant)),
              ),
              Text(m.source,
                  style: TextStyle(
                      fontSize: 9,
                      color: m.source == 'pinned'
                          ? cs.info
                          : cs.onSurfaceVariant)),
              if (picked) ...[
                const SizedBox(width: 5),
                Icon(Icons.check, size: 11, color: cs.primary),
              ],
            ]),
          );
        }),
        const SizedBox(height: 6),
        Text('${turn.modelCalls} legacy diagnostic model calls · ${turn.latencyMs} ms',
            style: TextStyle(fontSize: 10, color: cs.onSurfaceVariant)),
        if (turn.activatedSkills.isNotEmpty)
          Text('Active: ${turn.activatedSkills.join(', ')}',
              style: TextStyle(fontSize: 10, color: cs.onSurfaceVariant)),
        if (turn.boundTools.isNotEmpty) ...[
          Divider(height: 13, color: cs.outlineVariant),
          Text('bound  ${turn.boundTools.join(', ')}',
              style: TextStyle(
                  fontSize: 9.5,
                  fontFamily: AdklTheme.mono,
                  height: 1.4,
                  color: cs.onSurfaceVariant)),
          const SizedBox(height: 3),
          Row(children: [
            Text('~${turn.toolTokens} tok',
                style: TextStyle(fontSize: 9, color: cs.onSurfaceVariant)),
            const SizedBox(width: 10),
            Text('${turn.latencyMs} ms',
                style: TextStyle(fontSize: 9, color: cs.onSurfaceVariant)),
            const Spacer(),
            // Executable vs bound, per turn. This used to read "not executed"
            // unconditionally, on the false premise that the tools needed
            // credentials this library lacks -- they need env vars, and with
            // tools.repo set they run for real. An unconditional label is worse
            // than none: it taught the reader to discount every reply.
            if (turn.executableTools.isEmpty)
              Tooltip(
                message: 'No tool ran. Set tools.repo in agent.yaml to import '
                    'real callables; without it the reply is model memory only, '
                    'and the model is told to say so.',
                child: Text('no tools',
                    style: TextStyle(fontSize: 9, color: cs.attention)),
              )
            else
              Tooltip(
                message: '${turn.executableTools.length} of '
                    '${turn.boundTools.length} bound tools are executable and '
                    'hit live services.',
                child: Text('${turn.executableTools.length} executable',
                    style: TextStyle(fontSize: 9, color: cs.onSurfaceVariant)),
              ),
          ]),

          if (turn.unimportableTools.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text('could not import  ${turn.unimportableTools.join(', ')}',
                style: TextStyle(
                    fontSize: 9.5,
                    fontFamily: AdklTheme.mono,
                    color: cs.failure)),
          ],

          // Loader failures. Shown because the widget emitter failed silently
          // for an entire debugging round: it returned a problem string nothing
          // read, so the panel showed zero widgets and no error at all.
          ...turn.toolLoadProblems.map((p) => Padding(
                padding: const EdgeInsets.only(top: 5),
                child: Text(p,
                    style: TextStyle(
                        fontSize: 9, height: 1.35, color: cs.attention)),
              )),
          if (turn.outputFindings.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text('reply filtered  ${turn.outputFindings.join(', ')}',
                style: TextStyle(
                    fontSize: 9.5,
                    fontFamily: AdklTheme.mono,
                    color: cs.attention)),
          ],
        ],

        // The staged-widget pipeline. Every spec and the gate it stopped at,
        // because "no widget appeared" needs an answer and the gate name IS the
        // answer -- `not_live` sends you to the staging tool, `built_nothing` to
        // the builder, `suppressed` to the surface rule.
        if (turn.widgetGates.isNotEmpty) ...[
          Divider(height: 13, color: cs.outlineVariant),
          Row(children: [
            Text('WIDGETS',
                style: TextStyle(
                    fontSize: 9,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.8,
                    color: cs.onSurfaceVariant)),
            const Spacer(),
            // The contract shaped the reply that is displayed above. Null is
            // worth seeing: a reply restating a card is what that looks like.
            Tooltip(
              message: turn.contract != null
                  ? 'Injected before the model wrote, from: '
                      '${turn.contractSources.join(', ')}'
                  : (turn.contractNote ??
                      'No reply-shaping contract resolved, so the model wrote '
                      'without knowing a widget was coming.'),
              child: Text(
                  turn.contract ??
                      (turn.contractNote != null
                          ? 'contract not injectable'
                          : 'no contract'),
                  style: TextStyle(
                      fontSize: 9,
                      fontFamily: AdklTheme.mono,
                      color: turn.contract == null
                          ? cs.attention
                          : cs.info)),
            ),
          ]),
          const SizedBox(height: 4),
          ...turn.widgetGates.map((g) => _widgetGate(cs, g)),
        ],
        if (turn.unresolvedTools.isNotEmpty) ...[
          const SizedBox(height: 5),
          Text('unresolved  ${turn.unresolvedTools.join(', ')}',
              style: TextStyle(
                  fontSize: 9.5,
                  fontFamily: AdklTheme.mono,
                  color: cs.failure)),
        ],
      ]),
    );
  }

  Widget _composer(ColorScheme cs) => Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          border: Border(top: BorderSide(color: cs.outlineVariant)),
        ),
        child: Row(children: [
          Expanded(
            child: Semantics(label: 'Test message', child: TextField(
              controller: _input,
              enabled: _status?.available == true && _recovery == null,
              onSubmitted: (_) => _send(),
              style: const TextStyle(fontSize: 12),
              decoration: InputDecoration(
                hintText: _status == null ? 'Loading…'
                    : _recovery != null ? 'Clear conversation to continue'
                    : _status!.available ? 'Message' : 'unavailable',
                isDense: true,
                contentPadding:
                    const EdgeInsets.symmetric(horizontal: 11, vertical: 11),
                fillColor: cs.surface,
              ),
            )),
          ),
          const SizedBox(width: 8),
          FilledButton(
            onPressed:
                (_status?.available ?? false) && !_sending && _recovery == null ? _send : null,
            child: const Icon(Icons.arrow_upward, size: 20, semanticLabel: 'Send message'),
          ),
        ]),
      );
}

class _TestContextDialog extends StatefulWidget {
  const _TestContextDialog({required this.initial});
  final Map<String, dynamic> initial;
  @override
  State<_TestContextDialog> createState() => _TestContextDialogState();
}

class _TestContextDialogState extends State<_TestContextDialog> {
  late final _input = TextEditingController(
    text: const JsonEncoder.withIndent('  ').convert(widget.initial));
  String? _error;
  @override
  void dispose() { _input.dispose(); super.dispose(); }
  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Local test context'),
    content: SizedBox(width: 480, child: Column(mainAxisSize: MainAxisSize.min, children: [
      const Text('JSON state applied to each test turn. Use synthetic scenario data. This does not authenticate a production user. Applying starts a new conversation.'),
      TextField(controller: _input, minLines: 5, maxLines: 12,
        decoration: InputDecoration(labelText: 'Test state JSON', errorText: _error)),
    ])),
    actions: [
      TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
      FilledButton(onPressed: () {
        try {
          final value = jsonDecode(_input.text);
          if (value is! Map<String, dynamic>) throw const FormatException();
          Navigator.pop(context, value);
        } catch (_) { setState(() => _error = 'Enter a valid JSON object.'); }
      }, child: const Text('Apply')),
    ],
  );
}
