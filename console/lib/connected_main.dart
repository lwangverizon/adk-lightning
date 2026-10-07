import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

void main() => runApp(MaterialApp(
  title: 'ADKL Connected Inspection',
  theme: ThemeData(colorSchemeSeed: Colors.indigo, useMaterial3: true),
  home: const ConnectedPortal(),
));

class ConnectedApi {
  ConnectedApi({http.Client? client, Uri? baseUri})
      : client = client ?? http.Client(), baseUri = baseUri ?? Uri.base;
  final http.Client client;
  final Uri baseUri;
  Future<Map<String, dynamic>> get(String path) async {
    if (!RegExp(r'^/api/v1/agents(?:/[a-z0-9-]+/inspection)?$').hasMatch(path)) {
      throw const FormatException('Invalid API path');
    }
    // Relative to the built portal base: supports both / and embedded /adkl/.
    final response = await client.get(baseUri.resolve(path.substring(1)))
        .timeout(const Duration(seconds: 20));
    if (response.statusCode != 200 || response.bodyBytes.length > 5 * 1024 * 1024) {
      throw const FormatException('Unavailable or access denied');
    }
    final data = jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
    if (data['schemaVersion'] != 1 || data['mode'] != 'connected') {
      throw const FormatException('Unsupported connected protocol');
    }
    return data;
  }
  void close() => client.close();
}

/// Separate read-only entry point. No local workspace, mutation or chat API.
class ConnectedPortal extends StatefulWidget {
  const ConnectedPortal({super.key, this.api});
  final ConnectedApi? api;
  @override
  State<ConnectedPortal> createState() => _ConnectedPortalState();
}

class _ConnectedPortalState extends State<ConnectedPortal> {
  late final api = widget.api ?? ConnectedApi();
  List<Map<String, dynamic>> agents = [];
  Map<String, dynamic>? envelope;
  String? selected, error;
  bool loading = true;
  bool expired = false;
  int generation = 0;
  int tab = 0;
  String filter = '';
  Timer? expiry;

  @override
  void initState() { super.initState(); registry(); }
  @override
  void dispose() {
    expiry?.cancel();
    if (widget.api == null) api.close();
    super.dispose();
  }

  Future<void> registry() async {
    final request = ++generation;
    expiry?.cancel();
    setState(() { loading = true; envelope = null; selected = null; error = null; agents = []; });
    try {
      final data = await api.get('/api/v1/agents');
      final entries = (data['agents'] as List).cast<Map<String, dynamic>>();
      if (!mounted || request != generation) return;
      setState(() { agents = entries; loading = false; });
      if (entries.isNotEmpty) await select(entries.first['id'] as String);
    } catch (_) {
      if (mounted && request == generation) {
        setState(() {
        loading = false; error = 'Registry unavailable or access denied. Sign in through the approved gateway.';
      });
      }
    }
  }

  Future<void> select(String id) async {
    final request = ++generation;
    expiry?.cancel();
    setState(() { selected = id; envelope = null; error = null; loading = true; expired = false; });
    try {
      final data = await api.get('/api/v1/agents/$id/inspection');
      final snapshot = data['snapshot'] as Map<String, dynamic>;
      final entry = agents.singleWhere((a) => a['id'] == id);
      if (snapshot['schemaVersion'] != 1 || snapshot['kind'] != 'adkl_loaded_declarations' ||
          snapshot['agentId'] != id || snapshot['environment'] != entry['environment'] ||
          snapshot['agents'] is! List || (snapshot['agents'] as List).isEmpty ||
          snapshot['capabilities']['chat'] != false || snapshot['capabilities']['edit'] != false ||
          snapshot['capabilities']['sessions'] != false) {
        throw const FormatException('Identity or capabilities mismatch');
      }
      final evidence = data['retainedEvidence'];
      if (evidence != null && evidence['state'] == 'matched') {
        final binding = evidence['binding'];
        if (evidence['provenance'] != 'publisher_asserted' || evidence['executedHere'] != false ||
            binding['snapshotDigest'] != data['snapshotDigest'] ||
            ['agentId', 'environment', 'release', 'instanceId', 'generation']
                .any((key) => binding[key] != snapshot[key]) || evidence['contracts'] is! List) {
          throw const FormatException('Retained evidence identity mismatch');
        }
      }
      final received = DateTime.parse(data['gatewayReceivedAt'] as String);
      final seconds = data['observationValidForSeconds'] as int;
      if (seconds < 1 || seconds > 60) throw const FormatException('Invalid observation window');
      var remaining = received.add(Duration(seconds: seconds)).difference(DateTime.now());
      if (evidence != null && evidence['state'] == 'matched') {
        final receiptRemaining = DateTime.parse(evidence['expiresAt'] as String).difference(DateTime.now());
        if (receiptRemaining < remaining) remaining = receiptRemaining;
      }
      if (!mounted || request != generation) return;
      setState(() { envelope = data; loading = false; expired = remaining.isNegative; });
      if (!remaining.isNegative) {
        expiry = Timer(remaining, () {
        if (mounted && request == generation) setState(() => expired = true);
      });
      }
    } catch (_) {
      if (mounted && request == generation) {
        setState(() {
        loading = false; envelope = null;
        error = 'Inspection unavailable, uninitialized or access denied. Previous content cleared.';
      });
      }
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('ADKL · Connected'), actions: [
      IconButton(tooltip: 'Reload authorized registry', onPressed: loading ? null : registry,
        icon: const Icon(Icons.refresh)),
    ]),
    body: Column(children: [
      Padding(padding: const EdgeInsets.all(12), child: DropdownButtonFormField<String>(
        key: ValueKey('${agents.map((a) => a['id']).join(',')}-$selected'),
        initialValue: selected, isExpanded: true,
        decoration: const InputDecoration(labelText: 'Authorized agent', border: OutlineInputBorder()),
        items: agents.map((a) => DropdownMenuItem(value: a['id'] as String,
          child: Text(a['name'] as String))).toList(),
        onChanged: (id) { if (id != null) { tab = 0; filter = ''; select(id); } },
      )),
      const Padding(padding: EdgeInsets.symmetric(horizontal: 12), child: Text(
        'Read-only loaded declarations · One process, not fleet or session evidence · No chat or editing')),
      if (selected != null) TextButton.icon(onPressed: loading ? null : () => select(selected!),
        icon: const Icon(Icons.sync), label: const Text('Refresh observation')),
      if (loading) const LinearProgressIndicator(),
      if (error != null) Padding(padding: const EdgeInsets.all(12), child: Text(error!)),
      if (!loading && agents.isEmpty && error == null)
        const Text('No agents authorized for this account.'),
      if (envelope != null) ...[
        Padding(padding: const EdgeInsets.symmetric(horizontal: 12), child: Text(expired
          ? 'Observation expired — refresh to check connectivity. Declarations remain historical.'
          : 'Gateway observation recent — declaration capture time is separate.')),
        Padding(padding: const EdgeInsets.all(8), child: Wrap(spacing: 8, children: [
          for (final (i, label) in ['Overview', 'Agents', 'Skills', 'Tools'].indexed)
            ChoiceChip(label: Text(label), selected: tab == i,
              onSelected: (_) => setState(() { tab = i; filter = ''; })),
        ])),
        if (tab > 0) Padding(padding: const EdgeInsets.symmetric(horizontal: 12), child: TextField(
          key: ValueKey('$selected-$tab'), decoration: const InputDecoration(labelText: 'Filter declarations'),
          onChanged: (value) => setState(() => filter = value.toLowerCase()),
        )),
        Expanded(child: content()),
      ],
    ]),
  );

  List<Widget> retainedEvidence() {
    final evidence = envelope!['retainedEvidence'] as Map<String, dynamic>?;
    final state = evidence?['state'] ?? 'not_configured';
    if (state != 'matched') {
      return [Text('Retained verification: $state — no matching evidence established.')];
    }
    final expiredReceipt = DateTime.tryParse(evidence!['expiresAt'] as String? ?? '')
        ?.isBefore(DateTime.now()) ?? true;
    if (expired || expiredReceipt) {
      return [const Text('Retained verification: historical — refresh observation and evidence.')];
    }
    return [
      Text('Retained verification: ${evidence['verificationState']} (publisher asserted)',
        style: const TextStyle(fontWeight: FontWeight.bold)),
      const Text('Exact snapshot identity matched. Source-to-release association is a publisher claim, '
        'not runtime attestation. No evaluator executed here.'),
      Text('Run: ${evidence['runId'] ?? 'unavailable'} · Receipt expires: ${evidence['expiresAt']}'),
      for (final row in evidence['contracts'] as List)
        ListTile(dense: true, title: Text('${row['name']}: ${row['state']}'),
          subtitle: Text('${row['caseCount']} declared cases · ${row['observations']} observations')),
    ];
  }

  Widget content() {
    final snapshot = envelope!['snapshot'] as Map<String, dynamic>;
    final nodes = (snapshot['agents'] as List).cast<Map<String, dynamic>>();
    if (tab == 0) {
      return ListView(padding: const EdgeInsets.all(16), children: [
      SelectableText('Agent: ${snapshot['agentId']} · ${snapshot['environment']}\n'
        'Release: ${snapshot['release']} (host asserted)\nProcess: ${snapshot['instanceId']}\n'
        'Generation: ${snapshot['generation']}\nCaptured: ${snapshot['capturedAt']}\n'
        'Gateway received: ${envelope!['gatewayReceivedAt']}\nDigest: ${envelope!['snapshotDigest']}\n'
        'Scope: ${snapshot['scope']}'),
      const SizedBox(height: 12),
      const Text('Doctor: not run here · Behavioral evaluation: not run here', style: TextStyle(fontWeight: FontWeight.bold)),
      ...retainedEvidence(),
      const Text('Source files, resource content and source spans require a release-matched artifact. '
        'Dynamic instructions and tools are not evaluated. Empty inventory is not proof of absence.'),
      for (final limit in (snapshot['limitations'] as List?) ?? [])
        Padding(padding: const EdgeInsets.only(top: 8), child: Text('$limit')),
    ]);
    }
    final rows = <Map<String, dynamic>>[];
    if (tab == 1) {
      rows.addAll(nodes);
    } else {
      for (final node in nodes) {
        if (tab == 3) rows.addAll((node['tools'] as List).cast<Map<String, dynamic>>());
        for (final group in [...node['toolsets'] as List, ...node['bindings'] as List]) {
          rows.addAll(((group[tab == 2 ? 'skills' : 'tools'] as List?) ?? []).cast<Map<String, dynamic>>());
          if (tab == 3 && group['omission'] != null) {
            rows.add({
            'id': group['id'], 'name': group['type'], 'owner': group['owner'], 'omission': group['omission'],
          });
          }
        }
      }
    }
    final matching = rows.where((r) => '${r['name']} ${r['description'] ?? ''}'.toLowerCase().contains(filter)).toList();
    return ListView(padding: const EdgeInsets.all(12), children: [
      Text('${matching.length} of ${rows.length} declarations'),
      if (matching.isEmpty) const Text('No matching declarations; omitted/dynamic areas are not verified.'),
      for (final row in matching) ExpansionTile(key: ValueKey('$selected-$tab-${row['id']}'),
        title: Text('${row['name']}'), subtitle: Text('${row['owner'] ?? row['id']}\n${row['description'] ?? ''}'),
        children: [Padding(padding: const EdgeInsets.all(12), child: Semantics(
          label: const JsonEncoder.withIndent('  ').convert(row),
          excludeSemantics: true,
          child: SelectableText(const JsonEncoder.withIndent('  ').convert(row))))],
      ),
    ]);
  }
}
