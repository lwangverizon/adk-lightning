import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:adkl_console/api.dart';
import 'package:adkl_console/main.dart';
import 'package:adkl_console/workflow_graph.dart';

class EditorApi extends AdklApi {
  String description = 'Teach math';
  String? savedRevision;
  final issue = Finding.fromJson({'severity': 'WARN', 'rule': 'SKILL_BODY_LARGE', 'skill': 'other', 'message': 'Shorten this procedure'});
  @override
  Future<Map<String, dynamic>> agent() async => {'agent': {'name': 'tutor', 'factory': 'agent:build'}};
  @override
  Future<AgentList> agents() async => throw Exception('No picker');
  @override
  Future<List<ToolInfo>> catalog() async => [ToolInfo.fromJson({'name': 'lookup', 'summary': 'Read facts'})];
  @override
  Future<List<SkillSummary>> skills() async => [SkillSummary.fromJson({'name': 'math', 'description': description, 'tools': ['lookup']})];
  @override
  Future<List<Finding>> findings() async => [issue];
  @override
  Future<SkillDetail> skill(String name) async => SkillDetail.fromJson({'name': name, 'description': description,
    'body': 'Ask one question.', 'tools': ['lookup'], 'editable': true, 'revision': 'source-hash'});
  @override
  Future<LintResult> lint(String name, {required String description, required List<String> tools, SkillBody? doc, String? body}) async => LintResult([], 10, [issue]);
  @override
  Future<SaveResult> save(String name, {required String description, required List<String> tools,
    SkillBody? doc, String? body, String? revision, bool force = false}) async {
    this.description = description;
    savedRevision = revision;
    return SaveResult(saved: true, refused: false);
  }
  @override
  Future<ChatStatus> chatStatus() async => ChatStatus(available: false, problems: ['Offline fixture']);
  @override
  Future<Map<String, dynamic>> execution() async => {};
}

class StaleEditorApi extends EditorApi {
  bool stale = true;
  bool failClear = true;
  @override
  Future<List<ToolInfo>> catalog() async {
    if (stale) {
      throw const ApiException('Runtime source changed',
        recoverable: true, action: 'clear_conversation');
    }
    return super.catalog();
  }
  @override
  Future<void> chatClear() async {
    if (failClear) throw const ApiException('Reset failed');
    stale = false;
  }
}

class WorkflowEditorApi extends EditorApi {
  @override
  Future<Map<String, dynamic>> agent() async =>
    {'mode': 'factory', 'agent': {'name': 'tutor', 'factory': 'agent:build'}};
  @override
  Future<List<NativeWorkflow>> workflows() async => [
    const NativeWorkflow(name: 'device_discovery',
      description: 'Find available devices', operation: 'device_discovery',
      agent: 'tutor', graph: NativeWorkflowGraph(available: true, nodes: [
        NativeWorkflowNode('__START__', 'StartNode', ''),
        NativeWorkflowNode('choose', 'FunctionNode', ''),
        NativeWorkflowNode('left', 'FunctionNode', ''),
        NativeWorkflowNode('right', 'FunctionNode', ''),
        NativeWorkflowNode('finish', 'FunctionNode', ''),
        NativeWorkflowNode('join', 'FunctionNode', ''),
      ], edges: [
        NativeWorkflowEdge('__START__', 'choose', null),
        NativeWorkflowEdge('choose', 'left', 'a'),
        NativeWorkflowEdge('choose', 'right', 'b'),
        NativeWorkflowEdge('left', 'join', null),
        NativeWorkflowEdge('right', 'join', null),
        NativeWorkflowEdge('choose', 'finish', 'done'),
        NativeWorkflowEdge('join', 'choose', 'retry'),
      ])),
    const NativeWorkflow(name: 'uneven', description: 'Uneven branch lengths',
      operation: 'uneven', agent: 'tutor', graph: NativeWorkflowGraph(available: true,
        nodes: [
          NativeWorkflowNode('__START__', 'StartNode', ''),
          NativeWorkflowNode('split', 'FunctionNode', ''),
          NativeWorkflowNode('short', 'FunctionNode', ''),
          NativeWorkflowNode('long1', 'FunctionNode', ''),
          NativeWorkflowNode('long2', 'FunctionNode', ''),
          NativeWorkflowNode('join', 'FunctionNode', ''),
        ], edges: [
          NativeWorkflowEdge('__START__', 'split', null),
          NativeWorkflowEdge('split', 'short', 'short'),
          NativeWorkflowEdge('split', 'long1', 'long'),
          NativeWorkflowEdge('long1', 'long2', null),
          NativeWorkflowEdge('short', 'join', null),
          NativeWorkflowEdge('long2', 'join', null),
        ])),
    const NativeWorkflow(name: 'device_discovery',
      description: 'Other agent workflow', operation: 'device_discovery',
      agent: 'other'),
  ];
}

class WorkflowOnlyApi extends WorkflowEditorApi {
  @override
  Future<List<SkillSummary>> skills() async => [];
}

void main() {
  testWidgets('uneven branches place the join after every incoming node', (tester) async {
    tester.view.physicalSize = const Size(1600, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(MaterialApp(home: ConsoleShell(api: WorkflowEditorApi())));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Workflows'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('uneven'));
    await tester.pumpAndSettle();
    final graph = find.byType(WorkflowGraphView);
    final join = tester.getTopLeft(find.descendant(of: graph,
      matching: find.text('join')));
    final longNode = tester.getTopLeft(find.descendant(of: graph,
      matching: find.text('long2')));
    final shortNode = tester.getTopLeft(find.descendant(of: graph,
      matching: find.text('short')).last);
    expect(join.dx, greaterThan(longNode.dx));
    expect(join.dx, greaterThan(shortNode.dx));
  });


  testWidgets('workflow-only factory opens its first workflow', (tester) async {
    tester.view.physicalSize = const Size(1600, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(MaterialApp(home: ConsoleShell(api: WorkflowOnlyApi())));
    await tester.pumpAndSettle();
    expect(find.text('Read-only native workflow declaration'), findsOneWidget);
    expect(find.text('Agent: tutor'), findsOneWidget);
  });


  testWidgets('factory workflows appear in the library and read-only middle pane', (tester) async {
    tester.view.physicalSize = const Size(1600, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(MaterialApp(home: ConsoleShell(api: WorkflowEditorApi())));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Workflows'));
    await tester.pumpAndSettle();
    expect(find.text('device_discovery'), findsNWidgets(2));
    await tester.tap(find.text('device_discovery').first);
    await tester.pumpAndSettle();
    expect(find.text('Read-only native workflow declaration'), findsOneWidget);
    expect(find.text('Find available devices'), findsWidgets);
    expect(find.text('Native operation: device_discovery'), findsOneWidget);
    expect(find.text('Agent: tutor'), findsOneWidget);
    expect(find.textContaining('Declared graph · 6 nodes · 7 edges'), findsOneWidget);
    expect(find.descendant(of: find.byType(WorkflowGraphView),
      matching: find.byType(InteractiveViewer)), findsOneWidget);
    expect(find.descendant(of: find.byType(WorkflowGraphView),
      matching: find.byType(CustomPaint)), findsWidgets);
    expect(find.text('START'), findsOneWidget);
    expect(find.text('choose'), findsOneWidget);
    expect(find.text('left'), findsOneWidget);
    expect(find.text('right'), findsOneWidget);
    expect(find.text('join'), findsOneWidget);
    expect(find.text('finish'), findsOneWidget);
    expect(find.text('a'), findsOneWidget);
    expect(find.text('b'), findsOneWidget);
    expect(find.text('retry'), findsOneWidget);
    final graphView = find.byType(WorkflowGraphView);
    final finish = tester.getTopLeft(find.descendant(of: graphView,
      matching: find.text('finish')));
    final choose = tester.getTopLeft(find.descendant(of: graphView,
      matching: find.text('choose')));
    expect(finish.dx, greaterThan(choose.dx));
    await tester.tap(find.text('other · Other agent workflow'));
    await tester.pumpAndSettle();
    expect(find.text('Agent: other'), findsOneWidget);
    expect(find.text('Graph topology unavailable for this workflow.'), findsOneWidget);
    expect(find.text('Save'), findsNothing);
    await tester.tap(find.text('Skills').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('math').first);
    await tester.pumpAndSettle();
    expect(find.text('DESCRIPTION'), findsOneWidget);
  });


  testWidgets('a stale factory during page load can recover without a restart', (tester) async {
    tester.view.physicalSize = const Size(1600, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = StaleEditorApi();
    await tester.pumpWidget(MaterialApp(home: ConsoleShell(api: api)));
    await tester.pumpAndSettle();
    expect(find.text('Project changed'), findsOneWidget);
    await tester.tap(find.text('Clear conversation'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Could not clear conversation'), findsOneWidget);
    api.failClear = false;
    await tester.tap(find.text('Clear conversation'));
    await tester.pumpAndSettle();
    expect(find.text('Project changed'), findsNothing);
    expect(find.text('DESCRIPTION'), findsOneWidget);
  });

  testWidgets('native skills use original editable layout and all-agent findings', (tester) async {
    tester.view.physicalSize = const Size(1600, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = EditorApi();
    await tester.pumpWidget(MaterialApp(home: ConsoleShell(api: api)));
    await tester.pumpAndSettle();
    expect(find.text('Findings'), findsOneWidget);
    expect(find.text('DESCRIPTION'), findsOneWidget);
    expect(find.text('Save'), findsOneWidget);
    expect(find.text('Entire current agent'), findsOneWidget);
    expect(find.text('Doctor has not run'), findsOneWidget);
    expect(find.text('Shorten this procedure'), findsNothing);
    final description = find.byWidgetPredicate((widget) => widget is TextField && widget.controller?.text == 'Teach math');
    await tester.enterText(description, 'Teach math with examples');
    await tester.pump(const Duration(milliseconds: 600));
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(api.description, 'Teach math with examples');
    expect(api.savedRevision, 'source-hash');
    expect(find.text('unsaved'), findsNothing);
  });
}
