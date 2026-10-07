import 'package:flutter/material.dart';

import 'theme.dart';

/// Agent Configuration: a list of named cards, each opening a detail pane.
///
/// A middle column lists settings with titles, summaries and icons; the detail
/// pane shows an empty state until a card is selected.
/// Material 3 components use indigo for actions and amber for attention.
///
/// Why cards rather than one long settings form: each topic has its own
/// validation and its own failure mode, and a list makes "what can I configure"
/// answerable at a glance. It also gives each card room to state consequences --
/// a feature toggle that does not say what it costs is a trap.
class ConfigurationScreen extends StatefulWidget {
  const ConfigurationScreen({
    super.key,
    required this.agent,
    required this.onSaveFeatures,
  });

  /// The `/api/agent` payload: identity, compiled instruction, feature state.
  final Map<String, dynamic> agent;

  /// Called with the new `features:` block when the user changes a toggle.
  final Future<void> Function(Map<String, dynamic> features) onSaveFeatures;

  @override
  State<ConfigurationScreen> createState() => _ConfigurationScreenState();
}

enum _Card { brand, instruction, generation, features, retrieval, guardrails, skills, tools, environments }

class _ConfigurationScreenState extends State<ConfigurationScreen> {
  _Card? _selected;

  Map<String, dynamic> get _a =>
      (widget.agent['agent'] as Map<String, dynamic>?) ?? const {};
  Map<String, dynamic> get _compiled =>
      (widget.agent['compiled'] as Map<String, dynamic>?) ?? const {};
  Map<String, dynamic> get _features =>
      (widget.agent['features'] as Map<String, dynamic>?) ?? const {};
  Map<String, dynamic> _support(String name) {
    final runtime = (_features['runtime'] as Map<String, dynamic>?) ?? const {};
    for (final entry in (runtime['features'] ?? []) as List) {
      if (entry['name'] == name) return Map<String, dynamic>.from(entry as Map);
    }
    return {'status': 'unsupported', 'detail': 'Preview support has not been reported by the server.'};
  }
  List<Map<String, dynamic>> get _registry =>
      ((widget.agent['featureRegistry'] ?? []) as List)
          .cast<Map<String, dynamic>>();

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return LayoutBuilder(builder: (context, constraints) {
      final scale = MediaQuery.textScalerOf(context).scale(14) / 14;
      if (constraints.maxWidth < 800 * scale) {
        if (_selected == null) return _cardList(cs);
        return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Align(alignment: Alignment.centerLeft, child: TextButton.icon(
            onPressed: () => setState(() => _selected = null),
            icon: const Icon(Icons.arrow_back), label: const Text('All configuration'))),
          Expanded(child: _detail(cs)),
        ]);
      }
      return Row(children: [
        SizedBox(width: 320, child: _cardList(cs)),
        VerticalDivider(width: 1, color: cs.outlineVariant),
        Expanded(child: _selected == null ? _emptyState(cs) : _detail(cs)),
      ]);
    });
  }

  // ------------------------------------------------------------- card list

  Widget _cardList(ColorScheme cs) {
    final factory = widget.agent['mode'] == 'factory';
    final active = ((_features['active'] ?? []) as List).length;
    final total = _registry.length;
    final skills = ((widget.agent['skillCount'] ?? 0) as num).toInt();
    final tools = ((widget.agent['toolCount'] ?? 0) as num).toInt();

    return Container(
      color: cs.surface,
      child: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 18, 20, 12),
          child: Align(
            alignment: Alignment.centerLeft,
            child: Text('Configuration',
                style: TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w700,
                    color: cs.onSurface)),
          ),
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.only(bottom: 24),
            children: [
              _section(cs, 'Identity'),
              _card(cs, _Card.brand, Icons.palette_outlined, 'Brand',
                  'Agent name and model'),
              _card(cs, _Card.instruction, Icons.article_outlined,
                  'System prompt',
                  'Template, named slots, and the assembled prompt'),
              _card(cs, _Card.generation, Icons.psychology_outlined,
                  'Generation', 'Planner, thinking level, and output budget'),
              _section(cs, 'Behavior'),
              _card(cs, _Card.features, Icons.tune, 'Features',
                  '$active of $total selected'),
              _card(cs, _Card.retrieval, Icons.travel_explore, 'Retrieval',
                  'How skills are matched each turn'),
              _card(cs, _Card.guardrails, Icons.shield_outlined, 'Guardrails',
                  'Identity gating and refusal behavior'),
              _section(cs, 'Content'),
              _card(cs, _Card.skills, Icons.account_tree_outlined, 'Skills',
                  factory ? 'Factory owns skills · inspect in Project'
                      : skills > 0 ? '$skills in this agent' : 'Skills in this agent'),
              _card(cs, _Card.tools, Icons.handyman_outlined, 'Tools',
                  factory ? 'Factory owns tools · inspect in Project'
                      : tools > 0 ? '$tools in the catalog' : 'Catalog the agent binds from'),
              _section(cs, 'Deployment'),
              _card(cs, _Card.environments, Icons.settings_outlined,
                  'Environments', 'Per-environment skill overrides'),
            ],
          ),
        ),
      ]),
    );
  }

  Widget _section(ColorScheme cs, String label) => Padding(
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 6),
        child: Text(label.toUpperCase(),
            style: TextStyle(
                fontSize: 9.5,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.9,
                color: cs.onSurfaceVariant)),
      );

  Widget _card(ColorScheme cs, _Card id, IconData icon, String title,
      String subtitle) {
    final on = _selected == id;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 3),
      child: Material(
        color: on ? cs.surfaceContainerHigh : Colors.transparent,
        borderRadius: BorderRadius.circular(AdklTheme.rMd),
        child: InkWell(
          onTap: () => setState(() => _selected = id),
          borderRadius: BorderRadius.circular(AdklTheme.rMd),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 13),
            child: Row(children: [
              Icon(icon, size: 18, color: cs.onSurfaceVariant),
              const SizedBox(width: 13),
              Expanded(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(title,
                          style: TextStyle(
                              fontSize: 13,
                              fontWeight: FontWeight.w700,
                              color: cs.onSurface)),
                      const SizedBox(height: 2),
                      Text(subtitle,
                          style: TextStyle(
                              fontSize: 11.5, color: cs.onSurfaceVariant)),
                    ]),
              ),
              Icon(Icons.chevron_right, size: 16, color: cs.outline),
            ]),
          ),
        ),
      ),
    );
  }

  Widget _emptyState(ColorScheme cs) => Center(
        child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
          Container(
            width: 46,
            height: 46,
            decoration: BoxDecoration(
              color: cs.surfaceContainerHigh,
              borderRadius: BorderRadius.circular(AdklTheme.rLg),
            ),
            child: Icon(Icons.settings_outlined,
                size: 22, color: cs.onSurfaceVariant),
          ),
          const SizedBox(height: 14),
          Text('No configuration selected',
              style: TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                  color: cs.onSurface)),
          const SizedBox(height: 5),
          Text('Select a configuration from the list to view and edit it.',
              style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
        ]),
      );

  // ---------------------------------------------------------------- detail

  Widget _detail(ColorScheme cs) {
    final (title, body) = switch (_selected!) {
      _Card.brand => ('Brand', _identity(cs)),
      _Card.instruction => ('System prompt', _instruction(cs)),
      _Card.generation => ('Generation', _generation(cs)),
      _Card.features => ('Features', _featureList(cs)),
      _Card.retrieval => ('Retrieval', _retrieval(cs)),
      _Card.guardrails => ('Guardrails', _guardrails(cs)),
      _Card.environments => ('Environments', _environments(cs)),
      _Card.skills => ('Skills', _pointer(cs, 'Skills',
          'Edit skills in the Skills tab of the left rail. Each skill\'s '
          'description is what retrieval matches on, and its tool list is what '
          'gets bound when it is selected.')),
      _Card.tools => ('Tools', _pointer(cs, 'Tools',
          'Browse the catalog in the Tools tab of the left rail. The catalog is '
          'generated from the agent\'s registrations by `adkl catalog`; it is a '
          'build artifact, not a place to author.')),
    };

    return Column(children: [
      Container(
        height: 52,
        padding: const EdgeInsets.symmetric(horizontal: 24),
        decoration: BoxDecoration(
          border: Border(bottom: BorderSide(color: cs.outlineVariant)),
        ),
        child: Row(children: [
          Text(title,
              style: TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w700,
                  color: cs.onSurface)),
        ]),
      ),
      Expanded(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(24, 20, 24, 40),
          children: [body],
        ),
      ),
    ]);
  }

  Widget _identity(ColorScheme cs) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _readonlyField(cs, 'NAME', '${_a['name'] ?? '-'}', mono: true),
          const SizedBox(height: 16),
          _readonlyField(cs, 'MODEL', '${_a['model'] ?? '-'}', mono: true),
          const SizedBox(height: 18),
          _note(cs,
              'Identity lives in agent.yaml, which is the source of truth. This '
              'pane is read-only for now -- editing it here before the compiler '
              'exists would let the console and the file disagree.'),
        ],
      );

  Widget _instruction(ColorScheme cs) {
    if (_compiled['error'] != null) {
      return _note(cs, 'Could not compile: ${_compiled['error']}',
          tone: cs.failure);
    }
    final slots = (_compiled['slotsFilled'] as Map<String, dynamic>?) ?? {};
    final req = ((_compiled['requiredState'] ?? []) as List).cast<String>();
    final opt = ((_compiled['optionalState'] ?? []) as List).cast<String>();
    final pass = ((_compiled['passthrough'] ?? []) as List).cast<String>();

    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Wrap(spacing: 10, runSpacing: 10, children: [
        _stat(cs, '${_compiled['chars'] ?? 0}', 'characters'),
        _stat(cs, '~${_compiled['estTokens'] ?? 0}', 'est. tokens'),
        _stat(cs, '${slots.length}', 'slots filled'),
      ]),
      const SizedBox(height: 20),
      if (pass.isNotEmpty) ...[
        _note(cs,
            'These placeholders are not valid ADK state names, so they reach '
            'the model as literal text: ${pass.map((p) => '{$p}').join(', ')}',
            tone: cs.failure),
        const SizedBox(height: 16),
      ],
      _label(cs, 'SLOTS'),
      ...slots.entries.map((e) => Padding(
            padding: const EdgeInsets.only(bottom: 5),
            child: Row(children: [
              Icon(Icons.check_circle_outline, size: 13, color: cs.success),
              const SizedBox(width: 8),
              Expanded(
                child: Text(e.key,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontFamily: AdklTheme.mono,
                        fontSize: 11.5,
                        fontWeight: FontWeight.w600)),
              ),
              const SizedBox(width: 8),
              Text('${e.value} chars',
                  style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
            ]),
          )),
      const SizedBox(height: 18),
      _label(cs, 'STATE'),
      if (req.isNotEmpty)
        _kv(cs, 'Required', req.join(', '),
            hint: 'Absent at runtime raises a KeyError.'),
      if (opt.isNotEmpty)
        _kv(cs, 'Optional', opt.join(', '),
            hint: 'Absent renders as empty text.'),
      const SizedBox(height: 18),
      _label(cs, 'ASSEMBLED PROMPT'),
      Container(
        width: double.infinity,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: cs.surfaceContainerLow,
          borderRadius: BorderRadius.circular(AdklTheme.rMd),
          border: Border.all(color: cs.outlineVariant),
        ),
        child: SelectableText('${_compiled['text'] ?? ''}',
            style: const TextStyle(
                fontFamily: AdklTheme.mono, fontSize: 11, height: 1.55)),
      ),
    ]);
  }

  // -------------------------------------------------------------- features

  Widget _featureList(ColorScheme cs) {
    final active = ((_features['active'] ?? []) as List).cast<String>();
    final config = (_features['config'] as Map<String, dynamic>?) ?? {};
    final warnings = ((_features['warnings'] ?? []) as List).cast<String>();
    final error = _features['error'] as String?;

    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      if (error != null) ...[
        _note(cs, error, tone: cs.failure),
        const SizedBox(height: 16),
      ],
      for (final w in warnings) ...[
        _note(cs, w, tone: cs.attention),
        const SizedBox(height: 10),
      ],
      if (warnings.isNotEmpty) const SizedBox(height: 8),
      ..._registry.map((f) => _featureCard(cs, f, active, config)),
      const SizedBox(height: 8),
      _note(cs,
          'Selection records the requested pipeline. Preview support is shown '
          'separately; a selected feature without an implementation blocks chat. '
          'Implementation status does not verify your tools or agent outcomes.'),
    ]);
  }

  Widget _featureCard(ColorScheme cs, Map<String, dynamic> f,
      List<String> active, Map<String, dynamic> config) {
    final name = '${f['name']}';
    final on = active.contains(name);
    final stage = '${f['stage']}';
    final pos = active.indexOf(name);
    final opts = ((f['options'] ?? []) as List).cast<Map<String, dynamic>>();
    final cfg = (config[name] as Map<String, dynamic>?) ?? const {};
    final support = _support(name);

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      decoration: BoxDecoration(
        color: cs.surface,
        borderRadius: BorderRadius.circular(AdklTheme.rLg),
        border: Border.all(
            color: on ? cs.primary.withValues(alpha: 0.35) : cs.outlineVariant),
      ),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            if (on)
              Container(
                width: 20,
                height: 20,
                alignment: Alignment.center,
                decoration:
                    BoxDecoration(color: cs.primary, shape: BoxShape.circle),
                child: Text('${pos + 1}',
                    style: const TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.w700,
                        color: Colors.white)),
              )
            else
              Icon(Icons.circle_outlined, size: 20, color: cs.outline),
            const SizedBox(width: 11),
            Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Wrap(
                      spacing: 7,
                      runSpacing: 5,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        Text(name,
                            style: TextStyle(
                                fontFamily: AdklTheme.mono,
                                fontSize: 12.5,
                                fontWeight: FontWeight.w700,
                                color: cs.onSurface)),
                        _chip(cs, '${f['kind']}'.replaceAll('_', ' ')),
                        _chip(cs, 'preview: ${support['status']}',
                            tone: support['status'] == 'implemented' ? cs.info : cs.attention),
                        if (stage != 'stable')
                          _chip(cs, stage, tone: cs.attention),
                        if (f['mayShortCircuit'] == true)
                          Tooltip(
                            message: 'When this fires it halts its hook, so '
                                'later features -- and any hand-written callback '
                                'on the same hook -- do not run.',
                            child: _chip(cs, 'short-circuits',
                                tone: cs.attention),
                          ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text('${f['summary']}',
                        style: TextStyle(
                            fontSize: 11.5,
                            height: 1.45,
                            color: cs.onSurfaceVariant)),
                  ]),
            ),
            const SizedBox(width: 10),
            Switch(
              value: on,
              onChanged: on || support['status'] != 'unsupported'
                  ? (v) => _toggle(name, v) : null,
            ),
          ]),
          const SizedBox(height: 8),
          Text('${support['detail']}',
              style: TextStyle(fontSize: 11.5, height: 1.45, color: cs.onSurfaceVariant)),
          if (on && opts.isNotEmpty) ...[
            const SizedBox(height: 12),
            Divider(height: 1, color: cs.outlineVariant),
            const SizedBox(height: 10),
            ...opts.map((o) => Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        SizedBox(
                          width: 165,
                          child: Text('${o['key']}',
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                  fontFamily: AdklTheme.mono, fontSize: 11)),
                        ),
                        SizedBox(
                          width: 62,
                          child: Text('${cfg[o['key']] ?? o['default']}',
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                  fontFamily: AdklTheme.mono,
                                  fontSize: 11,
                                  fontWeight: FontWeight.w700,
                                  color: cs.onSurface)),
                        ),
                        Expanded(
                          child: Text('${o['help']}',
                              style: TextStyle(
                                  fontSize: 10.5,
                                  height: 1.4,
                                  color: cs.onSurfaceVariant)),
                        ),
                      ]),
                )),
          ],
        ]),
      ),
    );
  }

  Future<void> _toggle(String name, bool on) async {
    final active = ((_features['active'] ?? []) as List).cast<String>().toList();
    // 'declaredConfig' is the raw block as written; 'config' is defaults-merged
    // and has `enabled` stripped. Writing 'config' back would launder every
    // inherited default into a declared one -- freezing this project against
    // later registry defaults -- and erase any `enabled: false` kill switch,
    // which is an operator control this editor has no business touching. A
    // toggle changes the pipeline and nothing else.
    final declared =
        (_features['declaredConfig'] as Map<String, dynamic>?) ?? const {};
    if (on) {
      if (!active.contains(name)) active.add(name);
    } else {
      active.remove(name);
    }
    // Send declared config untouched plus the new pipeline, so the server's
    // registry re-validates ordering before anything is written.
    final block = <String, dynamic>{
      ...declared,
      'pipeline': active,
    };
    await widget.onSaveFeatures(block);
  }

  /// Planner and output budget. The prospect agent sets BuiltInPlanner with
  /// thinking_level=LOW and max_output_tokens=8192, and the reason for the floor
  /// is worth surfacing: thinking models spend the budget on internal reasoning
  /// and return empty content without it.
  Widget _generation(ColorScheme cs) {
    final g = (widget.agent['generation'] as Map<String, dynamic>?) ?? const {};
    if (g.isEmpty) {
      return _note(cs,
          'No generation block in agent.yaml. Without one the agent uses ADK '
          'defaults: no planner, and no explicit output budget. A thinking model '
          'can exhaust a small budget on internal reasoning and return empty '
          'content, so an explicit floor is worth setting.');
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      ...g.entries.map((e) => _kv(cs, e.key, '${e.value}')),
      const SizedBox(height: 16),
      _note(cs,
          'thinking_level trades latency for reasoning depth. include_thoughts '
          'surfaces the model\'s reasoning in the response stream, which is '
          'useful in a console and usually wrong in production.'),
    ]);
  }

  /// Sierra splits guardrails into a global action plus per-category overrides.
  /// Ours is narrower: the real prospect agent's guardrail is an identity gate
  /// that refuses model-supplied account identifiers while unauthenticated.
  Widget _guardrails(ColorScheme cs) {
    final cfg = (_features['config'] as Map<String, dynamic>?) ?? const {};
    final gate = (cfg['identity_gate'] as Map<String, dynamic>?) ?? const {};
    final active = ((_features['active'] ?? []) as List).contains('identity_gate');
    final support = _support('identity_gate');
    final supported = support['status'] == 'implemented';

    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Icon(Icons.shield_outlined,
            size: 16, color: active ? cs.attention : cs.onSurfaceVariant),
        const SizedBox(width: 8),
        Flexible(child: Text(active
            ? supported ? 'Identity gate configured for preview' : 'Identity gate selected; preview blocked'
            : 'Identity gate is off',
            style: TextStyle(
                fontSize: 13, fontWeight: FontWeight.w700, color: cs.onSurface))),
      ]),
      const SizedBox(height: 6),
      Text('${support['detail']}',
          style: TextStyle(fontSize: 11.5, height: 1.5,
              color: cs.onSurfaceVariant)),
      const SizedBox(height: 16),
      if (gate.isNotEmpty)
        ...gate.entries.map((e) => _kv(cs, e.key, '${e.value}')),
      const SizedBox(height: 16),
      _note(cs,
          'Pipeline selection does not prove identity enforcement. Each tool '
          'must authorize access using trusted identity and object permissions.',
          tone: cs.attention),
    ]);
  }

  /// Per-environment skill overrides. The real repo swaps skills by name for
  /// uat via a Python dict; here it is data, and validation runs per environment.
  Widget _environments(ColorScheme cs) {
    final envs = ((widget.agent['environments'] ?? []) as List).cast<String>();
    if (envs.isEmpty) {
      return _note(cs,
          'No environment overrides. Add skills.overrides.<env> in agent.yaml to '
          'swap a skill per environment -- validation then runs per environment, '
          'so a skill can pass in prod and fail in uat.');
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _label(cs, 'ENVIRONMENTS'),
      ...envs.map((e) => Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Row(children: [
              Icon(Icons.layers_outlined, size: 14, color: cs.onSurfaceVariant),
              const SizedBox(width: 8),
              Text(e, style: const TextStyle(
                  fontFamily: AdklTheme.mono, fontSize: 12,
                  fontWeight: FontWeight.w600)),
            ]),
          )),
      const SizedBox(height: 14),
      _note(cs,
          'Switch environment from the top bar. The skill list, its lint '
          'findings and the compiled prompt are all re-resolved, because an '
          'override can change any of them.'),
    ]);
  }

  Widget _retrieval(ColorScheme cs) {
    final r = (widget.agent['retrieval'] as Map<String, dynamic>?) ?? const {};
    if (r.isEmpty) {
      return _note(cs, 'No retrieval block in agent.yaml.');
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      ...r.entries.map((e) => _kv(cs, e.key, '${e.value}')),
      const SizedBox(height: 16),
      _note(cs,
          'Retrieval decides which skills are visible each turn, and therefore '
          'which tools are bound. Widening top_k costs tokens on every turn; '
          'narrowing it risks missing the right skill.'),
    ]);
  }

  // ---------------------------------------------------------------- shared

  Widget _pointer(ColorScheme cs, String title, String text) =>
      _note(cs, text);

  Widget _label(ColorScheme cs, String text) => Padding(
        padding: const EdgeInsets.only(bottom: 9),
        child: Text(text,
            style: TextStyle(
                fontSize: 10.5,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.9,
                color: cs.onSurfaceVariant)),
      );

  Widget _readonlyField(ColorScheme cs, String label, String value,
          {bool mono = false}) =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        _label(cs, label),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
          decoration: BoxDecoration(
            color: cs.surfaceContainerLow,
            borderRadius: BorderRadius.circular(AdklTheme.rMd),
            border: Border.all(color: cs.outlineVariant),
          ),
          child: Text(value,
              style: TextStyle(
                  fontFamily: mono ? AdklTheme.mono : null,
                  fontSize: 12.5,
                  color: cs.onSurface)),
        ),
      ]);

  Widget _kv(ColorScheme cs, String k, String v, {String? hint}) => Padding(
        padding: const EdgeInsets.only(bottom: 9),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(
            width: 150,
            child: Text(k,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                    fontFamily: AdklTheme.mono,
                    fontSize: 11.5,
                    color: cs.onSurfaceVariant)),
          ),
          Expanded(
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(v,
                      style: TextStyle(
                          fontFamily: AdklTheme.mono,
                          fontSize: 11.5,
                          fontWeight: FontWeight.w600,
                          color: cs.onSurface)),
                  if (hint != null)
                    Text(hint,
                        style: TextStyle(
                            fontSize: 10.5, color: cs.onSurfaceVariant)),
                ]),
          ),
        ]),
      );

  Widget _stat(ColorScheme cs, String value, String label) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(
          color: cs.surfaceContainerLow,
          borderRadius: BorderRadius.circular(AdklTheme.rMd),
          border: Border.all(color: cs.outlineVariant),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(value,
              style: TextStyle(
                  fontSize: 16,
                  fontWeight: FontWeight.w700,
                  color: cs.onSurface)),
          Text(label,
              style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
        ]),
      );

  Widget _chip(ColorScheme cs, String text, {Color? tone}) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
        decoration: BoxDecoration(
          color: tone == null
              ? cs.surfaceContainerHigh
              : cs.attentionContainer.withValues(alpha: 0.55),
          borderRadius: BorderRadius.circular(4),
        ),
        child: Text(text,
            style: TextStyle(
                fontSize: 9.5,
                fontWeight: FontWeight.w700,
                color: tone ?? cs.onSurfaceVariant)),
      );

  Widget _note(ColorScheme cs, String text, {Color? tone}) => Container(
        width: double.infinity,
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: tone == null
              ? cs.surfaceContainerLow
              : (tone == cs.failure
                  ? cs.failureContainer.withValues(alpha: 0.45)
                  : cs.attentionContainer.withValues(alpha: 0.45)),
          borderRadius: BorderRadius.circular(AdklTheme.rMd),
          border: Border.all(
              color: tone == null
                  ? cs.outlineVariant
                  : tone.withValues(alpha: 0.35)),
        ),
        child: Text(text,
            style: TextStyle(
                fontSize: 11.5,
                height: 1.55,
                color: tone == null ? cs.onSurfaceVariant : cs.onSurface)),
      );
}
