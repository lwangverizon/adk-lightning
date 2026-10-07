import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:adkl_console/configuration.dart';
import 'package:adkl_console/theme.dart';

/// The agent payload the server returns, trimmed to what this screen reads.
final _agent = <String, dynamic>{
  'agent': {'name': 'prospect_minimal', 'model': 'gemini-3.5-flash'},
  'skillCount': 3,
  'toolCount': 11,
  'retrieval': {'top_k': 3, 'min_score': 0.15},
  'compiled': {
    'text': '<role>You are ExampleCo.</role>',
    'chars': 4703,
    'estTokens': 1175,
    'slotsFilled': {'RESPONSE_STANDARDS': 1768, 'SIMPLICITY_SOURCE_OF_TRUTH': 2091},
    'requiredState': ['today'],
    'optionalState': ['pageName', 'geoDetails'],
    'passthrough': <String>[],
  },
  'features': {
    'active': ['skill_retrieval', 'history_slicing'],
    'declared': <String>[],
    'config': {
      'skill_retrieval': {'top_k': 3, 'bm25': true},
      'history_slicing': {'recent_turns': 5},
    },
    'warnings': ['skill_retrieval is experimental: its options may change.'],
    'error': null,
  },
  'featureRegistry': [
    {
      'name': 'skill_retrieval', 'kind': 'toolset', 'stage': 'experimental',
      'summary': 'Select skills per turn by embedding similarity.',
      'defaultOn': false, 'mayShortCircuit': false,
      'reads': <String>[], 'writes': ['agent.tools'], 'mustRunAfter': <String>[],
      'options': [
        {'key': 'top_k', 'type': 'int', 'default': 8, 'help': 'Matches considered.'},
      ],
    },
    {
      'name': 'history_slicing', 'kind': 'model_callback', 'stage': 'stable',
      'summary': 'Trim the conversation to the last N turns.',
      'defaultOn': false, 'mayShortCircuit': false,
      'reads': <String>[], 'writes': ['llm_request.contents'],
      'mustRunAfter': <String>[],
      'options': [
        {'key': 'recent_turns', 'type': 'int', 'default': 5, 'help': 'Turns kept.'},
      ],
    },
    {
      'name': 'semantic_cache', 'kind': 'model_callback', 'stage': 'wip',
      'summary': 'Serve a previous answer when close enough.',
      'defaultOn': false, 'mayShortCircuit': true,
      'reads': ['llm_request.contents'], 'writes': <String>[],
      'mustRunAfter': ['history_slicing'],
      'options': <Map<String, dynamic>>[],
    },
  ],
};

Future<void> _pump(WidgetTester t,
    {Future<void> Function(Map<String, dynamic>)? onSave}) async {
  await t.pumpWidget(MaterialApp(
    theme: AdklTheme.light(),
    home: Scaffold(
      body: ConfigurationScreen(
        agent: _agent,
        onSaveFeatures: onSave ?? (_) async {},
      ),
    ),
  ));
}

void main() {
  testWidgets('factory counts remain unknown until explicit inspection', (t) async {
    await t.pumpWidget(MaterialApp(home: Scaffold(body: ConfigurationScreen(
      agent: {..._agent, 'mode': 'factory', 'skillCount': null, 'toolCount': null},
      onSaveFeatures: (_) async {},
    ))));
    await t.drag(find.byType(ListView).first, const Offset(0, -260));
    await t.pumpAndSettle();
    expect(find.text('Factory owns skills · inspect in Project'), findsOneWidget);
    expect(find.text('Factory owns tools · inspect in Project'), findsOneWidget);
    expect(find.text('0 in this agent'), findsNothing);
  });

  testWidgets('lists configuration topics with counts', (t) async {
    await _pump(t);
    // 'Identity' is now a section header, and 'Brand' is the card --
    // matching Sierra's grouping.
    expect(find.text('IDENTITY'), findsOneWidget);
    expect(find.text('Brand'), findsOneWidget);
    expect(find.text('System prompt'), findsOneWidget);
    // Sierra shows a one-line subtitle per card; ours carries live counts.
    expect(find.text('2 of 3 selected'), findsOneWidget);  // fixture has 3
    // 9 cards across 4 sections now, so the lower ones need a scroll.
    await t.drag(find.byType(ListView).first, const Offset(0, -260));
    await t.pumpAndSettle();
    expect(find.text('3 in this agent'), findsOneWidget);
    expect(find.text('11 in the catalog'), findsOneWidget);
  });

  testWidgets('shows an empty state until a topic is chosen', (t) async {
    await _pump(t);
    expect(find.text('No configuration selected'), findsOneWidget);
    await t.tap(find.text('Brand'));
    await t.pumpAndSettle();
    expect(find.text('No configuration selected'), findsNothing);
    expect(find.text('prospect_minimal'), findsOneWidget);
  });

  testWidgets('system prompt pane shows the compiled result', (t) async {
    await _pump(t);
    await t.tap(find.text('System prompt'));
    await t.pumpAndSettle();
    expect(find.text('4703'), findsOneWidget);
    expect(find.text('~1175'), findsOneWidget);
    // Required vs optional state must be distinguished: one raises, one doesn't.
    expect(find.textContaining('raises a KeyError'), findsOneWidget);
  });

  testWidgets('active features are numbered by pipeline position', (t) async {
    await _pump(t);
    await t.tap(find.text('Features'));
    await t.pumpAndSettle();
    // skill_retrieval is first, history_slicing second.
    expect(find.text('1'), findsOneWidget);
    expect(find.text('2'), findsOneWidget);
    expect(find.byType(Switch), findsNWidgets(3));  // registry fixture size
  });

  testWidgets('non-stable stage and short-circuit are flagged', (t) async {
    await _pump(t);
    await t.tap(find.text('Features'));
    await t.pumpAndSettle();
    expect(find.text('wip'), findsOneWidget);
    expect(find.text('experimental'), findsOneWidget);
    // A feature that halts its hook must say so -- it silences later features
    // and any hand-written callback on the same hook.
    expect(find.text('short-circuits'), findsOneWidget);
  });

  testWidgets('toggling sends a pipeline the server can validate', (t) async {
    Map<String, dynamic>? sent;
    await _pump(t, onSave: (f) async => sent = f);
    await t.tap(find.text('Features'));
    await t.pumpAndSettle();
    await t.tap(find.byType(Switch).first);
    await t.pumpAndSettle();
    expect(sent, isNotNull);
    expect(sent!['pipeline'], isA<List>());
    // Turning one off leaves the other active rather than resetting everything.
    expect((sent!['pipeline'] as List).contains('history_slicing'), isTrue);
  });

  testWidgets('toggling round-trips declared config, never merged config', (t) async {
    // It used to write 'config' back. That is defaults-merged with `enabled`
    // stripped, so one unrelated toggle froze every inherited default into the
    // file and deleted any `enabled: false` kill switch -- an operator control
    // this editor has no business touching.
    Map<String, dynamic>? sent;
    final agent = Map<String, dynamic>.from(_agent);
    agent['features'] = {
      ...(_agent['features'] as Map<String, dynamic>),
      'declaredConfig': {
        'history_slicing': {'recent_turns': 5},
        'output_cleanup': {'enabled': false},
      },
    };
    await t.pumpWidget(MaterialApp(theme: AdklTheme.light(), home: Scaffold(
      body: ConfigurationScreen(
        agent: agent, onSaveFeatures: (f) async => sent = f))));
    await t.tap(find.text('Features'));
    await t.pumpAndSettle();
    await t.tap(find.byType(Switch).first);
    await t.pumpAndSettle();
    expect(sent!['output_cleanup'], {'enabled': false});
    expect(sent!['history_slicing'], {'recent_turns': 5});
    // 'bm25' lives only in the merged view, so its presence would prove laundering.
    expect(sent!.containsKey('skill_retrieval'), isFalse);
  });

  testWidgets('missing support data never claims runtime enforcement', (t) async {
    await _pump(t);
    await t.tap(find.text('Features'));
    await t.pumpAndSettle();
    expect(find.text('preview: unsupported'), findsNWidgets(3));
    final switches = t.widgetList<Switch>(find.byType(Switch)).toList();
    expect(switches[0].onChanged, isNotNull);
    expect(switches[2].onChanged, isNull);
  });

  testWidgets('selected identity gate is shown as blocked without its adapter', (t) async {
    final agent = Map<String, dynamic>.from(_agent);
    agent['features'] = {
      'active': ['identity_gate'],
      'runtime': {
        'features': [
          {'name': 'identity_gate', 'requested': true, 'status': 'unsupported',
           'detail': 'Identity gating has no runtime callback.'}
        ]
      }
    };
    await t.pumpWidget(MaterialApp(theme: AdklTheme.light(), home: Scaffold(
      body: ConfigurationScreen(agent: agent, onSaveFeatures: (_) async {}))));
    await t.ensureVisible(find.text('Guardrails'));
    await t.tap(find.text('Guardrails'));
    await t.pumpAndSettle();
    expect(find.text('Identity gate selected; preview blocked'), findsOneWidget);
    expect(find.text('Identity gate is active'), findsNothing);
    expect(find.text('Identity gating has no runtime callback.'), findsOneWidget);
  });
}
