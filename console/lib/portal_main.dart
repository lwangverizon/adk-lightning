import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'api.dart';
import 'diagnostics.dart';
import 'rail.dart';
import 'section_editor.dart';
import 'theme.dart';
import 'tool_detail.dart';
import 'workflow_graph.dart';
import 'portal_reload_stub.dart'
    if (dart.library.js_interop) 'portal_reload_web.dart'
    as browser;

const portalBuild = String.fromEnvironment(
  'ADKL_PORTAL_BUILD',
  defaultValue: 'development',
);

/// Shown where the original console shows its read-only native-skill note.
const readOnlyNotice =
    'Read-only artifact inspection · Not live production evidence · '
    'Chat and editing unavailable';

class SnapshotMissing implements Exception {
  const SnapshotMissing();
}

void main() => runApp(
  MaterialApp(
    title: 'adk-lightning · Inspect',
    debugShowCheckedModeBanner: false,
    theme: AdklTheme.light(),
    darkTheme: AdklTheme.dark(),
    home: const PortalShell(),
  ),
);

/// Only static same-origin GETs. No AdklApi instance or mutation transport.
class PortalApi {
  PortalApi({http.Client? client}) : _client = client ?? http.Client();
  final http.Client _client;
  Future<Map<String, dynamic>> load(String filename) async {
    if (!RegExp(r'^[a-z0-9_-]+\.json$').hasMatch(filename)) {
      throw const FormatException('Invalid snapshot filename');
    }
    final response = await _client
        .get(
          Uri.parse('data/$filename'),
          headers: {'Cache-Control': 'no-cache'},
        )
        .timeout(const Duration(seconds: 20));
    if (response.statusCode == 404) throw const SnapshotMissing();
    if (response.statusCode != 200 ||
        response.bodyBytes.length > 16 * 1024 * 1024) {
      throw const FormatException('Snapshot unavailable');
    }
    final data =
        jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
    if (data['schemaVersion'] != 1) {
      throw const FormatException('Unsupported snapshot version');
    }
    return data;
  }

  void close() => _client.close();
}

/// The original console shell (top bar, library rail, skill workspace and the
/// Checks panel) without chat, project jobs, saving, editing or Doctor runs.
class PortalShell extends StatefulWidget {
  const PortalShell({super.key, this.api});
  final PortalApi? api;
  @override
  State<PortalShell> createState() => _PortalShellState();
}

class _PortalShellState extends State<PortalShell> {
  late final api = widget.api ?? PortalApi();
  final scaffold = GlobalKey<ScaffoldState>();
  List<Map<String, dynamic>> agents = [];
  Map<String, dynamic>? bundle, skillRow, toolRow, workflowRow;
  String? selected, error, servedBuild;
  bool recovered = false;
  bool loading = true, raw = false, attention = false;
  // Supporting panel: shown beside the workspace on wide layouts, or as the
  // Findings destination on compact layouts, exactly like the original.
  bool supportOpen = true, supportVisible = false, findingsAll = true;
  double supportWidth = 360;
  int generation = 0, tab = 0;
  String query = '';

  /// The editor console's selection model: one selection per collection,
  /// kept across rail tabs. A selected tool takes the pane on the Tools tab;
  /// the skill stays selected underneath, so going back is one click.
  int get view => tab == 1 && toolRow == null ? 0 : tab;
  Map<String, dynamic>? get selectedRow =>
      [skillRow, toolRow, workflowRow][view];

  Map<String, dynamic> get inventory => bundle!['inventory'];
  Map<String, dynamic> get report => bundle!['report'];
  List<Map<String, dynamic>> collection(String key) =>
      (inventory[key] as List? ?? []).cast<Map<String, dynamic>>();
  List<Finding> get findings =>
      (report['findings'] as List).map(Finding.fromJson).toList();
  List<SkillSummary> get skills => collection('skills')
      .map(
        (s) => SkillSummary(
          name: s['name'],
          path: s['owner'],
          overridden: false,
          description: s['description'] ?? '',
          tools: (s['tools'] as List).cast<String>(),
          estTokens: (s['instructions'] as String? ?? '').length ~/ 4,
          errors: findings
              .where((f) => f.skill == s['name'] && f.isError)
              .length,
          warnings: findings
              .where((f) => f.skill == s['name'] && !f.isError)
              .length,
        ),
      )
      .toList();

  /// Same schema precedence as the original console's native catalog: ADK 2.x
  /// declarations publish `parameters_json_schema`, older ones `parameters`.
  static String _parameterType(Map value) {
    final type = value['type'];
    if (type is List) return type.join(' | ');
    if (type != null) return '$type';
    return value['anyOf'] != null ? 'union' : 'schema';
  }

  ToolInfo tool(Map<String, dynamic> row) {
    final declaration = row['declaration'] as Map? ?? {};
    final schema =
        declaration['parameters_json_schema'] as Map? ??
        declaration['parameters'] as Map?;
    final parameters = schema ?? {};
    final props = parameters['properties'] as Map? ?? {};
    final required = parameters['required'] as List? ?? [];
    return ToolInfo(
      name: row['name'],
      summary: row['description'] ?? '',
      sideEffect: 'unknown',
      sideEffectInferred: false,
      requiresAuth: false,
      estTokens: jsonEncode(declaration).length ~/ 4,
      source: 'native_factory',
      scope: row['scope']?.toString() ?? 'unknown',
      schemaKnown: schema != null,
      params: props.entries
          .map(
            (e) => ParamInfo(
              '${e.key}',
              e.value is Map ? _parameterType(e.value as Map) : 'schema',
              required.contains(e.key),
              '${e.value is Map ? e.value['description'] ?? '' : ''}',
            ),
          )
          .toList(),
    );
  }

  List<NativeWorkflow> get workflows =>
      collection('workflows').map(NativeWorkflow.fromJson).toList();

  @override
  void initState() {
    super.initState();
    _registry();
  }

  @override
  void dispose() {
    if (widget.api == null) api.close();
    super.dispose();
  }

  Future<void> _registry() async {
    final previous = selected;
    final request = ++generation;
    setState(() {
      loading = true;
      bundle = null;
      skillRow = toolRow = workflowRow = null;
      error = null;
      agents = [];
      selected = null;
      recovered = false;
      servedBuild = null;
    });
    try {
      final value = await api.load('registry.json');
      if (!mounted || request != generation) return;
      setState(() {
        agents = (value['agents'] as List).cast<Map<String, dynamic>>();
        servedBuild = value['uiBuild'] as String?;
        loading = false;
      });
      if (agents.isNotEmpty) {
        await _select(
          agents.any((a) => a['id'] == previous)
              ? previous!
              : agents.first['id'],
        );
      }
    } catch (_) {
      if (mounted && request == generation) {
        setState(() {
          loading = false;
          error = 'Cannot load agent registry.';
        });
      }
    }
  }

  Future<Map<String, dynamic>?> _snapshot(
    String id,
    String filename,
    int request,
  ) async {
    try {
      return await api.load(filename);
    } on SnapshotMissing {
      // Exactly one registry refresh and one retry: no polling or reload loops.
      if (!mounted || request != generation) return null;
      final registry = await api.load('registry.json');
      if (!mounted || request != generation) return null;
      final entries = (registry['agents'] as List).cast<Map<String, dynamic>>();
      final matches = entries.where((a) => a['id'] == id).toList();
      setState(() {
        agents = entries;
        servedBuild = registry['uiBuild'] as String?;
      });
      if (matches.length != 1 || matches.single['snapshot'] == null) {
        throw const SnapshotMissing();
      }
      final value = await api.load(matches.single['snapshot']);
      if (!mounted || request != generation) return null;
      setState(() => recovered = true);
      return value;
    }
  }

  Future<void> _select(String id) async {
    final request = ++generation;
    final entry = agents.singleWhere((a) => a['id'] == id);
    setState(() {
      selected = id;
      bundle = null;
      skillRow = toolRow = workflowRow = null;
      error = null;
      tab = 0;
      query = '';
      raw = false;
      attention = false;
      recovered = false;
      supportVisible = false;
      findingsAll = true;
      loading = entry['snapshot'] != null;
    });
    if (entry['snapshot'] == null) return;
    try {
      final value = await _snapshot(id, entry['snapshot'], request);
      if (value == null || !mounted || request != generation) return;
      if (value['kind'] != 'adkl_static_inspection' ||
          value['agent']['id'] != id ||
          value['report']['findings'] is! List ||
          value['report']['checks'] is! List ||
          value['inventory']['skills'] is! List ||
          value['inventory']['tools'] is! List ||
          value['identity'] is! Map) {
        throw const FormatException('Invalid snapshot');
      }
      for (final row in value['inventory']['workflows'] as List? ?? []) {
        NativeWorkflow.fromJson(row);
      }
      if (mounted && request == generation) {
        setState(() {
          bundle = value;
          loading = false;
          // Same opening selection as the editor: first skill, else workflow.
          final rows = collection('skills');
          final flows = collection('workflows');
          skillRow = rows.isEmpty ? null : rows.first;
          workflowRow = skillRow == null && flows.isNotEmpty
              ? flows.first
              : null;
          if (workflowRow != null) tab = 2;
          raw = _structured(skillRow) == null;
        });
      }
    } on SnapshotMissing {
      if (mounted && request == generation) {
        setState(() {
          loading = false;
          error =
              'Publication changed while loading this agent. Retry after the '
              'rollout settles. No previous agent data is shown.';
        });
      }
    } catch (_) {
      if (mounted && request == generation) {
        setState(() {
          loading = false;
          error =
              'Snapshot unavailable or invalid. No previous agent data is shown.';
        });
      }
    }
  }

  /// Parsed sections, or null when the original would fall back to Raw.
  SkillBody? _structured(Map<String, dynamic>? row) {
    if (row == null) return null;
    final doc = SkillBody.fromJson((row['doc'] as Map<String, dynamic>?) ?? {});
    return doc.sections.isEmpty ? null : doc;
  }

  void open(int kind, Map<String, dynamic> row) {
    setState(() {
      tab = kind;
      if (kind == 0) {
        skillRow = row;
        toolRow = workflowRow = null;
        // Same default as the original editor: Sections unless unparsed.
        raw = _structured(row) == null;
      } else if (kind == 1) {
        toolRow = row;
        workflowRow = null;
      } else {
        workflowRow = row;
        toolRow = null;
      }
      supportVisible = false;
    });
    scaffold.currentState?.closeDrawer();
  }

  void openNamed(int kind, String name) {
    final key = ['skills', 'tools', 'workflows'][kind];
    final matches = collection(key)
        .where(
          (r) => kind == 2
              ? '${r['agent']}:${r['name']}' == name
              : r['name'] == name,
        )
        .toList();
    if (matches.length == 1) {
      open(kind, matches.single);
      return;
    }
    // Never silently resolve duplicate tool names to the wrong owner.
    showDialog<void>(
      context: context,
      builder: (context) => SimpleDialog(
        title: const Text('Select declaration owner'),
        children: [
          for (final row in matches)
            SimpleDialogOption(
              onPressed: () {
                Navigator.pop(context);
                open(kind, row);
              },
              child: Text('${row['owner']}'),
            ),
        ],
      ),
    );
  }

  Widget library() => NavRail(
    skills: skills,
    tools: collection('tools').map(tool).toList(),
    workflows: workflows,
    tab: tab,
    // As in the editor: the query and every selection survive tab switches.
    onTab: (value) => setState(() => tab = value),
    selectedSkill: skillRow?['name'],
    selectedTool: toolRow?['name'],
    selectedWorkflow: workflowRow == null
        ? null
        : '${workflowRow!['agent']}:${workflowRow!['name']}',
    onSkill: (name) => openNamed(0, name),
    onTool: (name) => openNamed(1, name),
    onWorkflow: (name) => openNamed(2, name),
    query: query,
    onQuery: (value) => setState(() => query = value),
    env: null,
    findings: findings,
    authoritativeFindings: true,
    // "Edits pending" is editor wording; export freshness is shown in Checks.
    findingsStale: false,
    scopeLabel: 'Published declarations · not live execution',
    needsAttention: attention,
    onNeedsAttention: (value) => setState(() => attention = value),
  );

  // -------------------------------------------------------------------- build

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return LayoutBuilder(
      builder: (context, constraints) {
        // The original console's width budgets, including enlarged text.
        final scale = MediaQuery.textScalerOf(context).scale(14) / 14;
        final wide = constraints.maxWidth >= 1400 * scale.clamp(1, 1.5);
        final libraryVisible =
            constraints.maxWidth >= 1100 * scale.clamp(1, 1.5);
        final ready = bundle != null;
        final workspace = _content(cs);
        final support = _sidePanel(cs);
        return Scaffold(
          key: scaffold,
          drawer: !libraryVisible && ready
              ? Drawer(
                  width: constraints.maxWidth.clamp(0, 360),
                  child: SafeArea(
                    child: Column(
                      children: [
                        ListTile(
                          title: const Text('Agent library'),
                          trailing: IconButton(
                            tooltip: 'Close library',
                            icon: const Icon(Icons.close),
                            onPressed: () =>
                                scaffold.currentState?.closeDrawer(),
                          ),
                        ),
                        Expanded(child: library()),
                      ],
                    ),
                  ),
                )
              : null,
          bottomNavigationBar: wide || !ready
              ? null
              : NavigationBar(
                  selectedIndex: supportVisible ? 1 : 0,
                  onDestinationSelected: (index) =>
                      setState(() => supportVisible = index == 1),
                  destinations: const [
                    NavigationDestination(
                      icon: Icon(Icons.edit_note),
                      label: 'Skills',
                    ),
                    NavigationDestination(
                      icon: Icon(Icons.fact_check_outlined),
                      label: 'Findings',
                    ),
                  ],
                ),
          body: SafeArea(
            child: Column(
              children: [
                _topBar(cs, wide: wide, libraryVisible: libraryVisible),
                ..._notices(cs),
                if (loading) const LinearProgressIndicator(minHeight: 2),
                Expanded(
                  child: !ready
                      ? _unavailable(cs)
                      : Row(
                          children: [
                            if (libraryVisible)
                              SizedBox(width: 280, child: library()),
                            Expanded(
                              child: wide
                                  ? Row(
                                      children: [
                                        Expanded(child: workspace),
                                        if (supportOpen) ...[
                                          _resizeHandle(cs),
                                          SizedBox(
                                            width: supportWidth,
                                            child: support,
                                          ),
                                        ],
                                      ],
                                    )
                                  : IndexedStack(
                                      index: supportVisible ? 1 : 0,
                                      children: [
                                        ExcludeFocus(
                                          excluding: supportVisible,
                                          child: workspace,
                                        ),
                                        ExcludeFocus(
                                          excluding: !supportVisible,
                                          child: support,
                                        ),
                                      ],
                                    ),
                            ),
                          ],
                        ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _resizeHandle(ColorScheme cs) => MouseRegion(
    cursor: SystemMouseCursors.resizeColumn,
    child: GestureDetector(
      onHorizontalDragUpdate: (details) => setState(() {
        supportWidth = (supportWidth - details.delta.dx).clamp(320, 440);
      }),
      child: Semantics(
        label: 'Resize supporting panel',
        onIncrease: () =>
            setState(() => supportWidth = (supportWidth + 20).clamp(320, 440)),
        onDecrease: () =>
            setState(() => supportWidth = (supportWidth - 20).clamp(320, 440)),
        child: Container(width: 8, color: cs.surfaceContainerHigh),
      ),
    ),
  );

  Widget _topBar(
    ColorScheme cs, {
    required bool wide,
    required bool libraryVisible,
  }) => Material(
    color: cs.surfaceContainerLow,
    child: ConstrainedBox(
      constraints: const BoxConstraints(minHeight: 56),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8),
        child: Row(
          children: [
            if (!libraryVisible && bundle != null)
              IconButton(
                tooltip: 'Open library',
                icon: const Icon(Icons.menu),
                onPressed: () => scaffold.currentState?.openDrawer(),
              ),
            const Icon(Icons.bolt, size: 24),
            const SizedBox(width: 8),
            Expanded(child: _agentPicker(cs)),
            if (wide) ...[
              _topTab(cs, Icons.edit_note, 'Skills'),
              IconButton(
                tooltip: supportOpen
                    ? 'Hide supporting panel'
                    : 'Show supporting panel',
                onPressed: bundle == null
                    ? null
                    : () => setState(() => supportOpen = !supportOpen),
                icon: const Icon(Icons.view_sidebar_outlined),
              ),
            ],
            PopupMenuButton<String>(
              tooltip: 'Workspace actions',
              onSelected: (action) {
                if (action == 'details') _overview();
                if (action == 'registry') _registry();
                if (action == 'page') browser.reload();
              },
              itemBuilder: (_) => [
                PopupMenuItem(
                  value: 'details',
                  enabled: bundle != null,
                  child: const Text('Artifact and check coverage'),
                ),
                PopupMenuItem(
                  value: 'registry',
                  enabled: !loading,
                  child: const Text('Reload publication'),
                ),
                const PopupMenuItem(value: 'page', child: Text('Reload page')),
              ],
            ),
          ],
        ),
      ),
    ),
  );

  /// The original's selected top-level section, without the Project tab.
  Widget _topTab(ColorScheme cs, IconData icon, String label) => Semantics(
    selected: true,
    child: Container(
      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
      decoration: BoxDecoration(
        color: cs.surfaceContainerHigh,
        borderRadius: BorderRadius.circular(AdklTheme.rSm),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: cs.onSurface),
          const SizedBox(width: 6),
          Text(
            label,
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w700,
              color: cs.onSurface,
            ),
          ),
        ],
      ),
    ),
  );

  Widget _agentPicker(ColorScheme cs) {
    final name =
        agents
            .where((a) => a['id'] == selected)
            .map((a) => a['name'] as String)
            .firstOrNull ??
        'ADKL Inspect';
    final label = TextStyle(
      fontFamily: AdklTheme.mono,
      fontSize: 11.5,
      color: cs.onSurfaceVariant,
    );
    if (agents.length < 2) {
      return Text(
        name,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: label,
      );
    }
    return Align(
      alignment: Alignment.centerLeft,
      child: PopupMenuButton<String>(
        tooltip: 'Switch agent',
        onSelected: _select,
        position: PopupMenuPosition.under,
        itemBuilder: (_) => [
          for (final agent in agents)
            PopupMenuItem<String>(
              value: agent['id'],
              child: Row(
                children: [
                  Icon(
                    agent['id'] == selected
                        ? Icons.radio_button_checked
                        : Icons.radio_button_off,
                    size: 13,
                    color: agent['id'] == selected
                        ? cs.primary
                        : cs.onSurfaceVariant,
                  ),
                  const SizedBox(width: 9),
                  Flexible(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Text(
                          agent['name'],
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        Text(
                          agent['snapshot'] == null
                              ? '${agent['id']}  ·  no snapshot published'
                              : '${agent['id']}  ·  published snapshot',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            fontSize: 9.5,
                            color: cs.onSurfaceVariant,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
        ],
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Flexible(
              child: Text(
                name,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: label,
              ),
            ),
            const SizedBox(width: 3),
            Icon(Icons.arrow_drop_down, size: 16, color: cs.onSurfaceVariant),
          ],
        ),
      ),
    );
  }

  /// Release-transition notices only when they apply; no permanent banners.
  List<Widget> _notices(ColorScheme cs) {
    final mismatch = servedBuild != null && servedBuild != portalBuild;
    if (!mismatch && !recovered) return const [];
    return [
      Container(
        width: double.infinity,
        padding: const EdgeInsets.fromLTRB(16, 4, 8, 4),
        decoration: BoxDecoration(
          color: cs.attentionContainer.withValues(alpha: 0.35),
          border: Border(bottom: BorderSide(color: cs.outlineVariant)),
        ),
        child: Wrap(
          spacing: 12,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Icon(Icons.info_outline, size: 14, color: cs.onSurfaceVariant),
            if (mismatch)
              const Text(
                'A different UI build is being served. Reload to update.',
                style: TextStyle(fontSize: 11.5),
              ),
            if (recovered)
              const Text(
                'Publication refreshed after a rollout.',
                style: TextStyle(fontSize: 11.5),
              ),
            if (mismatch)
              TextButton(
                onPressed: browser.reload,
                child: const Text('Reload page'),
              ),
          ],
        ),
      ),
    ];
  }

  /// The original console's error card, for registry and snapshot states.
  Widget _unavailable(ColorScheme cs) {
    if (loading && error == null) {
      return Center(
        child: Text(
          'Loading inspection…',
          style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant),
        ),
      );
    }
    final failed = error != null;
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 460),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Card.outlined(
            child: Padding(
              padding: const EdgeInsets.all(22),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    failed ? Icons.cloud_off : Icons.inventory_2_outlined,
                    size: 28,
                    color: failed ? cs.failure : cs.onSurfaceVariant,
                  ),
                  const SizedBox(height: 12),
                  Text(
                    failed ? 'Cannot load the inspection' : 'Nothing published',
                    style: TextStyle(
                      fontSize: 15,
                      fontWeight: FontWeight.w600,
                      color: cs.onSurface,
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    error ?? 'No inspection snapshot published for this agent.',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontSize: 11.5,
                      color: cs.onSurfaceVariant,
                    ),
                  ),
                  if (failed) ...[
                    const SizedBox(height: 16),
                    FilledButton(
                      onPressed: loading ? null : _registry,
                      child: const Text('Retry publication'),
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  // ---------------------------------------------------------------- workspace

  Widget _content(ColorScheme cs) {
    if (bundle == null) return const SizedBox.shrink();
    if (selectedRow == null) {
      return Center(
        child: Text(
          'Select a ${['skill', 'tool', 'workflow'][view]}',
          style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant),
        ),
      );
    }
    final row = selectedRow!;
    final Widget child;
    if (view == 1) {
      child = ToolDetail(
        tool: tool(row),
        skills: skills.where((s) => s.path == row['owner']).toList(),
        onOpenSkill: (name) {
          final match = collection(
            'skills',
          ).singleWhere((s) => s['name'] == name && s['owner'] == row['owner']);
          open(0, match);
        },
      );
    } else if (view == 2) {
      child = _workflowDetail(cs, row);
    } else {
      child = _skill(cs, row);
    }
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 1120),
        child: child,
      ),
    );
  }

  Widget _workflowDetail(ColorScheme cs, Map<String, dynamic> row) => ListView(
    padding: const EdgeInsets.fromLTRB(28, 24, 28, 48),
    children: [
      Text(row['name'], style: Theme.of(context).textTheme.headlineSmall),
      const SizedBox(height: 12),
      Text(
        'Read-only native workflow declaration',
        style: TextStyle(color: cs.onSurfaceVariant),
      ),
      const SizedBox(height: 24),
      Text('DESCRIPTION', style: Theme.of(context).textTheme.labelMedium),
      const SizedBox(height: 8),
      SelectableText(row['description']),
      const SizedBox(height: 24),
      WorkflowGraphView(graph: NativeWorkflow.fromJson(row).graph),
      for (final nested in row['nestedWorkflows'] as List? ?? []) ...[
        const SizedBox(height: 24),
        Text(
          'Delegated workflow: ${nested['name']}',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        Text(
          'Declared target of ${nested['entryNode']}',
          style: TextStyle(color: cs.onSurfaceVariant),
        ),
        const SizedBox(height: 12),
        WorkflowGraphView(graph: NativeWorkflowGraph.fromJson(nested['graph'])),
      ],
      const SizedBox(height: 24),
      Text('Agent: ${row['agent']}'),
      Text('Native operation: ${row['operation']}'),
      Text('Exposure: ${row['exposure'] ?? 'declared'}'),
      const SizedBox(height: 16),
      Text(
        'Declared topology only. No runtime traversal or execution is shown.',
        style: TextStyle(color: cs.onSurfaceVariant),
      ),
    ],
  );

  /// The original skill editor, rendered read-only: same header, fields,
  /// tool list and Sections / Raw switch; no Save, tool picker or unbind.
  Widget _skill(ColorScheme cs, Map<String, dynamic> row) {
    final body = row['instructions'] as String?;
    final tokens = (body ?? '').length ~/ 4;
    final key = '$selected-${row['owner']}-${row['name']}';
    return Column(
      children: [
        Container(
          padding: const EdgeInsets.fromLTRB(28, 16, 20, 14),
          decoration: BoxDecoration(
            border: Border(bottom: BorderSide(color: cs.outlineVariant)),
          ),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      row['name'],
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.headlineSmall,
                    ),
                    if (row['owner'] != null)
                      Text(
                        '${row['owner']}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: 11,
                          color: cs.onSurfaceVariant,
                        ),
                      ),
                  ],
                ),
              ),
              const SizedBox(width: 12),
              if (MediaQuery.sizeOf(context).width >= 600)
                _tokenPill(cs, tokens),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.all(12),
          child: Text(
            readOnlyNotice,
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
          ),
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(28, 10, 28, 48),
            children: [
              _field(
                cs,
                label: 'DESCRIPTION',
                hint:
                    'When retrieval should select this skill. Trigger phrases '
                    'and exclusions.',
                child: ReadOnlyField(
                  key: ValueKey('description-$key'),
                  text: row['description'] ?? '',
                  label: 'Skill description',
                  minLines: 4,
                  style: const TextStyle(
                    fontSize: AdklTheme.fsBody,
                    height: 1.65,
                  ),
                ),
              ),
              const SizedBox(height: 28),
              _toolsField(cs, row),
              const SizedBox(height: 28),
              _instructions(cs, row, key),
            ],
          ),
        ),
      ],
    );
  }

  Widget _instructions(ColorScheme cs, Map<String, dynamic> row, String key) {
    final body = row['instructions'] as String?;
    final doc = _structured(row);
    final showRaw = raw || doc == null;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _label(cs, 'INSTRUCTIONS'),
                  const SizedBox(height: 3),
                  Text(
                    showRaw
                        ? 'Raw markdown, exactly as published.'
                        : 'One field per section, parsed from the published '
                              'body.',
                    style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant),
                  ),
                ],
              ),
            ),
            SegmentedButton<bool>(
              showSelectedIcon: false,
              segments: const [
                ButtonSegment(value: false, label: Text('Sections')),
                ButtonSegment(value: true, label: Text('Raw')),
              ],
              selected: {showRaw},
              onSelectionChanged: (v) => setState(() => raw = v.single),
            ),
          ],
        ),
        const SizedBox(height: 10),
        if (body == null)
          _emptyBox(cs, 'Instructions omitted by publisher.')
        else if (showRaw)
          ReadOnlyField(
            key: ValueKey('raw-$key'),
            text: body,
            label: 'Skill instructions, raw Markdown',
            minLines: 16,
            style: const TextStyle(
              fontFamily: AdklTheme.mono,
              fontSize: AdklTheme.fsMono,
              height: 1.7,
            ),
          )
        else ...[
          if (doc.preamble.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 22),
              child: ReadOnlyField(
                key: ValueKey('preamble-$key'),
                text: doc.preamble,
                label: 'Preamble',
                minLines: 1,
                style: const TextStyle(fontSize: AdklTheme.fsBody, height: 1.65),
              ),
            ),
          SectionEditor(
            key: ValueKey('sections-$key'),
            doc: doc,
            readOnly: true,
            toolNames: collection(
              'tools',
            ).map((t) => t['name'] as String).toSet(),
            onChanged: () {},
          ),
        ],
      ],
    );
  }

  /// Section label as its own accessibility header, never merged with hints.
  Widget _label(ColorScheme cs, String text) => Semantics(
    container: true,
    header: true,
    child: Text(text, style: _labelStyle(cs)),
  );

  TextStyle _labelStyle(ColorScheme cs) => TextStyle(
    fontSize: 10.5,
    fontWeight: FontWeight.w700,
    letterSpacing: 0.9,
    color: cs.onSurfaceVariant,
  );

  Widget _field(
    ColorScheme cs, {
    required String label,
    required String hint,
    required Widget child,
  }) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      _label(cs, label),
      const SizedBox(height: 3),
      Text(hint, style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
      const SizedBox(height: 9),
      child,
    ],
  );

  Widget _emptyBox(ColorScheme cs, String text) => Container(
    width: double.infinity,
    padding: const EdgeInsets.all(14),
    decoration: BoxDecoration(
      color: cs.surfaceContainerLow,
      borderRadius: BorderRadius.circular(8),
      border: Border.all(color: cs.outlineVariant),
    ),
    child: Text(
      text,
      style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
    ),
  );

  Widget _toolsField(ColorScheme cs, Map<String, dynamic> row) {
    final names = (row['tools'] as List).cast<String>();
    final owned = {
      for (final t in collection('tools'))
        if (t['owner'] == row['owner']) t['name'] as String: t,
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _label(cs, 'TOOLS'),
        const SizedBox(height: 3),
        Text(
          'Additional tools requested by this skill.',
          style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant),
        ),
        const SizedBox(height: 9),
        if (names.isEmpty)
          _emptyBox(
            cs,
            'No skill-specific tools bound. Globally configured tools may '
            'still be available.',
          )
        else
          // A bordered list, not a chip cloud: each row opens the inspector.
          Card.outlined(
            child: Column(
              children: [
                for (var i = 0; i < names.length; i++) ...[
                  if (i > 0) Divider(height: 1, color: cs.outlineVariant),
                  _toolRow(cs, names[i], owned[names[i]]),
                ],
              ],
            ),
          ),
      ],
    );
  }

  Widget _toolRow(ColorScheme cs, String name, Map<String, dynamic>? row) {
    final missing = row == null;
    final info = missing ? null : tool(row);
    return ListTile(
      onTap: missing ? null : () => open(1, row),
      leading: Icon(
        missing ? Icons.error_outline : iconForClass(info!.sideEffect),
        color: missing ? cs.failure : cs.onSurfaceVariant,
      ),
      title: Text(name, maxLines: 2, overflow: TextOverflow.ellipsis),
      subtitle: Text(
        missing ? 'Not in catalog' : info!.summary,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
      ),
    );
  }

  Widget _tokenPill(ColorScheme cs, int tokens) {
    final heavy = tokens > 3000;
    return Tooltip(
      message:
          'Estimated input tokens this skill\'s instructions add per turn '
          'while the skill is active. Tool declaration costs appear separately.',
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
        decoration: BoxDecoration(
          color: heavy
              ? cs.attentionContainer.withValues(alpha: 0.55)
              : cs.surfaceContainerHigh,
          borderRadius: BorderRadius.circular(5),
          border: Border.all(
            color: heavy
                ? cs.attention.withValues(alpha: 0.35)
                : cs.outlineVariant,
          ),
        ),
        child: Text(
          '~$tokens tok/turn',
          style: TextStyle(
            fontSize: 10.5,
            fontWeight: FontWeight.w600,
            color: heavy ? cs.onSurface : cs.onSurfaceVariant,
          ),
        ),
      ),
    );
  }

  // -------------------------------------------------------------- side panel

  /// Category order of the original: what breaks the agent first.
  static const _categoryOrder = [
    'Correctness',
    'Conflict',
    'Routing',
    'Tools',
    'Presentation',
    'Cost',
  ];

  /// Same presentation as the original's scan time: local, whole seconds.
  String? _exportedAt() {
    final value = bundle!['exportedAt'];
    final parsed = value is String ? DateTime.tryParse(value) : null;
    return parsed?.toLocal().toString().split('.').first ?? value?.toString();
  }

  String? get _selectedName =>
      view == 2 || selectedRow == null ? null : selectedRow!['name'] as String;

  Widget _sidePanel(ColorScheme cs) {
    if (bundle == null) return const SizedBox.shrink();
    final all = findings;
    final name = _selectedName;
    final visible = all
        .where(
          (f) =>
              findingsAll ||
              (name != null && f.skill == name && f.targetsTool == (view == 1)),
        )
        .toList();
    final checks = (report['checks'] as List).cast<Map<String, dynamic>>();
    final unchecked = checks.where((c) => c['status'] == 'not_checked').length;
    final failed = checks.where((c) => c['status'] == 'error').length;
    final errors = all.where((f) => f.isError).length;
    final warnings = all.where((f) => f.severity == 'WARN').length;
    final freshness = report['freshness'] ?? 'unknown';
    final selectedLabel = name == null
        ? 'item: none'
        : '${view == 1 ? 'tool' : 'skill'}: $name';
    final grouped = <String, List<Finding>>{};
    for (final f in visible) {
      grouped.putIfAbsent(f.category, () => []).add(f);
    }
    for (final list in grouped.values) {
      list.sort((a, b) {
        const rank = {'ERROR': 0, 'WARN': 1, 'INFO': 2};
        return (rank[a.severity] ?? 3).compareTo(rank[b.severity] ?? 3);
      });
    }
    // The original order, except categories holding errors lead: SDK report
    // categories (Diagnostics, Verification, ...) are absent from that order
    // and would otherwise sink an ERROR below Cost advisories.
    final ordered = [
      ..._categoryOrder.where(grouped.containsKey),
      ...grouped.keys.where((k) => !_categoryOrder.contains(k)),
    ];
    final categories = [
      ...ordered.where((k) => grouped[k]!.any((f) => f.isError)),
      ...ordered.where((k) => !grouped[k]!.any((f) => f.isError)),
    ];
    final small = TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant);
    return Material(
      color: cs.surfaceContainerLow,
      child: Column(
        children: [
          Container(
            height: 42,
            padding: const EdgeInsets.symmetric(horizontal: 16),
            decoration: BoxDecoration(
              border: Border(bottom: BorderSide(color: cs.outlineVariant)),
            ),
            child: Row(
              children: [
                Semantics(
                  header: true,
                  child: Text(
                    'Findings',
                    style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                      color: cs.onSurface,
                    ),
                  ),
                ),
                const SizedBox(width: 7),
                if (visible.isNotEmpty)
                  Text(
                    '${visible.length}',
                    style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant),
                  ),
                const Spacer(),
                Tooltip(
                  message:
                      'Published Doctor report from the offline export. '
                      'Selection filters this report; it does not run a scan. '
                      'Review check coverage.',
                  child: Icon(
                    Icons.info_outline,
                    size: 13,
                    color: cs.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
          // Where the original offers Run Doctor: the report is published.
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 10),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(
                  Icons.health_and_safety_outlined,
                  size: 18,
                  color: cs.onSurfaceVariant,
                ),
                const SizedBox(width: 8),
                Text(
                  'Published Doctor report',
                  style: TextStyle(
                    fontSize: 12.5,
                    fontWeight: FontWeight.w600,
                    color: cs.onSurface,
                  ),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Text(
              '$errors errors · $warnings warnings · $unchecked not run · '
              '$failed failed',
              textAlign: TextAlign.center,
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Text(
              'Exported: ${_exportedAt() ?? 'unavailable'}',
              textAlign: TextAlign.center,
            ),
          ),
          if (freshness != 'current')
            const Padding(
              padding: EdgeInsets.all(8),
              child: Text('Report outdated or unverified at export.'),
            ),
          ExpansionTile(
            title: Text('Coverage · $freshness at scan'),
            subtitle: Text(
              'Saved base · profile ${report['profile'] ?? 'default'} · '
              'UI build $portalBuild',
            ),
            children: [
              SizedBox(
                height: 180,
                child: ListView(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  children: [
                    Text(
                      'Source: ${report['sourceDigest'] ?? 'unknown'}',
                      style: small,
                    ),
                    for (final check in checks)
                      ListTile(
                        dense: true,
                        title: Text('${check['id']}: ${check['status']}'),
                        subtitle: Text('${check['detail']}'),
                      ),
                  ],
                ),
              ),
            ],
          ),
          DropdownButton<bool>(
            isExpanded: true,
            padding: const EdgeInsets.symmetric(horizontal: 16),
            value: findingsAll,
            items: [
              DropdownMenuItem(
                value: false,
                child: Text('Selected $selectedLabel'),
              ),
              const DropdownMenuItem(
                value: true,
                child: Text('Entire current agent'),
              ),
            ],
            onChanged: (value) => setState(() => findingsAll = value!),
          ),
          Text('${visible.length} of ${all.length} findings'),
          Expanded(
            child: visible.isEmpty
                ? Center(
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(
                          Icons.info_outline,
                          size: 22,
                          color: cs.onSurfaceVariant,
                        ),
                        const SizedBox(height: 8),
                        Text(
                          !findingsAll
                              ? 'No findings for this selection'
                              : 'No findings from completed checks',
                          style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                            color: cs.onSurface,
                          ),
                        ),
                        const SizedBox(height: 3),
                        Text(
                          'Unchecked areas are not a pass',
                          style: TextStyle(
                            fontSize: 11,
                            color: cs.onSurfaceVariant,
                          ),
                        ),
                      ],
                    ),
                  )
                : ListView(
                    padding: const EdgeInsets.fromLTRB(12, 12, 12, 24),
                    children: [
                      for (final category in categories) ...[
                        _categoryHeader(cs, category, grouped[category]!),
                        for (final f in grouped[category]!) _finding(cs, f),
                        const SizedBox(height: 10),
                      ],
                    ],
                  ),
          ),
        ],
      ),
    );
  }

  Widget _categoryHeader(ColorScheme cs, String name, List<Finding> items) {
    final errors = items.where((f) => f.isError).length;
    return Padding(
      padding: const EdgeInsets.only(top: 4, bottom: 6),
      child: Row(
        children: [
          Text(
            name.toUpperCase(),
            style: TextStyle(
              fontSize: 9,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.8,
              color: cs.onSurfaceVariant,
            ),
          ),
          const SizedBox(width: 6),
          Text(
            '${items.length}',
            style: TextStyle(fontSize: 9.5, color: cs.onSurfaceVariant),
          ),
          if (errors > 0) ...[
            const SizedBox(width: 5),
            Text(
              '$errors errors',
              style: TextStyle(
                fontSize: 9,
                fontWeight: FontWeight.w600,
                color: cs.failure,
              ),
            ),
          ],
          const SizedBox(width: 8),
          Expanded(child: Divider(height: 1, color: cs.outlineVariant)),
        ],
      ),
    );
  }

  Widget _finding(ColorScheme cs, Finding f) {
    final tone = f.tone(cs);
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(11),
      decoration: BoxDecoration(
        color: cs.surface,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: cs.outlineVariant),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(f.icon, size: 13, color: tone),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  f.title,
                  style: TextStyle(
                    fontSize: 10.5,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.3,
                    color: tone,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          if (findingsAll && f.skill.isNotEmpty)
            Text(
              f.skill,
              style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant),
            ),
          Text(
            f.message,
            style: TextStyle(fontSize: 11.5, height: 1.55, color: cs.onSurface),
          ),
          TextButton(
            onPressed: () => showFindingDetails(context, [f]),
            child: const Text('Explain & next step'),
          ),
        ],
      ),
    );
  }

  Widget _source(String text) => SelectableText(
    text,
    style: const TextStyle(fontFamily: AdklTheme.mono, fontSize: 12, height: 1.7),
  );

  void _overview() => showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('Artifact and check coverage'),
      content: SizedBox(
        width: 680,
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _source(
                'UI build: $portalBuild\nRegistry UI build: ${servedBuild ?? 'not published'}\nArtifact: ${bundle!['identity']['artifact']}\nSDK: ${bundle!['identity']['sdkRevision']}\nExported: ${bundle!['exportedAt']}\nSource digest: ${report['sourceDigest']}\nDeployment: not verified',
              ),
              for (final c in report['checks'] as List)
                ListTile(
                  title: Text('${c['id']}: ${c['status']}'),
                  subtitle: Text('${c['detail']}'),
                ),
              for (final limit in [
                ...report['limitations'],
                ...inventory['limitations'],
              ])
                Text('• $limit'),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Close'),
        ),
      ],
    ),
  );
}

/// The original outlined field, read-only: selectable, never mutable.
class ReadOnlyField extends StatefulWidget {
  const ReadOnlyField({
    super.key,
    required this.text,
    required this.label,
    required this.minLines,
    required this.style,
  });
  final String text, label;
  final int minLines;
  final TextStyle style;
  @override
  State<ReadOnlyField> createState() => _ReadOnlyFieldState();
}

class _ReadOnlyFieldState extends State<ReadOnlyField> {
  late final controller = TextEditingController(text: widget.text);

  @override
  void didUpdateWidget(ReadOnlyField old) {
    super.didUpdateWidget(old);
    if (old.text != widget.text) controller.text = widget.text;
  }

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => TextField(
    controller: controller,
    readOnly: true,
    showCursor: false,
    maxLines: null,
    minLines: widget.minLines,
    style: widget.style,
    decoration: InputDecoration(labelText: widget.label),
  );
}
