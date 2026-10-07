import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;

class ApiException implements Exception {
  const ApiException(this.message, {this.code, this.recoverable = false, this.action});
  final String message;
  final String? code;
  final bool recoverable;
  final String? action;
  bool get canClear => recoverable && action == 'clear_conversation';

  factory ApiException.fromJson(Map<String, dynamic> data) => ApiException(
    data['error']?.toString() ?? 'Operation failed',
    code: data['code'] as String?, recoverable: data['recoverable'] == true,
    action: data['action'] as String?);

  @override
  String toString() => message;
}

/// Client for the adkl dev server.
///
/// Deliberately thin: the server owns all lint rules and validation, so the
/// console never forms its own opinion about whether a skill is valid. That is
/// what keeps text-on-disk canonical -- if the UI could decide, the UI would
/// eventually disagree with CI.
class AdklApi {
  /// Empty base by default, which makes every request same-origin.
  ///
  /// It used to default to `http://127.0.0.1:8787`, and that is a bug you only
  /// see in a browser: the same server serves both the bundle and the API, so
  /// when it runs on any other port the app loads fine and then every call goes
  /// to a port with nothing on it -- "cannot reach adkl server" on a server that
  /// is up and serving you the page. curl against the real port passes
  /// throughout, which is exactly why it survived.
  ///
  /// Relative URLs cannot drift from the port the page came from. Pass `base`
  /// explicitly only for a split setup (`flutter run` on 5000 against the API on
  /// 8787), where the origins genuinely differ.
  AdklApi({this.base = '', this.streamClientFactory, http.Client? client})
      : _injectedClient = client;
  final http.Client Function()? streamClientFactory;
  final http.Client? _injectedClient;

  /// Shared client for every non-stream request. Injectable because holding one
  /// request open is the only way to observe the deadline below; created lazily
  /// so a test that exercises only the stream never opens a real socket factory.
  late final http.Client _client = _injectedClient ?? http.Client();
  http.Client? _chatClient;
  Completer<void>? _chatAbort;

  /// One deadline for every request, rather than a per-endpoint estimate.
  ///
  /// The server serializes on a single request lock, so a config read can sit
  /// behind a worker start that imports an entire host factory; a short deadline
  /// on `/api/agent` would abort a request whose only problem was being queued.
  /// The deadline is for the other case: only `chatStream` used to bound its
  /// wait, so a wedged worker left every other panel spinning forever with no
  /// message -- indistinguishable from a slow one, or from a server that died.
  static const _deadline = Duration(seconds: 130);

  /// Bound one request and name what went wrong, distinguishing a server that
  /// answered too late from one that could not be reached at all. Both used to
  /// reach the panels as a raw `TimeoutException`/`ClientException`
  /// `toString()`, or -- with no deadline at all -- as nothing.
  Future<T> _bounded<T>(String label, Future<T> Function() call) async {
    try {
      return await call().timeout(_deadline);
    } on TimeoutException {
      throw ApiException('$label: the adkl server did not answer within '
          '${_deadline.inSeconds}s. It may still be starting a worker; check its log.');
    } on http.RequestAbortedException {
      // Stop was pressed. It is a `ClientException` subtype, so without this it
      // would be reported to the user as a server they could not reach.
      rethrow;
    } on http.ClientException catch (error) {
      throw ApiException('$label: could not reach the adkl server'
          '${base.isEmpty ? '' : ' at $base'} (${error.message})');
    }
  }

  void cancelChat() {
    if (_chatAbort case final abort? when !abort.isCompleted) abort.complete();
    _chatAbort = null;
    _chatClient?.close();
    _chatClient = null;
  }
  final String base;

  /// Selected environment. Null = base config; 'uat' applies skills.overrides.uat.
  String? env;
  Map<String, dynamic>? testState;

  String _u(String path) => '$base$path${env == null ? '' : '?env=$env'}';

  Future<Map<String, dynamic>> agent() async => _get('/api/agent');
  Future<Map<String, dynamic>> project() async => _get('/api/project');
  Future<Map<String, dynamic>> audit() async => _get('/api/audit');
  Future<List<Finding>> findings() async => ((await _get('/api/findings'))['findings'] as List)
      .map(Finding.fromJson).toList();
  Future<Map<String, dynamic>> projectAction(String action, [Map<String, dynamic> data = const {}]) async {
    final path = action == 'create' ? '/api/agents' : '/api/project/$action';
    final response = await _bounded('POST $path', () => _client.post(Uri.parse(_u(path)),
        headers: {'Content-Type': 'application/json'}, body: jsonEncode(data)));
    final result = jsonDecode(response.body) as Map<String, dynamic>;
    if (response.statusCode >= 400) throw ApiException.fromJson(result);
    return result;
  }

  /// Every agent this server can switch to, plus which is selected.
  Future<AgentList> agents() async =>
      AgentList.fromJson(await _get('/api/agents'));

  /// Switch agents. The server rebinds its workspace and drops the chat
  /// session, so the caller must reload everything and clear the transcript --
  /// a reply from the previous agent left on screen would be read as evidence
  /// about the new one.
  Future<void> selectAgent(String id) async {
    final r = await _bounded('POST /api/agents/select',
        () => _client.post(Uri.parse(_u('/api/agents/select')),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'id': id})));
    if (r.statusCode != 200) {
      throw Exception('switch to $id failed: ${r.statusCode} ${r.body}');
    }
    testState = null;
  }

  Future<List<ToolInfo>> catalog() async {
    final d = await _get('/api/catalog');
    return (d['tools'] as List).map((t) => ToolInfo.fromJson(t)).toList();
  }

  Future<List<NativeWorkflow>> workflows() async {
    final d = await _get('/api/workflows');
    return (d['workflows'] as List).map((w) => NativeWorkflow.fromJson(w)).toList();
  }

  Future<List<SkillSummary>> skills() async {
    final d = await _get('/api/skills');
    return (d['skills'] as List).map((s) => SkillSummary.fromJson(s)).toList();
  }

  Future<SkillDetail> skill(String name) async =>
      SkillDetail.fromJson(await _get('/api/skills/$name'));

  /// Lint an unsaved buffer. Same rules as CI, so the editor cannot mislead.
  Future<LintResult> lint(String name,
      {required String description,
      required List<String> tools,
      SkillBody? doc,
      String? body}) async {
    final r = await _bounded('POST /api/skills/$name/lint',
        () => _client.post(Uri.parse(_u('/api/skills/$name/lint')),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({
              'description': description,
              'tools': tools,
              if (doc != null) 'doc': doc.toJson(),
              if (doc == null) 'body': body ?? '',
            })));
    return LintResult.fromJson(jsonDecode(r.body));
  }

  /// Save. The server refuses (409) if the result would have lint ERRORs;
  /// [force] overrides, and the UI makes that an explicit, visible choice.
  Future<SaveResult> save(String name,
      {required String description,
      required List<String> tools,
      SkillBody? doc,
      String? body,
      String? revision,
      bool force = false}) async {
    final r = await _bounded('PUT /api/skills/$name',
        () => _client.put(Uri.parse(_u('/api/skills/$name')),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({
              'description': description,
              'tools': tools,
              if (doc != null) 'doc': doc.toJson(),
              if (doc == null) 'body': body ?? '',
              'force': force,
              'revision': revision,
            })));
    final d = jsonDecode(r.body) as Map<String, dynamic>;
    return SaveResult(
      saved: d['saved'] == true,
      refused: r.statusCode == 409,
      error: d['error'] as String?,
      findings: ((d['findings'] ?? []) as List).map(Finding.fromJson).toList(),
    );
  }

  /// Replace the features block. The server refuses (409) an ordering the
  /// registry rejects, so an invalid pipeline never reaches agent.yaml.
  Future<SaveResult> saveFeatures(Map<String, dynamic> features) async {
    final r = await _bounded('PUT /api/features',
        () => _client.put(Uri.parse(_u('/api/features')),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'features': features})));
    final d = jsonDecode(r.body) as Map<String, dynamic>;
    return SaveResult(
      saved: d['saved'] == true,
      refused: r.statusCode == 409,
      error: d['error'] as String?,
    );
  }

  Future<Map<String, dynamic>> execution() async => _get('/api/execution');

  Future<void> selectExecution(String profile) async {
    final r = await _bounded('POST /api/execution/select',
        () => _client.post(Uri.parse(_u('/api/execution/select')),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'profile': profile})));
    if (r.statusCode != 200) throw Exception('Execution profile selection failed');
    testState = null;
  }

  /// Preflight: whether a turn can be attempted, and why not.
  Future<ChatStatus> chatStatus() async =>
      ChatStatus.fromJson(await _get('/api/chat'));

  Future<ChatTurn> chatSend(String text) async {
    final r = await _bounded('POST /api/chat',
        () => _client.post(Uri.parse(_u('/api/chat')),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'text': text, if (testState != null) 'state': testState})));
    final d = jsonDecode(r.body) as Map<String, dynamic>;
    if (r.statusCode != 200) {
      return ChatTurn(userText: text, error: d['error']?.toString() ?? r.body);
    }
    return ChatTurn.fromJson(text, d);
  }

  /// One complete filtered text frame, live progress, then a terminal result.
  /// Closing the client aborts the request; the server discards that worker.
  Stream<Map<String, dynamic>> chatStream(String text) async* {
    cancelChat();
    final client = (streamClientFactory ?? http.Client.new)();
    _chatClient = client;
    final abort = Completer<void>();
    _chatAbort = abort;
    var done = false;
    String? displayed;
    try {
      final request = http.AbortableRequest('POST', Uri.parse(_u('/api/chat')),
          abortTrigger: abort.future)
        ..headers.addAll({'Content-Type': 'application/json', 'Accept': 'text/event-stream'})
        ..body = jsonEncode({'text': text, if (testState != null) 'state': testState});
      final response = await _bounded('POST /api/chat', () => client.send(request));
      if (response.statusCode != 200) {
        final body = await response.stream.bytesToString();
        final error = jsonDecode(body) as Map<String, dynamic>;
        throw ApiException.fromJson(error);
      }
      // A stall between frames is its own failure, and saying so beats the raw
      // `TimeoutException` the panel used to print: the turn is unfinished, so
      // the transcript has to be cleared before another one can start.
      await for (final event in decodeChatFrames(response.stream.timeout(_deadline,
          onTimeout: (sink) => sink.addError(ApiException(
              'The reply stopped arriving after ${_deadline.inSeconds}s. '
              'Clear the conversation and retry.',
              recoverable: true, action: 'clear_conversation'))))) {
        if (event['type'] == 'error') throw ApiException.fromJson(event);
        if (event['type'] == 'text') {
          if (displayed != null || event['text'] is! String) throw const FormatException('Invalid reply frame');
          displayed = event['text'] as String;
        }
        if (event['type'] == 'done') {
          final result = event['result'];
          if (result is! Map || result['reply'] != displayed) {
            throw const FormatException('Final reply differs from streamed reply');
          }
          done = true;
        }
        yield event;
        if (done) break;
      }
      if (!done) throw const FormatException('Chat stream ended before completion; clear and retry');
    } finally {
      if (!abort.isCompleted) abort.complete();
      client.close();
      if (identical(_chatClient, client)) _chatClient = null;
      if (identical(_chatAbort, abort)) _chatAbort = null;
    }
  }

  Future<void> chatClear() async {
    final response = await _bounded('POST /api/chat/clear',
        () => _client.post(Uri.parse(_u('/api/chat/clear'))));
    if (response.statusCode != 200) {
      throw ApiException.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
    }
  }

  Future<Map<String, dynamic>> _get(String path) async {
    final r = await _bounded('GET $path', () => _client.get(Uri.parse(_u(path))));
    if (r.statusCode != 200) {
      try {
        throw ApiException.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
      } on FormatException {
        throw ApiException('GET $path failed (${r.statusCode})');
      }
    }
    return jsonDecode(r.body) as Map<String, dynamic>;
  }
}

/// Decode the server's single-line JSON SSE frames across arbitrary UTF-8 chunks.
Stream<Map<String, dynamic>> decodeChatFrames(Stream<List<int>> bytes) async* {
  var buffer = '';
  await for (final chunk in bytes.transform(utf8.decoder)) {
    buffer += chunk;
    if (buffer.length > 8 * 1024 * 1024) throw const FormatException('Chat frame exceeds limit');
    int end;
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      final frame = buffer.substring(0, end);
      buffer = buffer.substring(end + 2);
      if (!frame.startsWith('data: ')) throw const FormatException('Invalid chat frame');
      final event = jsonDecode(frame.substring(6));
      if (event is! Map<String, dynamic> || event['version'] != 1 ||
          !{'progress', 'text', 'done', 'error', 'ping'}.contains(event['type'])) {
        throw const FormatException('Unsupported chat frame');
      }
      yield event;
    }
  }
  if (buffer.isNotEmpty) throw const FormatException('Truncated chat frame');
}

class AgentSummary {
  AgentSummary({
    required this.id,
    required this.name,
    required this.path,
    required this.skills,
    this.model,
    this.error,
  });

  final String id;
  final String name;
  final String path;
  final int skills;
  final String? model;

  /// Set when agent.yaml would not parse. Listed anyway rather than hidden:
  /// "I can see it is broken" is debuggable, "my agent vanished" is not.
  final String? error;

  factory AgentSummary.fromJson(Map<String, dynamic> j) => AgentSummary(
        id: j['id'] ?? '',
        name: j['name'] ?? '',
        path: j['path'] ?? '',
        skills: (j['skills'] ?? 0) as int,
        model: j['model'] as String?,
        error: j['error'] as String?,
      );
}

class AgentList {
  AgentList({required this.agents, this.selected});
  final List<AgentSummary> agents;
  final String? selected;

  factory AgentList.fromJson(Map<String, dynamic> j) => AgentList(
        agents: ((j['agents'] ?? []) as List)
            .map((a) => AgentSummary.fromJson((a as Map).cast<String, dynamic>()))
            .toList(),
        selected: j['selected'] as String?,
      );
}

class ToolInfo {
  ToolInfo({
    required this.name,
    required this.summary,
    required this.sideEffect,
    required this.sideEffectInferred,
    required this.requiresAuth,
    required this.estTokens,
    required this.params,
    this.source = 'ast',
    this.scope = 'unknown',
    this.schemaKnown = true,
  });

  final String name;
  final String summary;
  final String sideEffect; // read | write | render | auth | unknown
  final bool sideEffectInferred;
  final bool requiresAuth;
  final int estTokens;
  final List<ParamInfo> params;
  final String source, scope;
  final bool schemaKnown;

  factory ToolInfo.fromJson(Map<String, dynamic> j) => ToolInfo(
        name: j['name'] ?? '',
        summary: j['summary'] ?? '',
        sideEffect: j['sideEffect'] ?? 'unknown',
        sideEffectInferred: j['sideEffectInferred'] ?? true,
        requiresAuth: j['requiresAuth'] ?? false,
        estTokens: j['estTokens'] ?? 0,
        source: j['source'] ?? 'ast',
        scope: j['scope'] ?? 'unknown',
        schemaKnown: j['schemaKnown'] ?? true,
        params: ((j['params'] ?? []) as List)
            .map((p) => ParamInfo.fromJson(p as Map<String, dynamic>))
            .toList(),
      );
}

class ParamInfo {
  ParamInfo(this.name, this.type, this.required, this.description);
  final String name;
  final String type;
  final bool required;
  final String description;

  factory ParamInfo.fromJson(Map<String, dynamic> j) => ParamInfo(
      j['name'] ?? '', j['type'] ?? '', j['required'] ?? false,
      j['description'] ?? '');
}

class SkillSummary {
  SkillSummary({
    required this.name,
    required this.path,
    required this.overridden,
    required this.description,
    required this.tools,
    required this.estTokens,
    required this.errors,
    required this.warnings,
  });

  final String name;
  final String path;
  final bool overridden;
  final String description;
  final List<String> tools;
  final int estTokens;
  final int errors;
  final int warnings;

  factory SkillSummary.fromJson(Map<String, dynamic> j) => SkillSummary(
        name: j['name'] ?? '',
        path: j['path'] ?? '',
        overridden: j['overridden'] ?? false,
        description: j['description'] ?? '',
        tools: ((j['tools'] ?? []) as List).cast<String>(),
        estTokens: j['estTokens'] ?? 0,
        errors: j['errors'] ?? 0,
        warnings: j['warnings'] ?? 0,
      );
}

class SkillDetail {
  SkillDetail({
    this.editable = true,
    this.revision,
    required this.name,
    required this.path,
    required this.overridden,
    required this.description,
    required this.body,
    required this.doc,
    required this.tools,
    required this.estTokens,
    required this.findings,
  });

  final String name;
  final bool editable;
  final String? revision;
  final String path;
  final bool overridden;
  final String description;
  final String body;

  /// Structured view of the body: sections -> workflows -> steps, and gotchas.
  /// The console edits this instead of raw markdown; `body` is kept only for
  /// the eject path and for anything the parser does not model.
  final SkillBody doc;
  final List<String> tools;
  final int estTokens;
  final List<Finding> findings;

  factory SkillDetail.fromJson(Map<String, dynamic> j) => SkillDetail(
        editable: j['editable'] ?? true,
        revision: j['revision'],
        name: j['name'] ?? '',
        path: j['path'] ?? '',
        overridden: j['overridden'] ?? false,
        description: j['description'] ?? '',
        body: j['body'] ?? '',
        doc: SkillBody.fromJson(
            (j['doc'] as Map<String, dynamic>?) ?? const {}),
        tools: ((j['tools'] ?? []) as List).cast<String>(),
        estTokens: j['estTokens'] ?? 0,
        findings: ((j['findings'] ?? []) as List).map(Finding.fromJson).toList(),
      );
}

/// Mirrors adkl/skill_doc.py. Mutable, because the editor mutates in place and
/// posts the whole doc back; the server diffs it against the on-disk parse so
/// untouched sections stay byte-identical.
class SkillBody {
  SkillBody({required this.preamble, required this.sections});

  String preamble;
  List<DocSection> sections;

  factory SkillBody.fromJson(Map<String, dynamic> j) => SkillBody(
        preamble: j['preamble'] ?? '',
        sections: ((j['sections'] ?? []) as List)
            .map((s) => DocSection.fromJson(s as Map<String, dynamic>))
            .toList(),
      );

  Map<String, dynamic> toJson() => {
        'preamble': preamble,
        'sections': sections.map((s) => s.toJson()).toList(),
      };

  bool get isEmpty => sections.isEmpty && preamble.isEmpty;
}

class DocSection {
  DocSection({
    required this.title,
    required this.kind,
    required this.prose,
    required this.items,
    required this.workflows,
  });

  final String title;
  final String kind; // workflows | gotchas | prose
  String prose;
  List<GotchaItem> items;
  List<DocWorkflow> workflows;

  factory DocSection.fromJson(Map<String, dynamic> j) => DocSection(
        title: j['title'] ?? '',
        kind: j['kind'] ?? 'prose',
        prose: j['prose'] ?? '',
        items: ((j['items'] ?? []) as List)
            .map((i) => GotchaItem.fromJson(i as Map<String, dynamic>))
            .toList(),
        workflows: ((j['workflows'] ?? []) as List)
            .map((w) => DocWorkflow.fromJson(w as Map<String, dynamic>))
            .toList(),
      );

  Map<String, dynamic> toJson() => {
        'title': title,
        'kind': kind,
        'prose': prose,
        'items': items.map((i) => i.toJson()).toList(),
        'workflows': workflows.map((w) => w.toJson()).toList(),
      };
}

class DocWorkflow {
  DocWorkflow({required this.name, required this.when, required this.steps});

  String name;
  String when;
  List<DocStep> steps;

  factory DocWorkflow.fromJson(Map<String, dynamic> j) => DocWorkflow(
        name: j['name'] ?? '',
        when: j['when'] ?? '',
        steps: ((j['steps'] ?? []) as List)
            .map((s) => DocStep.fromJson(s as Map<String, dynamic>))
            .toList(),
      );

  Map<String, dynamic> toJson() =>
      {'name': name, 'when': when, 'steps': steps.map((s) => s.toJson()).toList()};
}

class DocStep {
  DocStep({required this.text, required this.done});
  String text;
  bool done;

  factory DocStep.fromJson(Map<String, dynamic> j) =>
      DocStep(text: j['text'] ?? '', done: j['done'] == true);

  Map<String, dynamic> toJson() => {'text': text, 'done': done};
}

class GotchaItem {
  GotchaItem({required this.label, required this.text});
  String label;
  String text;

  factory GotchaItem.fromJson(Map<String, dynamic> j) =>
      GotchaItem(label: j['label'] ?? '', text: j['text'] ?? '');

  Map<String, dynamic> toJson() => {'label': label, 'text': text};
}

class ChatStatus {
  ChatStatus({required this.available, required this.problems, this.model,
      this.warnings = const [], this.failure});
  final bool available;
  final List<String> problems;
  final List<String> warnings;
  final String? model;
  final ApiException? failure;

  factory ChatStatus.fromJson(Map<String, dynamic> j) => ChatStatus(
        available: j['available'] == true,
        problems: ((j['problems'] ?? []) as List).map((p) => '$p').toList(),
        model: j['model'] as String?,
        failure: j['error'] == null ? null : ApiException.fromJson(j),
        warnings: (((j['runtime'] as Map?)?['warnings'] ?? []) as List)
            .map((p) => '$p').toList(),
      );
}

/// One turn plus its retrieval trace. The trace is the reason this panel exists:
/// a reply cannot tell you that two skills tied and the wrong one won.
class ChatTurn {
  ChatTurn({
    required this.userText,
    this.reply,
    this.error,
    this.matches = const [],
    this.picked,
    this.margin,
    this.fallback = false,
    this.boundTools = const [],
    this.unresolvedTools = const [],
    this.toolTokens = 0,
    this.latencyMs = 0,
    this.modelCalls = 0,
    this.activatedSkills = const [],
    this.widgets = const [],
    this.widgetTools = const [],
    this.executableTools = const [],
    this.unimportableTools = const [],
    this.toolCalls = const [],
    this.toolLoadProblems = const [],
    this.widgetGates = const [],
    this.contract,
    this.contractSources = const [],
    this.contractNote,
    this.outputFindings = const [],
    this.execution,
  });

  final String userText;
  final String? reply;
  final String? error;
  final List<SkillMatch> matches;
  final String? picked;
  final double? margin;
  final bool fallback;
  final List<String> boundTools;
  final List<String> unresolvedTools;
  final int toolTokens;
  final int latencyMs;
  final int modelCalls;
  final List<String> activatedSkills;

  /// A2UI widgets a tool emitted this turn, off event.actions.render_ui_widgets.
  final List<Map<String, dynamic>> widgets;

  /// Bound tools whose module emits a widget, per the catalog's `emits_widget`.
  /// Read from the server rather than guessed from the name: four of this
  /// agent's eleven tools emit via the staged path with no `render_` prefix.
  final List<String> widgetTools;

  /// Bound tools that resolved to a real callable, so could actually run.
  /// Empty means the reply came from model memory alone.
  final List<String> executableTools;

  /// Bound tools the sibling repo could not import. The dangerous case: the
  /// skill declares it, the catalog knows it, and it silently is not there.
  final List<String> unimportableTools;

  /// What the model actually did: {name, args?, status, responseChars?}.
  /// The only field separating "a tool was available" from "a tool ran".
  final List<Map<String, dynamic>> toolCalls;

  /// Loader failures worth showing, e.g. a widget emitter that never loaded.
  final List<String> toolLoadProblems;

  /// Per-spec staged-widget decisions: {key, passed, gate, detail, revived}.
  /// The answer to "why did no widget appear" -- every gate that declined.
  final List<Map<String, dynamic>> widgetGates;

  /// The reply-shaping contract injected before the model wrote, if any. A
  /// reply that restates a card usually means this was null.
  final String? contract;

  /// Spec keys that contributed to the resolved contract.
  final List<String> contractSources;

  final String? contractNote;

  /// Reply mutations made by output_cleanup or pii_redaction this turn.
  final List<String> outputFindings;

  /// Read-only adapter events and prepared requests, not provider accounting.
  final Map<String, dynamic>? execution;

  factory ChatTurn.fromJson(String userText, Map<String, dynamic> j) {
    final t = (j['trace'] as Map<String, dynamic>?) ?? const {};
    return ChatTurn(
      userText: userText,
      reply: j['reply'] as String?,
      error: t['error'] as String?,
      matches: ((t['matches'] ?? []) as List)
          .map((m) => SkillMatch.fromJson(m as Map<String, dynamic>))
          .toList(),
      picked: t['picked'] as String?,
      margin: (t['margin'] as num?)?.toDouble(),
      fallback: t['fallback'] == true,
      boundTools: ((t['boundTools'] ?? []) as List).cast<String>(),
      unresolvedTools: ((t['unresolvedTools'] ?? []) as List).cast<String>(),
      toolTokens: ((t['toolTokens'] ?? 0) as num).toInt(),
      latencyMs: ((t['latencyMs'] ?? 0) as num).toInt(),
      modelCalls: ((t['modelCalls'] ?? 0) as num).toInt(),
      activatedSkills: ((t['activatedSkills'] ?? []) as List).cast<String>(),
      widgets: ((t['widgets'] ?? []) as List)
          .map((w) => (w as Map).cast<String, dynamic>())
          .toList(),
      widgetTools: ((t['widgetTools'] ?? []) as List).cast<String>(),
      executableTools:
          ((t['executableTools'] ?? []) as List).cast<String>(),
      unimportableTools:
          ((t['unimportableTools'] ?? []) as List).cast<String>(),
      toolCalls: ((t['toolCalls'] ?? []) as List)
          .map((c) => (c as Map).cast<String, dynamic>())
          .toList(),
      toolLoadProblems:
          ((t['toolLoadProblems'] ?? []) as List).cast<String>(),
      widgetGates: ((t['widgetGates'] ?? []) as List)
          .map((g) => (g as Map).cast<String, dynamic>())
          .toList(),
      contract: t['contract'] as String?,
      contractSources:
          ((t['contractSources'] ?? []) as List).cast<String>(),
      contractNote: t['contractNote'] as String?,
      outputFindings: ((t['outputFindings'] ?? []) as List).cast<String>(),
      execution: (t['execution'] as Map?)?.cast<String, dynamic>(),
    );
  }
}

class SkillMatch {
  SkillMatch({required this.skill, required this.score, required this.source});
  final String skill;
  final double score;
  final String source;   // embedding | lexical | pinned | fallback

  factory SkillMatch.fromJson(Map<String, dynamic> j) => SkillMatch(
        skill: j['skill'] ?? '',
        score: ((j['score'] ?? 0) as num).toDouble(),
        source: j['source'] ?? '',
      );
}

class Finding {
  Finding(this.severity, this.rule, this.skill, this.message,
      {this.displayTitle, this.remediation, this.subjectKind, this.reportCategory});
  final String? displayTitle, remediation, subjectKind, reportCategory;
  String get title => displayTitle ?? (rule.isEmpty ? 'Check details' : rule.toLowerCase().replaceAll('_', ' '));
  String get nextStep => remediation ?? 'Guidance unavailable from this server. Run Doctor with a matching SDK version.';
  bool get targetsTool => subjectKind == 'tool';
  final String severity; // ERROR | WARN | INFO
  final String rule;
  final String skill;
  final String message;

  bool get isError => severity == 'ERROR';

  /// Which question this finding answers, derived from the rule name.
  ///
  /// Grouped by OUTCOME rather than by severity, because with ~20 rules a flat
  /// severity list cannot tell "this skill is wired wrong" from "this skill is
  /// expensive" from "this skill contradicts the prompt" -- three different
  /// people fix those, and only the first blocks a save.
  ///
  /// Derived from the name rather than a hand-maintained map: a new rule lands in
  /// a category automatically, and the naming convention is already consistent
  /// because the rules were written as families.
  String get category {
    if (reportCategory != null) return reportCategory!;
    if (rule.startsWith('TURN_COST') ||
        rule.startsWith('SKILL_BODY') ||
        rule.startsWith('TOOL_DECLARATION') ||
        rule.startsWith('TOOL_DOCSTRING') ||
        rule.startsWith('REFERENCES_')) {
      return 'Cost';
    }
    if (rule.contains('CONTRADICT') ||
        rule.contains('PROMISED') ||
        rule.contains('INSTRUCTED_DIFFERENTLY') ||
        rule.contains('PROHIBITION')) {
      return 'Conflict';
    }
    // Advisory presentation coverage only. PRESENTATION_METADATA_INVALID is
    // deliberately left in 'Correctness': it is an ERROR that blocks the save,
    // which is what that bucket means here, and grouping it with the two
    // advisory rules would hide that.
    if (rule.startsWith('WIDGET_OUTPUT')) {
      return 'Presentation';
    }
    if (rule.startsWith('TOOL_') || rule.contains('AUTH_GATE')) {
      return 'Tools';
    }
    if (rule.startsWith('DESCRIPTION_') || rule.startsWith('NAME_')) {
      return 'Routing';
    }
    return 'Correctness';
  }

  factory Finding.fromJson(dynamic j) => Finding(j['severity'] ?? 'INFO',
      j['rule'] ?? '', j['subject']?['name'] ?? j['skill'] ?? '', j['message'] ?? '',
      displayTitle: j['title'], remediation: j['remediation'],
      subjectKind: j['subject']?['kind'], reportCategory: j['category']);
}

class LintResult {
  LintResult(this.findings, this.estTokens, [this.allFindings]);
  final List<Finding>? allFindings;
  final List<Finding> findings;
  final int estTokens;

  factory LintResult.fromJson(Map<String, dynamic> j) => LintResult(
        ((j['findings'] ?? []) as List).map(Finding.fromJson).toList(),
        j['estTokens'] ?? 0,
        (j['allFindings'] as List?)?.map(Finding.fromJson).toList(),
      );
}

class SaveResult {
  SaveResult(
      {required this.saved,
      required this.refused,
      this.error,
      this.findings = const []});
  final bool saved;
  final bool refused;
  final String? error;
  final List<Finding> findings;
}

/// Read-only declaration from a native factory; execution is observed in Test.
class NativeWorkflow {
  const NativeWorkflow({required this.name, required this.description,
    required this.operation, required this.agent, this.graph});
  final String name, description, operation, agent;
  final NativeWorkflowGraph? graph;
  factory NativeWorkflow.fromJson(dynamic value) {
    final j = value as Map<String, dynamic>;
    return NativeWorkflow(name: j['name'] as String,
      description: j['description'] as String,
      operation: j['operation'] as String? ?? 'host-owned', agent: j['agent'] as String,
      graph: j['graph'] is Map ? NativeWorkflowGraph.fromJson(j['graph']) : null);
  }
}

class NativeWorkflowGraph {
  const NativeWorkflowGraph({required this.available, required this.nodes,
    required this.edges});
  final bool available;
  final List<NativeWorkflowNode> nodes;
  final List<NativeWorkflowEdge> edges;
  factory NativeWorkflowGraph.fromJson(dynamic value) {
    final j = value as Map;
    return NativeWorkflowGraph(available: j['available'] == true,
      nodes: (j['nodes'] as List? ?? []).map((n) => NativeWorkflowNode.fromJson(n)).toList(),
      edges: (j['edges'] as List? ?? []).map((e) => NativeWorkflowEdge.fromJson(e)).toList());
  }
}

class NativeWorkflowNode {
  const NativeWorkflowNode(this.name, this.kind, this.description);
  final String name, kind, description;
  factory NativeWorkflowNode.fromJson(dynamic value) {
    final j = value as Map;
    return NativeWorkflowNode(j['name'] as String, j['kind'] as String,
      j['description'] as String? ?? '');
  }
}

class NativeWorkflowEdge {
  const NativeWorkflowEdge(this.from, this.to, this.route);
  final String from, to;
  final Object? route;
  factory NativeWorkflowEdge.fromJson(dynamic value) {
    final j = value as Map;
    return NativeWorkflowEdge(j['from'] as String, j['to'] as String, j['route']);
  }
  String get label => route == null ? '' : route is List
    ? (route as List).join(' / ') : route == '__DEFAULT__' ? 'default' : '$route';
}
