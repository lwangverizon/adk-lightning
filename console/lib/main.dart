import 'dart:async';

import 'package:flutter/material.dart';

import 'api.dart';
import 'a2ui_gallery.dart';
import 'chat_panel.dart';
import 'configuration.dart';
import 'rail.dart';
import 'diagnostics.dart';
import 'section_editor.dart';
import 'theme.dart';
import 'tool_detail.dart';
import 'tool_picker.dart';
import 'workflow_graph.dart';
import 'project_panel.dart';
import 'doctor_panel.dart';

void main() => runApp(const ConsoleApp());

class ConsoleApp extends StatelessWidget {
  const ConsoleApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'adk-lightning',
        debugShowCheckedModeBanner: false,
        theme: AdklTheme.light(),
        darkTheme: AdklTheme.dark(),
        home: Uri.base.queryParameters['a2ui-gallery'] == '1'
            ? const A2uiGallery() : const ConsoleShell(),
      );
}

/// Adaptive library/detail workspace with a single supporting panel.
/// Compact windows use full-width views; global keys retain state on resize.
class ConsoleShell extends StatefulWidget {
  const ConsoleShell({super.key, this.api});
  final AdklApi? api;
  @override
  State<ConsoleShell> createState() => _ConsoleShellState();
}

class _ConsoleShellState extends State<ConsoleShell> {
  late final api = widget.api ?? AdklApi();
  List<Finding> _allFindings = [];
  List<Finding> _workspaceSavedFindings = [];
  bool _findingsAll = true, _editable = true;
  bool _needsAttention = false;
  Map<String, dynamic>? _savedReport;
  DateTime? _scanReceivedAt;
  int _reportGeneration = 0;
  List<Finding> get _displayFindings =>
      ((_savedReport?['findings'] as List?) ?? []).map(Finding.fromJson).toList();

  Future<void> _scanSaved() async {
    final generation = _reportGeneration;
    await showDialog<void>(context: context,
      builder: (_) => DoctorPanel(api: api, initialReport: _savedReport,
        onReport: (report) {
          if (!mounted || generation != _reportGeneration) return;
          setState(() {
            _savedReport = report;
            // A failed rerun retains the prior scan timestamp.
            if (report['freshness'] == 'current') _scanReceivedAt = DateTime.now();
          });
        }));
  }

  void _invalidateReport() {
    _reportGeneration++;
    if (_savedReport != null) {
      _savedReport = {..._savedReport!, 'freshness': 'stale', 'passed': false};
    }
  }
  String? _sourceRevision;

  List<SkillSummary> _skills = [];
  List<ToolInfo> _catalog = [];
  List<NativeWorkflow> _workflows = [];
  String? _workflowName;
  Map<String, dynamic> _agent = {};

  int _railTab = 0;
  int _topTab = 0;   // 0 = Skills workspace, 1 = Configuration
  bool _chatOpen = true;

  /// Bumped on a saved feature change, to force a fresh ChatPanel.
  ///
  /// The server rebuilds the agent, but the worker still holds an ADK session
  /// created under the OLD configuration -- so without this the next turn runs the
  /// previous pipeline while the panel shows the new one, and the transcript above
  /// it came from a third. Resetting is the honest option: a conversation nobody
  /// can attribute to a configuration is worse than no conversation.
  int _chatRevision = 0;

  /// Agents this server can switch to. Empty when the server predates
  /// /api/agents, in which case the picker is simply not shown.
  AgentList? _agents;
  bool _switching = false;
  String _railQuery = '';
  String? _skillName;
  String? _pendingSkill;
  String? _toolName;

  String? _loadError;
  bool _canClearOnLoad = false;
  bool _clearingOnLoad = false;
  bool _loading = true;

  // editor buffer
  final _desc = TextEditingController();
  final _body = TextEditingController();   // kept for the raw fallback
  SkillBody? _doc;
  bool _rawMode = false;
  List<String> _tools = [];
  List<Finding> _findings = [];
  int _estTokens = 0;
  final _scaffoldKey = GlobalKey<ScaffoldState>();
  final _workspaceKey = GlobalKey();
  final _supportKey = GlobalKey();
  int _supportTab = 0; // findings, test
  bool _supportVisible = false; // full-screen support on compact windows
  double _supportWidth = 360;
  bool _dirty = false;
  bool _saving = false;
  Timer? _debounce;

  @override
  void initState() {
    super.initState();
    _boot();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _desc.dispose();
    _body.dispose();
    super.dispose();
  }

  Future<void> _boot() async {
    _selectionGeneration++;
    setState(() {
      _loading = true;
      _pendingSkill = null;
      _loadError = null;
      _canClearOnLoad = false;
      _reportGeneration++;
      _savedReport = null;
      _scanReceivedAt = null;
      _findingsAll = true;
    });
    try {
      final agent = await api.agent();
      final catalog = await api.catalog();
      final skills = await api.skills();
      final workflows = agent['mode'] == 'factory' ? await api.workflows() : <NativeWorkflow>[];
      final findings = await api.findings();
      // Best-effort: a server without /api/agents still serves one agent fine,
      // so a missing picker is better than a failed boot.
      AgentList? agents;
      try {
        agents = await api.agents();
      } catch (_) {
        agents = null;
      }
      if (!mounted) return;
      setState(() {
        _agent = agent;
        _catalog = catalog;
        _skills = skills;
        _workflows = workflows;
        _workflowName = null;
        _skillName = null;
        _toolName = null;
        _railTab = 0;
        _allFindings = findings;
        _workspaceSavedFindings = findings;
        _agents = agents;
        _loading = false;
      });
      if (skills.isNotEmpty) {
        await _openSkill(skills.first.name, reload: true);
      } else if (workflows.isNotEmpty) {
        await _openWorkflow('${workflows.first.agent}:${workflows.first.name}');
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _loadError = '$e';
        _canClearOnLoad = e is ApiException && e.canClear;
      });
    }
  }

  Future<void> _clearAndBoot() async {
    if (_clearingOnLoad) return;
    setState(() => _clearingOnLoad = true);
    try {
      await api.chatClear();
      if (!mounted) return;
      setState(() => _chatRevision++);
      await _boot();
    } catch (error) {
      if (mounted) setState(() => _loadError = 'Could not clear conversation: $error');
    } finally {
      if (mounted) setState(() => _clearingOnLoad = false);
    }
  }

  Future<void> _createAgent() async {
    if (_saving) return;
    if (_dirty && !await _confirmDiscard()) return;
    if (!mounted) return;
    final created = await showDialog<bool>(context: context,
        barrierDismissible: false, builder: (_) => CreateAgentDialog(api: api));
    if (created != true || !mounted) return;
    api.cancelChat();
    api.env = null;
    setState(() { _dirty = false; _skillName = null; _topTab = 2; _chatRevision++; });
    await _boot();
  }

  int _selectionGeneration = 0;
  Future<void> _openSkill(String name, {bool reload = false}) async {
    if (!reload && name == _skillName && _toolName == null &&
        _workflowName == null && _railTab == 0 && _pendingSkill == null) {
      return;
    }
    if (_dirty && name != _skillName && !await _confirmDiscard()) return;
    if (!mounted) return;
    final selection = ++_selectionGeneration;
    _lintGeneration++;
    final editGeneration = _lintGeneration;
    _debounce?.cancel();
    setState(() {
      _pendingSkill = name;
      _railTab = 0;
      _topTab = 0;
      _supportVisible = false;
    });
    _scaffoldKey.currentState?.closeDrawer();
    try {
      final d = await api.skill(name);
      if (!mounted || selection != _selectionGeneration ||
          editGeneration != _lintGeneration) {
        return;
      }
      setState(() {
        _railTab = 0;
        _topTab = 0;
        _skillName = name;
        _workflowName = null;
        _pendingSkill = null;
        _toolName = null;
        _desc.text = d.description;
        _body.text = d.body;
        _doc = d.doc;
        // Fall back to the text editor only when the parser found no structure.
        _rawMode = d.doc.sections.isEmpty;
        _tools = List.of(d.tools);
        _findings = _workspaceSavedFindings
            .where((f) => f.skill == name && f.subjectKind != 'tool').toList();
        _allFindings = _workspaceSavedFindings;
        _editable = d.editable;
        _sourceRevision = d.revision;
        _estTokens = d.estTokens;
        _dirty = false;
      });
    } catch (e) {
      if (mounted && selection == _selectionGeneration) {
        setState(() => _pendingSkill = null);
        _snack('Could not open $name: $e');
      }
    }
  }

  Future<void> _openTool(String name) async {
    if (_dirty && !await _confirmDiscard()) return;
    if (!mounted) return;
    _selectionGeneration++;
    _lintGeneration++;
    _debounce?.cancel();
    setState(() {
      _dirty = false;
      _topTab = 0;
      _toolName = name;
      _workflowName = null;
      _pendingSkill = null;
      _allFindings = _workspaceSavedFindings;
      _findings = _allFindings.where((f) => f.skill == name && f.targetsTool).toList();
    });
  }

  Future<void> _openWorkflow(String name) async {
    if (_dirty && !await _confirmDiscard()) return;
    if (!mounted) return;
    _selectionGeneration++;
    _lintGeneration++;
    _debounce?.cancel();
    setState(() {
      _dirty = false;
      _workflowName = name;
      _toolName = null;
      _pendingSkill = null;
      _railTab = 2;
      _topTab = 0;
      _supportVisible = false;
    });
    _scaffoldKey.currentState?.closeDrawer();
  }

  Future<bool> _confirmDiscard() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Discard unsaved changes?'),
        content: Text('$_skillName has edits not written to disk.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(c, false),
              child: const Text('Keep editing')),
          FilledButton(
              onPressed: () => Navigator.pop(c, true),
              child: const Text('Discard')),
        ],
      ),
    );
    return ok ?? false;
  }

  int _lintGeneration = 0;
  void _touch() {
    _lintGeneration++;
    // Text controllers update the active field themselves. Rebuilding every
    // section, the library and both supporting views on each key is redundant.
    // Refresh shell chrome once; lint results still refresh after the debounce.
    if (!_dirty) setState(() => _dirty = true);
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 450), _relint);
  }

  Future<void> _relint() async {
    final name = _skillName;
    final generation = _lintGeneration;
    if (name == null) return;
    try {
      final r = await api.lint(name,
          description: _desc.text,
          tools: _tools,
          doc: _rawMode ? null : _doc,
          body: _rawMode ? _body.text : null);
      if (!mounted || generation != _lintGeneration || name != _skillName) return;
      setState(() {
        _findings = r.findings;
        if (r.allFindings != null) _allFindings = r.allFindings!;
        _estTokens = r.estTokens;
      });
    } catch (_) {/* next keystroke retries */}
  }

  Future<void> _save({bool force = false}) async {
    final name = _skillName;
    if (name == null || _saving) return;
    final selection = _selectionGeneration;
    final editGeneration = _lintGeneration;
    setState(() => _saving = true);
    var persisted = false;
    try {
      final r = await api.save(name,
          description: _desc.text,
          tools: _tools,
          doc: _rawMode ? null : _doc,
          body: _rawMode ? _body.text : null,
          revision: _sourceRevision,
          force: force);
      if (!mounted) return;
      persisted = r.saved;
      setState(() {
        if (selection == _selectionGeneration && editGeneration == _lintGeneration) {
          if (r.findings.isNotEmpty) _findings = r.findings;
          if (r.saved) _dirty = false;
        }
        // Reset the previous runtime even if the following refresh fails.
        if (r.saved) {
          _chatRevision++;
          _invalidateReport();
        }
      });
      if (r.saved) {
        final fresh = await api.skills();
        final findings = await api.findings();
        final savedDetail = await api.skill(name);
        if (!mounted) return;
        setState(() {
          _skills = fresh;
          _allFindings = findings;
          _workspaceSavedFindings = findings;
          if (selection == _selectionGeneration) {
            if (editGeneration == _lintGeneration) {
              _findings = findings.where((f) => f.skill == name && f.subjectKind != 'tool').toList();
            }
            _sourceRevision = savedDetail.revision;
          }
        });
        _snack('Saved · embeddings now stale',
            action:
                SnackBarAction(label: 'Why?', onPressed: _explainEmbeddings));
      } else if (r.refused) {
        _refusedDialog(r);
      } else {
        _snack(r.error ?? 'Save failed');
      }
    } catch (e) {
      if (!mounted) return;
      _snack(persisted ? 'Saved, but workspace refresh failed: $e' : 'Save failed: $e');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _refusedDialog(SaveResult r) => showDialog(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('Save refused'),
          content: SizedBox(
            width: 440,
            child:
                Column(mainAxisSize: MainAxisSize.min, children: [
              const Align(
                alignment: Alignment.centerLeft,
                child: Text(
                    'This would write a skill the validator rejects. Fix the '
                    'errors, or override if you know why.',
                    style: TextStyle(fontSize: AdklTheme.fsBody)),
              ),
              const SizedBox(height: 14),
              ...r.findings.where((f) => f.isError).map((f) => Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Align(
                      alignment: Alignment.centerLeft,
                      child: Text('${f.rule} — ${f.message}',
                          style: const TextStyle(fontSize: 11.5, height: 1.5)),
                    ),
                  )),
            ]),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
            TextButton(
                onPressed: () {
                  Navigator.pop(c);
                  _save(force: true);
                },
                child: const Text('Save anyway')),
          ],
        ),
      );

  void _explainEmbeddings() => showDialog(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('Stale embeddings'),
          content: const SizedBox(
            width: 440,
            child: Text(
                'Retrieval matches against precomputed vectors, so an edited '
                'description is not live until embeddings are rebuilt. Until '
                'then the agent still retrieves on the old text.\n\n'
                'In production this must be automatic (save → re-embed → ready). '
                'This prototype only reports it.',
                style: TextStyle(fontSize: AdklTheme.fsBody, height: 1.6)),
          ),
          actions: [
            FilledButton(
                onPressed: () => Navigator.pop(c), child: const Text('Got it'))
          ],
        ),
      );

  void _snack(String msg, {SnackBarAction? action}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(msg), action: action));
  }

  Future<void> _pickTools() async {
    final picked = await showToolPicker(context,
        catalog: _catalog, selected: _tools, bodyText: _body.text);
    if (picked == null) return;
    setState(() => _tools = picked);
    _touch();
    _relint();
  }

  Future<void> _switchEnv(String? env) async {
    if (_saving) return;
    if (_dirty && !await _confirmDiscard()) return;
    if (!mounted) return;
    _dirty = false;
    api.env = env;
    await _boot();
  }

  // -------------------------------------------------------------------- build

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    if (_loading) {
      return Scaffold(
          body: Center(
              child: SizedBox(
                  width: 22,
                  height: 22,
                  child: CircularProgressIndicator(
                      strokeWidth: 2, color: cs.onSurfaceVariant))));
    }
    if (_loadError != null) return _errorScreen(cs);
    if (_agents != null && _agents!.agents.isEmpty) {
      return Scaffold(body: Center(child: SizedBox(width: 480, child: Column(
        mainAxisSize: MainAxisSize.min, children: [
          Text('Build your first agent', style: Theme.of(context).textTheme.headlineMedium),
          const SizedBox(height: 12),
          const Text('Describe its purpose to create a project with a starter skill and shared runtime defaults.'),
          const SizedBox(height: 20),
          FilledButton(onPressed: _createAgent, child: const Text('Create agent')),
        ]))));
    }

    return LayoutBuilder(builder: (context, constraints) {
      // Width budgets protect the editor, including when text is enlarged.
      final scale = MediaQuery.textScalerOf(context).scale(14) / 14;
      final wide = constraints.maxWidth >= 1400 * scale.clamp(1, 1.5);
      final libraryVisible = constraints.maxWidth >= 1100 * scale.clamp(1, 1.5)
          && _topTab == 0;
      final workspace = KeyedSubtree(key: _workspaceKey, child: _topTab == 2
          ? ProjectPanel(key: ValueKey('project-${_agents?.selected}'), api: api,
              nativeHost: _agent['agent']?['factory'] != null,
              onRuntimeChanged: () => setState(() { _chatRevision++; }))
          : _topTab == 1
          ? ConfigurationScreen(agent: _agent, onSaveFeatures: _saveFeatures)
          : _content(cs));
      final support = KeyedSubtree(key: _supportKey, child: Column(children: [
        if (wide) SizedBox(height: 52, child: Row(children: [
          Expanded(child: SegmentedButton<int>(
            segments: const [
              ButtonSegment(value: 0, icon: Icon(Icons.fact_check_outlined), label: Text('Checks')),
              ButtonSegment(value: 1, icon: Icon(Icons.chat_bubble_outline), label: Text('Chat')),
            ],
            selected: {_supportTab},
            onSelectionChanged: (value) => setState(() => _supportTab = value.first),
          )),
        ])),
        Expanded(child: IndexedStack(index: _supportTab, children: [
          _panelActivity(active: _supportTab == 0, child: _sidePanel(cs)),
          _panelActivity(active: _supportTab == 1, child:
            ChatPanel(key: ValueKey('chat-${_agents?.selected ?? 'default'}-$_chatRevision'), api: api)),
        ])),
      ]));
      return Scaffold(
        key: _scaffoldKey,
        drawer: libraryVisible ? null : Drawer(
          width: constraints.maxWidth.clamp(0, 360),
          child: SafeArea(child: Column(children: [
            ListTile(title: const Text('Agent library'), trailing: IconButton(
              tooltip: 'Close library', icon: const Icon(Icons.close),
              onPressed: () => _scaffoldKey.currentState?.closeDrawer())),
            Expanded(child: _library(closeAfterSelection: true)),
          ])),
        ),
        bottomNavigationBar: wide ? null : NavigationBar(
          selectedIndex: _supportVisible ? _supportTab + 1 : _topTab == 2 ? 3 : 0,
          onDestinationSelected: (index) => setState(() {
            _supportVisible = index == 1 || index == 2;
            if (_supportVisible) { _supportTab = index - 1; }
            else { _topTab = index == 3 ? 2 : 0; }
          }),
          destinations: const [
            NavigationDestination(icon: Icon(Icons.edit_note), label: 'Editor'),
            NavigationDestination(icon: Icon(Icons.fact_check_outlined), label: 'Findings'),
            NavigationDestination(icon: Icon(Icons.chat_bubble_outline), label: 'Test'),
            NavigationDestination(icon: Icon(Icons.settings_outlined), label: 'Project'),
          ],
        ),
        body: SafeArea(child: Column(children: [
          _adaptiveTopBar(cs, wide: wide, libraryVisible: libraryVisible),
          Expanded(child: Row(children: [
            if (libraryVisible) SizedBox(width: 280, child: _library()),
            Expanded(child: wide
                ? Row(children: [
                    Expanded(child: workspace),
                    if (_chatOpen) MouseRegion(cursor: SystemMouseCursors.resizeColumn,
                      child: GestureDetector(
                        onHorizontalDragUpdate: (details) => setState(() {
                          _supportWidth = (_supportWidth - details.delta.dx).clamp(320, 440);
                        }),
                        child: Semantics(label: 'Resize supporting panel',
                          onIncrease: () => setState(() => _supportWidth = (_supportWidth + 20).clamp(320, 440)),
                          onDecrease: () => setState(() => _supportWidth = (_supportWidth - 20).clamp(320, 440)),
                          child: Container(width: 8, color: cs.surfaceContainerHigh)),
                      )),
                    // Maintain chat and editor state when hiding or relocating panels.
                    Offstage(offstage: !_chatOpen,
                      child: SizedBox(width: _supportWidth,
                        child: _panelActivity(active: _chatOpen, child: support))),
                  ])
                : IndexedStack(index: _supportVisible ? 1 : 0,
                    children: [
                      _panelActivity(active: !_supportVisible, child: workspace),
                      _panelActivity(active: _supportVisible, child: support),
                    ])),
          ])),
        ])),
      );
    });
  }

  Widget _panelActivity({required bool active, required Widget child}) =>
      ExcludeFocus(excluding: !active,
        child: TickerMode(enabled: active, child: child));

  Widget _library({bool closeAfterSelection = false}) => NavRail(
    skills: _skills, tools: _catalog, workflows: _workflows, tab: _railTab,
    selectedWorkflow: _workflowName, onWorkflow: _agent['mode'] == 'factory' ? _openWorkflow : null,
    onTab: (i) => setState(() => _railTab = i),
    selectedSkill: _pendingSkill ?? _skillName,
    selectedTool: _pendingSkill == null ? _toolName : null,
    onSkill: (name) => _openSkill(name),
    onTool: (name) async {
      await _openTool(name);
      if (mounted) setState(() { _topTab = 0; _supportVisible = false; });
      if (closeAfterSelection) _scaffoldKey.currentState?.closeDrawer();
    },
    query: _railQuery, onQuery: (q) => setState(() => _railQuery = q), env: api.env,
    findings: _displayFindings, authoritativeFindings: true,
    scopeLabel: _savedReport == null ? 'Doctor not run'
        : 'Doctor report · saved files only',
    findingsStale: _dirty || _savedReport?['freshness'] != 'current',
    needsAttention: _needsAttention,
    onNeedsAttention: (value) => setState(() => _needsAttention = value),
  );

  Widget _adaptiveTopBar(ColorScheme cs, {required bool wide, required bool libraryVisible}) {
    final envs = ((_agent['environments'] ?? []) as List).cast<String>();
    return Material(color: cs.surfaceContainerLow, child: ConstrainedBox(
      constraints: const BoxConstraints(minHeight: 56),
      child: Padding(padding: const EdgeInsets.symmetric(horizontal: 8), child: Row(children: [
        if (!libraryVisible) IconButton(tooltip: 'Open library', icon: const Icon(Icons.menu),
          onPressed: () => _scaffoldKey.currentState?.openDrawer()),
        const Icon(Icons.bolt, size: 24),
        const SizedBox(width: 8),
        Expanded(child: _agentPicker(cs)),
        if (wide) ...[
          _topTabButton(cs, 0, Icons.edit_note, 'Skills'),
          if (_agent['agent']?['factory'] == null) _topTabButton(cs, 1, Icons.tune, 'Configuration'),
          _topTabButton(cs, 2, Icons.settings_outlined, 'Project'),
          IconButton(tooltip: _chatOpen ? 'Hide supporting panel' : 'Show supporting panel',
            onPressed: () => setState(() => _chatOpen = !_chatOpen),
            icon: const Icon(Icons.view_sidebar_outlined)),
        ],
        PopupMenuButton<String>(tooltip: 'Workspace actions',
          onSelected: (action) {
            if (action == 'create') _createAgent();
            if (action == 'reload') _reloadFromDisk();
            if (action == 'config') setState(() { _topTab = 1; _supportVisible = false; });
            if (action.startsWith('env:')) _switchEnv(action.substring(4).isEmpty ? null : action.substring(4));
          },
          itemBuilder: (_) => [
            const PopupMenuItem(value: 'create', child: Text('Create agent')),
            const PopupMenuItem(value: 'reload', child: Text('Reload from disk')),
            if (!wide && _agent['agent']?['factory'] == null)
              const PopupMenuItem(value: 'config', child: Text('Configuration')),
            if (envs.isNotEmpty) ...[
              const PopupMenuDivider(),
              CheckedPopupMenuItem(value: 'env:', checked: api.env == null, child: const Text('Environment: prod')),
              for (final env in envs) CheckedPopupMenuItem(value: 'env:$env', checked: api.env == env, child: Text('Environment: $env')),
            ],
          ]),
      ])),
    ));
  }

  Widget _content(ColorScheme cs) {
    Widget? toolDetail;
    // A selected tool takes the pane; the rail keeps the skill selected so
    // going back is one click.
    if (_railTab == 1 && _toolName != null) {
      final t = _catalog.firstWhere((x) => x.name == _toolName,
          orElse: () => _catalog.first);
      toolDetail = ToolDetail(tool: t, skills: _skills, onOpenSkill: _openSkill);
    }
    if (_pendingSkill != null) {
      return Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
        const CircularProgressIndicator(),
        const SizedBox(height: 16),
        Text('Loading $_pendingSkill…', key: const ValueKey('skill-loading')),
      ]));
    }
    if (_railTab == 2) {
      if (_workflowName == null) {
        return Center(child: Text('Select a workflow',
          style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant)));
      }
      final workflow = _workflows.firstWhere((w) => '${w.agent}:${w.name}' == _workflowName);
      return _workflowDetail(cs, workflow);
    }
    if (_skillName == null && toolDetail == null) {
      return Center(
          child: Text('Select a skill',
              style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant)));
    }
    return Align(alignment: Alignment.topCenter,
      child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 1120),
        child: toolDetail ?? _editor(cs)));
  }

  Widget _workflowDetail(ColorScheme cs, NativeWorkflow workflow) =>
    Align(alignment: Alignment.topCenter, child: ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 1120),
      child: ListView(padding: const EdgeInsets.fromLTRB(28, 24, 28, 48),
        children: [
          Text(workflow.name, style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 12),
          Text('Read-only native workflow declaration',
            style: TextStyle(color: cs.onSurfaceVariant)),
          const SizedBox(height: 24),
          Text('DESCRIPTION', style: Theme.of(context).textTheme.labelMedium),
          const SizedBox(height: 8),
          SelectableText(workflow.description),
          const SizedBox(height: 24),
          WorkflowGraphView(graph: workflow.graph),
          const SizedBox(height: 24),
          Text('Agent: ${workflow.agent}'),
          Text('Native operation: ${workflow.operation}'),
          const SizedBox(height: 16),
          const Text('The Test panel shows observed workflow dispatch and outcomes.'),
        ]),
    ));

  /// Top-level section switch. Sierra puts Journeys / Library / Integrations /
  /// Configuration here; we have two sections so far.
  /// Agent picker. The name was static text before, which is why there was no
  /// way to reach the other agents on disk at all.
  ///
  /// Falls back to that static text when the server offers fewer than two
  /// agents: a dropdown with one entry invites a click that does nothing.
  Widget _agentPicker(ColorScheme cs) {
    final list = _agents;
    final name = (_agent['agent']?['name'] ?? '') as String;

    if (list == null || list.agents.length < 2) {
      return Text(name, maxLines: 1, overflow: TextOverflow.ellipsis,
          style: TextStyle(
              fontFamily: AdklTheme.mono,
              fontSize: 11.5,
              color: cs.onSurfaceVariant));
    }

    return PopupMenuButton<String>(
      tooltip: 'Switch agent',
      enabled: !_switching,
      onSelected: _switchAgent,
      position: PopupMenuPosition.under,
      itemBuilder: (_) => [
        for (final a in list.agents)
          PopupMenuItem<String>(
            value: a.id,
            // A broken agent stays listed but unselectable: switching to it
            // would replace a working console with an error screen, and the
            // reason is already visible in the row.
            enabled: a.error == null,
            child: Row(children: [
              Icon(
                  a.id == list.selected
                      ? Icons.radio_button_checked
                      : (a.error != null
                          ? Icons.error_outline
                          : Icons.radio_button_off),
                  size: 13,
                  color: a.error != null
                      ? cs.failure
                      : (a.id == list.selected
                          ? cs.primary
                          : cs.onSurfaceVariant)),
              const SizedBox(width: 9),
              Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Text(a.name,
                        style: const TextStyle(
                            fontSize: 12, fontWeight: FontWeight.w600)),
                    Text(
                        a.error ??
                            '${a.id}  ·  ${a.skills} skill'
                                '${a.skills == 1 ? '' : 's'}'
                                '${a.model == null ? '' : '  ·  ${a.model}'}',
                        style: TextStyle(
                            fontSize: 9.5,
                            color: a.error != null
                                ? cs.failure
                                : cs.onSurfaceVariant)),
                  ]),
            ]),
          ),
      ],
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Flexible(child: Text(name, maxLines: 1, overflow: TextOverflow.ellipsis,
            style: TextStyle(
                fontFamily: AdklTheme.mono,
                fontSize: 11.5,
                color: cs.onSurfaceVariant))),
        const SizedBox(width: 3),
        if (_switching)
          SizedBox(
              width: 10,
              height: 10,
              child: CircularProgressIndicator(
                  strokeWidth: 1.6, color: cs.onSurfaceVariant))
        else
          Icon(Icons.arrow_drop_down, size: 16, color: cs.onSurfaceVariant),
      ]),
    );
  }

  /// Switch agents: tell the server, then reload everything.
  ///
  /// A full `_boot()` rather than a partial refresh, because every screen is
  /// derived from the selected agent -- skills, catalog, features, compiled
  /// prompt. Refreshing some of them would leave one screen describing an agent
  /// the others had moved on from.
  Future<void> _switchAgent(String id) async {
    if (_switching || _saving) return;
    if (_dirty && !await _confirmDiscard()) return;
    if (!mounted) return;
    setState(() => _switching = true);
    try {
      await api.selectAgent(id);
      if (!mounted) return;
      _dirty = false;
      // _boot() reloads every screen and opens the new agent's first skill, so
      // nothing here needs to reset the open skill by hand.
      await _boot();
    } catch (e) {
      if (mounted) setState(() => _loadError = '$e');
    } finally {
      if (mounted) setState(() => _switching = false);
    }
  }

  Future<void> _reloadFromDisk() async {
    if (_saving) return;
    if (_dirty && !await _confirmDiscard()) return;
    if (!mounted) return;
    _dirty = false;
    await _boot();
  }

  Widget _topTabButton(ColorScheme cs, int i, IconData icon, String label) {
    final on = _topTab == i;
    return Semantics(selected: on, button: true, child: Material(
      color: on ? cs.surfaceContainerHigh : Colors.transparent,
      borderRadius: BorderRadius.circular(AdklTheme.rSm),
      child: InkWell(
        onTap: () => setState(() => _topTab = i),
        borderRadius: BorderRadius.circular(AdklTheme.rSm),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
          child: Row(children: [
            Icon(icon, size: 14,
                color: on ? cs.onSurface : cs.onSurfaceVariant),
            const SizedBox(width: 6),
            Text(label,
                style: TextStyle(
                    fontSize: 12,
                    fontWeight: on ? FontWeight.w700 : FontWeight.w500,
                    color: on ? cs.onSurface : cs.onSurfaceVariant)),
          ]),
        ),
      ),
    ));
  }

  Future<void> _saveFeatures(Map<String, dynamic> block) async {
    final r = await api.saveFeatures(block);
    if (!mounted) return;
    if (r.saved) {
      final fresh = await api.agent();
      if (!mounted) return;
      setState(() {
        _agent = fresh;
        _chatRevision++;
      });
      _snack('Features updated · test conversation reset');
    } else {
      // The refusal is the product working: show why, in full.
      showDialog(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('Change refused'),
          content: SizedBox(
            width: 520,
            child: SelectableText(r.error ?? 'unknown error',
                style: const TextStyle(fontSize: 11.5, height: 1.5)),
          ),
          actions: [
            FilledButton(
                onPressed: () => Navigator.pop(c), child: const Text('OK')),
          ],
        ),
      );
    }
  }

  // ------------------------------------------------------------------ editor

  Widget _editor(ColorScheme cs) {
    final errors = _findings.where((f) => f.isError).length;
    return Column(children: [
      Container(
        padding: const EdgeInsets.fromLTRB(28, 16, 20, 14),
        decoration: BoxDecoration(
          border: Border(bottom: BorderSide(color: cs.outlineVariant)),
        ),
        child: Row(children: [
          Expanded(
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(_skillName!, maxLines: 2, overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.headlineSmall),
                  if (_dirty) Text('unsaved', style: TextStyle(color: cs.onSurfaceVariant)),
                ]),
          ),
          const SizedBox(width: 12),
          if (MediaQuery.sizeOf(context).width >= 600) _tokenPill(cs),
          const SizedBox(width: 10),
          FilledButton(
            onPressed: _saving || !_dirty || !_editable ? null : () => _save(),
            child: _saving
                ? const SizedBox(
                    width: 13,
                    height: 13,
                    child: CircularProgressIndicator(
                        strokeWidth: 2, color: Colors.white))
                : Text(errors > 0 ? 'Save ($errors)' : 'Save'),
          ),
        ]),
      ),
      if (!_editable) const Padding(padding: EdgeInsets.all(12), child: Text('Read-only native skill: register its source in authoring.skills to enable editing.')),
      Expanded(
        child: ExcludeFocus(excluding: !_editable,
          child: AbsorbPointer(absorbing: !_editable, child: ListView(
          padding: const EdgeInsets.fromLTRB(28, 22, 28, 48),
          children: [
            if (_dirty && errors > 0)
              MaterialBanner(
                content: Text('Draft validation: $errors errors. Not a Doctor scan.'),
                actions: [TextButton(onPressed: () => showFindingDetails(
                  context, _findings.where((f) => f.isError).toList()),
                  child: const Text('Review draft errors'))],
              ),
            _field(cs,
                label: 'DESCRIPTION',
                hint: 'When retrieval should select this skill. Trigger phrases '
                    'and exclusions.',
                child: TextField(
                  controller: _desc,
                  maxLines: null,
                  minLines: 4,
                  onChanged: (_) => _touch(),
                  style: const TextStyle(
                      fontSize: AdklTheme.fsBody, height: 1.65),
                  decoration: const InputDecoration(
                      labelText: 'Skill description',
                      hintText: 'Use this skill to…\nNOT for … → other-skill'),
                )),
            const SizedBox(height: 28),
            _toolsField(cs),
            const SizedBox(height: 28),
            _instructions(cs),
          ],
        ))),
      ),
    ]);
  }

  /// Structured section editor, with a raw-markdown escape hatch. The toggle is
  /// deliberate: the parser models the shapes real skills use, and anything it
  /// cannot model must still be editable rather than unreachable.
  Widget _instructions(ColorScheme cs) {
    final doc = _doc;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Expanded(
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('INSTRUCTIONS',
                    style: TextStyle(
                        fontSize: 10.5,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 0.9,
                        color: cs.onSurfaceVariant)),
                const SizedBox(height: 3),
                Text(
                    _rawMode
                        ? 'Raw markdown. Saves reflow the whole body.'
                        : 'One field per section. Untouched sections are written '
                            'back byte-identical.',
                    style:
                        TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
              ]),
        ),
        SegmentedButton<bool>(
          showSelectedIcon: false,
          segments: const [
            ButtonSegment(value: false, label: Text('Sections')),
            ButtonSegment(value: true, label: Text('Raw')),
          ],
          selected: {_rawMode},
          onSelectionChanged: (v) => setState(() => _rawMode = v.first),
        ),
      ]),
      const SizedBox(height: 10),
      if (_rawMode || doc == null)
        TextField(
          controller: _body,
          maxLines: null,
          minLines: 16,
          onChanged: (_) => _touch(),
          style: const TextStyle(
              fontFamily: AdklTheme.mono, fontSize: AdklTheme.fsMono, height: 1.7),
          decoration: const InputDecoration(
              labelText: 'Skill instructions, raw Markdown',
              hintText: '## Workflows\n- [ ] Step 1: call `tool_name`'),
        )
      else
        SectionEditor(
          key: ValueKey(_skillName),
          doc: doc,
          toolNames: {for (final t in _catalog) t.name},
          onChanged: _touch,
        ),
    ]);
  }

  Widget _field(ColorScheme cs,
          {required String label,
          required String hint,
          required Widget child}) =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label,
            style: TextStyle(
                fontSize: 10.5,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.9,
                color: cs.onSurfaceVariant)),
        const SizedBox(height: 3),
        Text(hint, style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
        const SizedBox(height: 9),
        child,
      ]);

  Widget _toolsField(ColorScheme cs) {
    final byName = {for (final t in _catalog) t.name: t};
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('TOOLS',
                style: TextStyle(
                    fontSize: 10.5,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.9,
                    color: cs.onSurfaceVariant)),
            const SizedBox(height: 3),
            Text('Additional tools requested by this skill.',
                style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
          ]),
        ),
        OutlinedButton.icon(
          onPressed: _pickTools,
          icon: const Icon(Icons.add, size: 14),
          label: Text(_tools.isEmpty ? 'Add' : 'Edit ${_tools.length}'),
        ),
      ]),
      const SizedBox(height: 9),
      if (_tools.isEmpty)
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: cs.surfaceContainerLow,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: cs.outlineVariant),
          ),
          child: Text('No skill-specific tools bound. Globally configured tools may still be available.',
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        )
      else
        // A bordered list, not a chip cloud. Each row is clickable through to
        // the tool inspector, which is what makes "review each tool" real.
        Card.outlined(
          child: Column(children: [
            for (var i = 0; i < _tools.length; i++) ...[
              if (i > 0) Divider(height: 1, color: cs.outlineVariant),
              _toolRow(cs, _tools[i], byName[_tools[i]]),
            ],
          ]),
        ),
    ]);
  }

  Widget _toolRow(ColorScheme cs, String name, ToolInfo? t) {
    final missing = t == null;
    return ListTile(
      onTap: missing ? null : () => _openTool(name),
      leading: Icon(missing ? Icons.error_outline : iconForClass(t.sideEffect),
        color: missing ? cs.failure : cs.onSurfaceVariant),
      title: Text(name, maxLines: 2, overflow: TextOverflow.ellipsis),
      subtitle: Text(missing ? 'Not in catalog' : t.summary,
        maxLines: 2, overflow: TextOverflow.ellipsis),
      trailing: IconButton(tooltip: 'Unbind $name', icon: const Icon(Icons.close),
        onPressed: () { setState(() => _tools.remove(name)); _touch(); _relint(); }),
    );
  }

  Widget _tokenPill(ColorScheme cs) {
    final heavy = _estTokens > 3000;
    return Tooltip(
      message: 'Estimated input tokens this skill\'s instructions add per turn '
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
                  : cs.outlineVariant),
        ),
        child: Text('~$_estTokens tok/turn',
            style: TextStyle(
                fontSize: 10.5,
                fontWeight: FontWeight.w600,
                color: heavy ? cs.onSurface : cs.onSurfaceVariant)),
      ),
    );
  }

  // -------------------------------------------------------------- side panel

  /// Category order: what blocks a save first, what costs money last.
  ///
  /// Not alphabetical and not by count -- a reader scanning this wants the thing
  /// that breaks the agent before the thing that makes it expensive.
  static const _categoryOrder = [
    'Correctness', 'Conflict', 'Routing', 'Tools', 'Presentation', 'Cost',
  ];

  Widget _sidePanel(ColorScheme cs) {
    final visibleFindings = _displayFindings.where((f) => _findingsAll ||
        (f.skill == (_toolName ?? _skillName) &&
         f.targetsTool == (_toolName != null))).toList();
    final checks = (_savedReport?['checks'] as List?) ?? [];
    final unchecked = checks.where((c) => c['status'] == 'not_checked').length;
    final failed = checks.where((c) => c['status'] == 'error').length;
    final errors = _displayFindings.where((f) => f.isError).length;
    final warnings = _displayFindings.where((f) => f.severity == 'WARN').length;
    final selected = _toolName != null ? 'tool: $_toolName'
        : _skillName != null ? 'skill: $_skillName' : 'item: none';
    // Grouped by outcome, then by severity within each group. Errors still lead
    // overall because a save is blocked on them.
    final grouped = <String, List<Finding>>{};
    for (final f in visibleFindings) {
      grouped.putIfAbsent(f.category, () => []).add(f);
    }
    for (final list in grouped.values) {
      list.sort((a, b) {
        const rank = {'ERROR': 0, 'WARN': 1, 'INFO': 2};
        return (rank[a.severity] ?? 3).compareTo(rank[b.severity] ?? 3);
      });
    }
    final categories = [
      ..._categoryOrder.where(grouped.containsKey),
      ...grouped.keys.where((k) => !_categoryOrder.contains(k)),
    ];

    return Container(
      color: cs.surfaceContainerLow,
      child: Column(children: [
        Container(
          height: 42,
          padding: const EdgeInsets.symmetric(horizontal: 16),
          decoration: BoxDecoration(
            border: Border(bottom: BorderSide(color: cs.outlineVariant)),
          ),
          child: Row(children: [
            Text('Findings',
                style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    color: cs.onSurface)),
            const SizedBox(width: 7),
            if (visibleFindings.isNotEmpty)
              Text('${visibleFindings.length}',
                  style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
            const Spacer(),

            Tooltip(
              message: 'Doctor checks saved files. Selection filters this report; '
                  'it does not run a different scan. Review check coverage.',
              child: Icon(Icons.info_outline, size: 13, color: cs.onSurfaceVariant),
            ),
          ]),
        ),
        TextButton.icon(onPressed: _scanSaved,
          icon: const Icon(Icons.health_and_safety_outlined),
          label: Text(_savedReport == null ? 'Run Doctor' : 'Rerun Doctor')),
        if (_savedReport != null) ...[
          Padding(padding: const EdgeInsets.symmetric(horizontal: 12), child: Text(
            '$errors errors · $warnings warnings · $unchecked not run · $failed failed')),
          Padding(padding: const EdgeInsets.symmetric(horizontal: 12), child: Text(
            'Last current scan received: ${_scanReceivedAt?.toLocal().toString().split('.').first ?? 'unavailable'}')),
          if (_savedReport!['freshness'] != 'current')
            const Padding(padding: EdgeInsets.all(8), child: Text(
              'Report outdated or unverified — rerun Doctor.')),
        ],
        if (_savedReport != null)
          Material(color: cs.surfaceContainerLow, child: ExpansionTile(title: Text('Coverage · ${_savedReport!['freshness']} at scan'),
            subtitle: Text('Saved base · profile ${_savedReport!['profile'] ?? 'default'} · overlays excluded'),
            children: [SizedBox(height: 180, child: ListView(children: [
              Text('Source: ${_savedReport!['sourceDigest'] ?? 'unknown'}'),
              for (final check in _savedReport!['checks'] as List)
                ListTile(dense: true, title: Text('${check['id']}: ${check['status']}'),
                    subtitle: Text(check['detail'] as String)),
            ]))])),
        if (_dirty)
          const Padding(padding: EdgeInsets.all(8), child: Text(
            'Saved files only — draft changes not checked by Doctor.')),
        DropdownButton<bool>(isExpanded: true, padding: const EdgeInsets.symmetric(horizontal: 16), value: _findingsAll, items: [
          DropdownMenuItem(value: false, child: Text('Selected $selected')),
          const DropdownMenuItem(value: true, child: Text('Entire current agent'))],
          onChanged: (value) => setState(() => _findingsAll = value!)),
        if (_savedReport != null)
          Text('${visibleFindings.length} of ${_displayFindings.length} findings'),
        Expanded(
          child: visibleFindings.isEmpty
              ? Center(
                  child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                      Icon(Icons.info_outline,
                          size: 22, color: cs.onSurfaceVariant),
                      const SizedBox(height: 8),
                      Text(_savedReport == null ? 'Doctor has not run'
                          : !_findingsAll ? 'No findings for this selection'
                          : 'No findings from completed checks',
                          style: TextStyle(
                              fontSize: 12,
                              fontWeight: FontWeight.w600,
                              color: cs.onSurface)),
                      const SizedBox(height: 3),
                      Text('Unchecked areas are not a pass',
                          style: TextStyle(
                              fontSize: 11, color: cs.onSurfaceVariant)),
                    ]))
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
      ]),
    );
  }

  /// Doctor report counts, independent of the editor's save validation.
  Widget _categoryHeader(ColorScheme cs, String name, List<Finding> items) {
    final errors = items.where((f) => f.isError).length;
    return Padding(
      padding: const EdgeInsets.only(top: 4, bottom: 6),
      child: Row(children: [
        Text(name.toUpperCase(),
            style: TextStyle(
                fontSize: 9,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.8,
                color: cs.onSurfaceVariant)),
        const SizedBox(width: 6),
        Text('${items.length}',
            style: TextStyle(fontSize: 9.5, color: cs.onSurfaceVariant)),
        if (errors > 0) ...[
          const SizedBox(width: 5),
          Text('$errors errors',
              style: TextStyle(
                  fontSize: 9,
                  fontWeight: FontWeight.w600,
                  color: cs.failure)),
        ],
        const SizedBox(width: 8),
        Expanded(child: Divider(height: 1, color: cs.outlineVariant)),
      ]),
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
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(f.icon, size: 13, color: tone),
          const SizedBox(width: 6),
          Expanded(
            child: Text(f.title,
                style: TextStyle(
                    fontSize: 10.5,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.3,
                    color: tone)),
          ),
        ]),
        const SizedBox(height: 6),
        if (_findingsAll) Text(f.skill, style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
        Text(f.message,
            style: TextStyle(
                fontSize: 11.5, height: 1.55, color: cs.onSurface)),
        TextButton(onPressed: () => showFindingDetails(context, [f]),
            child: const Text('Explain & next step')),
      ]),
    );
  }

  Widget _errorScreen(ColorScheme cs) => Scaffold(
        body: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 460),
            child: Card.outlined(
              child: Padding(
                padding: const EdgeInsets.all(22),
                child:
                    Column(mainAxisSize: MainAxisSize.min, children: [
                  Icon(Icons.cloud_off, size: 28, color: cs.failure),
                  const SizedBox(height: 12),
                  Text(_canClearOnLoad ? 'Project changed' : 'Cannot load the console',
                      style: TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.w600,
                          color: cs.onSurface)),
                  const SizedBox(height: 10),
                  if (!_canClearOnLoad) Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: cs.surfaceContainerLow,
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: cs.outlineVariant),
                    ),
                    child: const SelectableText(
                        'python3 -m adkl.server --root workspace',
                        style: TextStyle(
                            fontFamily: AdklTheme.mono, fontSize: 11.5)),
                  ),
                  const SizedBox(height: 12),
                  Text(_loadError!,
                      style: TextStyle(
                          fontSize: 10.5, color: cs.onSurfaceVariant)),
                  const SizedBox(height: 16),
                  FilledButton(
                      onPressed: _clearingOnLoad ? null : _canClearOnLoad ? _clearAndBoot : _boot,
                      child: Text(_canClearOnLoad ? 'Clear conversation' : 'Retry')),
                ]),
              ),
            ),
          ),
        ),
      );
}
