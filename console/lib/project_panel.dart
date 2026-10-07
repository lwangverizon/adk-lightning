import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'api.dart';
import 'verification_panel.dart';

class CreateAgentDialog extends StatefulWidget {
  const CreateAgentDialog({super.key, required this.api});
  final AdklApi api;
  @override
  State<CreateAgentDialog> createState() => _CreateAgentDialogState();
}

class _CreateAgentDialogState extends State<CreateAgentDialog> {
  final _name = TextEditingController();
  final _purpose = TextEditingController();
  final _model = TextEditingController(text: 'gemini-3.6-flash');
  bool _busy = false;
  String? _error;
  @override
  void dispose() { _name.dispose(); _purpose.dispose(); _model.dispose(); super.dispose(); }

  Future<void> _create() async {
    setState(() { _busy = true; _error = null; });
    try {
      await widget.api.projectAction('create', {'name': _name.text.trim(),
        'purpose': _purpose.text.trim(), 'model': _model.text.trim()});
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) setState(() { _error = '$e'; _busy = false; });
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Create an agent'),
    content: SizedBox(width: 480, child: SingleChildScrollView(child: Column(
      mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
      const Text('Start with a purpose. Your agent gets its own project folder and an editable starter skill.'),
      const SizedBox(height: 16),
      TextField(controller: _name, enabled: !_busy, decoration: const InputDecoration(
        labelText: 'Agent name', helperText: 'Lowercase letters, digits and underscores')),
      const SizedBox(height: 12),
      TextField(controller: _purpose, enabled: !_busy, minLines: 3, maxLines: 6,
        decoration: const InputDecoration(labelText: 'What should this agent help people do?')),
      const SizedBox(height: 12),
      TextField(controller: _model, enabled: !_busy,
        decoration: const InputDecoration(labelText: 'Gemini model')),
      if (_error != null) Padding(padding: const EdgeInsets.only(top: 12), child: Text(_error!,
        style: TextStyle(color: Theme.of(context).colorScheme.error))),
    ]))),
    actions: [TextButton(onPressed: _busy ? null : () => Navigator.pop(context), child: const Text('Cancel')),
      FilledButton(onPressed: _busy ? null : _create, child: Text(_busy ? 'Creating…' : 'Create agent'))],
  );
}

class ProjectPanel extends StatefulWidget {
  const ProjectPanel({super.key, required this.api, required this.onRuntimeChanged, this.nativeHost = false});
  final bool nativeHost;
  final AdklApi api;
  final VoidCallback onRuntimeChanged;
  @override
  State<ProjectPanel> createState() => _ProjectPanelState();
}

class _ProjectPanelState extends State<ProjectPanel> {
  Map<String, dynamic> _status = {};
  Map<String, dynamic>? _report;
  String? _error;
  bool _busy = false;
  Timer? _poll;
  final _key = TextEditingController();
  @override
  void initState() { super.initState(); _refresh(); }
  @override
  void dispose() { _poll?.cancel(); _key.dispose(); super.dispose(); }

  Future<void> _refresh() async {
    try {
      final status = await widget.api.project();
      if (!mounted) return;
      final wasRunning = (_status['job'] as Map?)?['state'] == 'running';
      setState(() { _status = status; });
      _poll?.cancel();
      if ((status['job'] as Map?)?['state'] == 'running') {
        _poll = Timer(const Duration(seconds: 2), _refresh);
      } else if (wasRunning) {
        widget.onRuntimeChanged();
      }
    } catch (e) { if (mounted) setState(() { _error = '$e'; }); }
  }

  Future<void> _act(String action) async {
    setState(() { _busy = true; _error = null; });
    try {
      final report = action == 'audit' && widget.nativeHost ? await widget.api.projectAction('inspect', {}) :
        action == 'audit' ? await widget.api.audit() :
        await widget.api.projectAction(action, action == 'credentials' ? {'key': _key.text} : {});
      if (!mounted) return;
      if (action == 'audit' || action == 'inspect') setState(() { _report = report; });
      if (action == 'credentials') { _key.clear(); widget.onRuntimeChanged(); }
      await _refresh();
    } catch (e) { if (mounted) setState(() { _error = '$e'; }); }
    finally { if (mounted) setState(() { _busy = false; }); }
  }

  @override
  Widget build(BuildContext context) {
    final job = (_status['job'] as Map?) ?? {};
    final running = job['state'] == 'running';
    final disabled = _busy || running;
    return ListView(padding: const EdgeInsets.all(24), children: [
      Text('Project readiness', style: Theme.of(context).textTheme.headlineSmall),
      const SizedBox(height: 8),
      SelectableText('${_status['path'] ?? ''}'),
      const SizedBox(height: 20),
      Text(_status['prepared'] == true ? 'Runtime installed' : 'Prepare the project runtime to enable testing.'),
      const SizedBox(height: 8),
      Wrap(spacing: 8, runSpacing: 8, children: [
        FilledButton(onPressed: disabled || _status['canPrepare'] != true ? null : () => _act('prepare'),
          child: const Text('Prepare runtime')),
        OutlinedButton(onPressed: disabled ? null : () => _act('inspect'), child: const Text('Inspect runtime')),
        OutlinedButton(onPressed: disabled ? null : () => _act('audit'), child: const Text('Run audit')),
      ]),
      if (_status['canPrepare'] != true) const Padding(padding: EdgeInsets.only(top: 8),
        child: Text('Runtime setup needs a trusted SDK wheel configured by the workspace owner.')),
      const SizedBox(height: 24),
      if (_report != null) Padding(padding: const EdgeInsets.only(top: 16),
        child: _report!['source'] == 'host_factory'
          ? NativeInspectionReport(report: _report!)
          : SelectableText(const JsonEncoder.withIndent('  ').convert(_report))),
      const SizedBox(height: 24),
      VerificationPanel(status: Map<String, dynamic>.from((_status['verification'] as Map?) ?? {}),
        busy: disabled, onRun: _act, onRefresh: _refresh),
      const SizedBox(height: 24),
      const Text('Local Gemini credential'),
      const Text('Saved only to this project’s local .env file. Deployment uses your host secret manager.'),
      const SizedBox(height: 8),
      TextField(controller: _key, obscureText: true, enableSuggestions: false, autocorrect: false,
        decoration: const InputDecoration(labelText: 'Google API key')),
      Align(alignment: Alignment.centerLeft, child: TextButton(
        onPressed: disabled ? null : () => _act('credentials'), child: const Text('Save local credential'))),
      const SizedBox(height: 24),
      const Text('Customer-owned deployment'),
      const Text('Build a checked bundle for native ADK serving. Your host supplies access control, durable sessions and rollout.'),
      const SizedBox(height: 8),
      Align(alignment: Alignment.centerLeft, child: OutlinedButton(
        onPressed: disabled ? null : () => _act('release'), child: const Text('Build deployment bundle'))),
      if (running) const LinearProgressIndicator(),
      if (job['state'] != null && job['state'] != 'idle') Text('${job['operation']}: ${job['state']}'),
      if (job['message'] != null) Text('${job['message']}'),
      if (job['artifact'] != null) SelectableText(
        'Bundle: ${_status['path']}/.adkl/releases/${job['artifact']}\nDownload: ${widget.api.base}/api/project/artifact'),
      if (_error != null) Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),

    ]);
  }
}

class NativeInspectionReport extends StatelessWidget {
  final Map<String, dynamic> report;
  const NativeInspectionReport({super.key, required this.report});

  Widget _agent(BuildContext context, Map agent) {
    final bindings = (agent['adkl'] as List?) ?? [];
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text('${agent['name']} · ${agent['type']}', style: Theme.of(context).textTheme.titleMedium),
      Text('Model: ${agent['model'] ?? 'not declared'}'),
      if (bindings.isEmpty) const Text('No ADKL runtime binding declared on this agent.'),
      for (final binding in bindings) ...[
        Text('Shared features: ${(binding['features'] as List).join(', ')}'),
        // Both delivery axes: with a host sink the companion never joins the
        // transcript, so a widget arrives with no sentence and nothing here
        // would otherwise say why.
        Text('Widget delivery: ${binding['delivery']}'
            ' · companion: ${binding['companionDelivery'] ?? 'model_content'}'),
        // Staged specs were reported by the server and rendered nowhere, so a
        // host's whole staged-widget surface was invisible unless a reviewer
        // opened the raw inspection data. The role is the reply contract this
        // spec asks for; an adopted spec declares no payload type because its
        // build decides one.
        if ((binding['widgets'] as List? ?? []).isNotEmpty)
          ExpansionTile(title: Text('Staged widgets (${(binding['widgets'] as List).length})'),
            children: [
              const Text('Declared specs. A spec is not proof a widget rendered; '
                  'the Test panel reports each turn\'s gates.'),
              for (final spec in (binding['widgets'] as List)) ListTile(
                title: Text('${spec['key']} · ${spec['role'] ?? 'no reply contract'}'),
                subtitle: Text('payload: ${spec['payloadType'] ?? 'decided by build'}'
                    ' · register: ${spec['register']?.isNotEmpty == true ? spec['register'] : 'derived'}')),
            ]),
        for (final skill in (binding['skills'] as List)) ExpansionTile(
          title: Text('${skill['name']}'), subtitle: Text('${skill['description']}', maxLines: 2, overflow: TextOverflow.ellipsis),
          childrenPadding: const EdgeInsets.all(12),
          children: [SelectableText('${skill['description']}'), SelectableText('${skill['instructions']}'),
            Text('Declared tools: ${(skill['tools'] as List).join(', ')}')]),
        ExpansionTile(title: const Text('Tools and scope'), children: [
          for (final tool in (binding['tools'] as List)) ListTile(
            title: Text('${tool['name']} · ${tool['scope']}'),
            subtitle: Text('${tool['description']}'))]),
        if (binding['audit'] != null) ExpansionTile(title: const Text('Native content audit'), children: [
          const Text('Declared content only. Behavioral evaluation has not run.'),
          SelectableText(const JsonEncoder.withIndent('  ').convert(binding['audit']))]),
      ],
      for (final child in (agent['children'] as List? ?? [])) _agent(context, child as Map),
    ]);
  }

  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
    const Text('Native agent inspection'),
    const Text('Read-only declarations from the host factory. Edit the source in its repository.'),
    for (final finding in (report['authoringFindings'] ?? []) as List)
      Padding(padding: const EdgeInsets.symmetric(vertical: 6),
        child: Text('${finding['skill']}: ${finding['message']}',
          style: TextStyle(color: Theme.of(context).colorScheme.error))),
    const SizedBox(height: 12),
    _agent(context, report['agent'] as Map),
    if (report['lastRequest'] != null) ExpansionTile(
      title: const Text('Last local test request'), children: [
        const Text('Captured after shared before-model processing in this disposable session.'),
        SelectableText('${(report['lastRequest'] as Map)['instruction']}')]),
    ExpansionTile(title: const Text('Full inspection data'), children: [
      SelectableText(const JsonEncoder.withIndent('  ').convert(report))]),
  ]);
}
