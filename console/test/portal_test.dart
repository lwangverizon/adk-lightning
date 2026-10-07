import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:adkl_console/portal_main.dart';
import 'package:adkl_console/rail.dart';
import 'package:adkl_console/section_editor.dart';
import 'package:adkl_console/tool_detail.dart';
import 'package:adkl_console/theme.dart';
import 'package:adkl_console/workflow_graph.dart';

Map<String, dynamic> finding(
  String severity,
  String rule,
  String? skill,
  String category,
  String title, {
  String kind = 'skill',
}) => {
  'severity': severity,
  'rule': rule,
  'skill': skill,
  'subject': {'kind': skill == null ? 'project' : kind, 'name': skill},
  'category': category,
  'title': title,
  'message': '$title message',
  'remediation': '$title next step',
};

Map<String, dynamic> snapshot(String id, {List<dynamic>? findings}) => {
  'schemaVersion': 1,
  'kind': 'adkl_static_inspection',
  'agent': {'id': id},
  'identity': {'artifact': '$id-revision', 'sdkRevision': 'fixture'},
  'exportedAt': '2026-09-25',
  'report': {
    'sourceDigest': 'fixture',
    'freshness': 'current',
    'profile': 'inspection',
    'checks': [
      {'id': 'native', 'status': 'findings', 'detail': 'Native declarations'},
      {'id': 'behavior', 'status': 'not_checked', 'detail': 'Not run'},
    ],
    'findings': findings ?? [],
    'limitations': [],
  },
  'inventory': {
    'limitations': [],
    'skills': [
      {
        'name': '$id-skill',
        'owner': 'fixture',
        'description': 'Skill description',
        'tools': ['lookup'],
        'instructions': '## Workflows\n### Search\n- [ ] Call lookup',
        'doc': {
          'preamble': 'Important preamble',
          'sections': [
            {
              'title': 'Workflows',
              'kind': 'workflows',
              'workflows': [
                {
                  'name': 'Search',
                  'when': 'Customer asks',
                  'steps': [
                    {'text': 'Call lookup', 'done': false},
                  ],
                },
              ],
            },
          ],
        },
      },
    ],
    'tools': [
      {
        'name': 'lookup',
        'owner': 'fixture',
        'description': 'Catalog lookup',
        'declaration': {
          'parameters_json_schema': {
            'properties': {
              'query': {'type': 'string'},
              'limit': {
                'type': ['integer', 'null'],
              },
            },
            'required': ['query'],
          },
        },
      },
    ],
    'workflows': [
      {
        'name': 'discovery',
        'agent': id,
        'operation': 'discover',
        'description': 'Discovery flow',
        'graph': {
          'available': true,
          'nodes': [
            {'name': '__START__', 'kind': 'StartNode'},
            {'name': 'choose', 'kind': 'FunctionNode'},
          ],
          'edges': [
            {'from': '__START__', 'to': 'choose', 'route': 'ready'},
            {'from': 'choose', 'to': 'choose', 'route': 'retry'},
          ],
        },
      },
    ],
  },
};

class FixtureApi extends PortalApi {
  final slow = Completer<Map<String, dynamic>>();
  @override
  Future<Map<String, dynamic>> load(String file) async {
    if (file == 'registry.json') {
      return {
        'schemaVersion': 1,
        'agents': [
          {'id': 'a', 'name': 'Agent A', 'snapshot': 'a.json'},
          {'id': 'b', 'name': 'Agent B', 'snapshot': 'b.json'},
          {'id': 'missing', 'name': 'Not published', 'snapshot': null},
          {
            'id': 'failed',
            'name': 'Invalid snapshot',
            'snapshot': 'failed.json',
          },
        ],
      };
    }
    if (file == 'a.json') return slow.future;
    if (file == 'failed.json') throw const FormatException('fixture');
    return snapshot('b');
  }
}

Future<void> switchAgent(WidgetTester t, String name) async {
  await t.tap(find.byTooltip('Switch agent'));
  await t.pump();
  await t.pump(const Duration(milliseconds: 500));
  await t.tap(find.text(name).last);
  await t.pumpAndSettle();
}

Future<void> library(WidgetTester t, double width) async {
  if (width < 1100) {
    await t.tap(find.byTooltip('Open library'));
    await t.pumpAndSettle();
  }
}

void main() {
  testWidgets(
    'rollover cannot substitute another agent when selected agent disappears',
    (t) async {
      var registries = 0, snapshots = 0;
      final api = PortalApi(
        client: MockClient((request) async {
          if (request.url.path.endsWith('registry.json')) {
            final id = ++registries == 1 ? 'a' : 'b';
            return http.Response(
              jsonEncode({
                'schemaVersion': 1,
                'agents': [
                  {'id': id, 'name': id, 'snapshot': '$id.json'},
                ],
              }),
              200,
            );
          }
          snapshots++;
          return http.Response('', 404);
        }),
      );
      await t.pumpWidget(
        MaterialApp(
          theme: AdklTheme.light(),
          home: PortalShell(api: api),
        ),
      );
      await t.pumpAndSettle();
      expect(registries, 2);
      expect(snapshots, 1);
      expect(
        find.textContaining('Publication changed while loading'),
        findsOneWidget,
      );
      expect(find.text('b-skill'), findsNothing);
      api.close();
    },
  );
  for (final status in [404, 403, 500]) {
    testWidgets('rollout recovery is bounded and only retries 404 ($status)', (
      t,
    ) async {
      var registries = 0, snapshots = 0;
      final api = PortalApi(
        client: MockClient((request) async {
          expect(request.headers['Cache-Control'], 'no-cache');
          if (request.url.path.endsWith('registry.json')) {
            registries++;
            return http.Response(
              jsonEncode({
                'schemaVersion': 1,
                'uiBuild': 'other-build',
                'agents': [
                  {
                    'id': 'a',
                    'name': 'Agent A',
                    'snapshot': registries == 1 ? 'old.json' : 'new.json',
                  },
                ],
              }),
              200,
            );
          }
          snapshots++;
          if (request.url.path.endsWith('new.json')) {
            return http.Response(jsonEncode(snapshot('a')), 200);
          }
          return http.Response('', status);
        }),
      );
      await t.pumpWidget(
        MaterialApp(
          theme: AdklTheme.light(),
          home: PortalShell(api: api),
        ),
      );
      await t.pumpAndSettle();
      expect(registries, status == 404 ? 2 : 1);
      expect(snapshots, status == 404 ? 2 : 1);
      expect(find.textContaining('A different UI build'), findsOneWidget);
      if (status == 404) {
        expect(find.text('a-skill'), findsWidgets);
        expect(
          find.text('Publication refreshed after a rollout.'),
          findsOneWidget,
        );
      } else {
        expect(
          find.textContaining('Snapshot unavailable or invalid'),
          findsOneWidget,
        );
      }
      api.close();
    });
  }
  testWidgets('continuous missing snapshots stop after one retry', (t) async {
    var registries = 0, snapshots = 0;
    final api = PortalApi(
      client: MockClient((request) async {
        if (request.url.path.endsWith('registry.json')) {
          registries++;
          return http.Response(
            jsonEncode({
              'schemaVersion': 1,
              'agents': [
                {'id': 'a', 'name': 'Agent A', 'snapshot': 'missing.json'},
              ],
            }),
            200,
          );
        }
        snapshots++;
        return http.Response('', 404);
      }),
    );
    await t.pumpWidget(
      MaterialApp(
        theme: AdklTheme.light(),
        home: PortalShell(api: api),
      ),
    );
    await t.pumpAndSettle();
    expect(registries, 2);
    expect(snapshots, 2);
    expect(
      find.textContaining('Publication changed while loading'),
      findsOneWidget,
    );
    expect(find.text('Retry publication'), findsOneWidget);
    expect(find.text('a-skill'), findsNothing);
    api.close();
  });
  for (final width in [390.0, 1440.0]) {
    testWidgets(
      'original library, read-only sections, tool detail and workflow graph at $width',
      (t) async {
        t.view.physicalSize = Size(width, 1000);
        t.view.devicePixelRatio = 1;
        addTearDown(t.view.resetPhysicalSize);
        addTearDown(t.view.resetDevicePixelRatio);
        final api = FixtureApi();
        api.slow.complete(snapshot('a'));
        await t.pumpWidget(
          MaterialApp(
            theme: AdklTheme.light(),
            home: PortalShell(api: api),
          ),
        );
        await t.pumpAndSettle();
        expect(find.text('a-skill'), findsWidgets);
        await t.scrollUntilVisible(
          find.byType(SectionEditor),
          200,
          scrollable: find.byType(Scrollable).last,
        );
        expect(find.byType(SectionEditor), findsOneWidget);
        expect(
          t.widget<SectionEditor>(find.byType(SectionEditor)).readOnly,
          isTrue,
        );
        await t.tap(find.text('Raw'));
        await t.pumpAndSettle();
        expect(
          find.text('## Workflows\n### Search\n- [ ] Call lookup'),
          findsOneWidget,
        );
        await t.tap(find.text('Sections'));
        await t.pumpAndSettle();
        expect(find.byTooltip('Delete workflow Search'), findsNothing);
        expect(find.text('Save'), findsNothing);
        expect(find.text('Send'), findsNothing);
        expect(find.text('Run Doctor'), findsNothing);
        final editable = t
            .widgetList<EditableText>(find.byType(EditableText))
            .where((e) => !e.readOnly);
        expect(
          editable.length,
          width >= 1100 ? 1 : 0,
        ); // Only library search may edit.
        expect(find.byType(TextField), findsWidgets);
        for (final hidden in [
          'Run Doctor',
          'Rerun Doctor',
          'Chat',
          'Project',
          'Test',
          'Create agent',
        ]) {
          expect(find.text(hidden), findsNothing);
        }
        expect(find.byTooltip('Unbind lookup'), findsNothing);
        expect(find.textContaining('Edit '), findsNothing);
        expect(find.text(readOnlyNotice), findsOneWidget);
        await library(t, width);
        expect(find.byType(NavRail), findsOneWidget);
        await t.tap(find.text('Tools').first);
        await t.pumpAndSettle();
        await t.tap(find.text('lookup').first);
        await t.pumpAndSettle();
        expect(find.byType(ToolDetail), findsOneWidget);
        // ADK 2.x declarations publish parameters_json_schema.
        expect(find.text('query'), findsWidgets);
        expect(find.text('integer | null'), findsOneWidget);
        expect(find.text('Takes no parameters.'), findsNothing);
        await library(t, width);
        await t.tap(find.text('Workflows').first);
        await t.pumpAndSettle();
        await t.tap(find.text('discovery').first);
        await t.pumpAndSettle();
        expect(find.byType(WorkflowGraphView), findsOneWidget);
        expect(find.text('Declared graph · 2 nodes · 2 edges'), findsOneWidget);
        expect(find.text('retry'), findsOneWidget);
        await switchAgent(t, 'Agent B');
        expect(find.text('b-skill'), findsWidgets);
        expect(find.text('a-skill'), findsNothing);
        await switchAgent(t, 'Not published');
        expect(
          find.textContaining('No inspection snapshot published'),
          findsOneWidget,
        );
        expect(find.byType(SectionEditor), findsNothing);
        expect(t.takeException(), isNull);
      },
    );
  }
  for (final width in [390.0, 1440.0]) {
    testWidgets('original Checks panel shows the published report at $width', (
      t,
    ) async {
      t.view.physicalSize = Size(width, 1000);
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.resetPhysicalSize);
      addTearDown(t.view.resetDevicePixelRatio);
      final api = PortalApi(
        client: MockClient((request) async {
          if (request.url.path.endsWith('registry.json')) {
            return http.Response(
              jsonEncode({
                'schemaVersion': 1,
                'uiBuild': portalBuild,
                'agents': [
                  {'id': 'a', 'name': 'Agent A', 'snapshot': 'a.json'},
                ],
              }),
              200,
            );
          }
          return http.Response(
            jsonEncode(
              snapshot(
                'a',
                findings: [
                  finding(
                    'WARN',
                    'SKILL_BODY_LARGE',
                    'a-skill',
                    'Cost',
                    'Long',
                  ),
                  finding('INFO', 'X', 'other-skill', 'Cost', 'Elsewhere'),
                  finding(
                    'ERROR',
                    'CHECK_FAILED',
                    null,
                    'Diagnostics',
                    'Failed',
                  ),
                ],
              ),
            ),
            200,
          );
        }),
      );
      await t.pumpWidget(
        MaterialApp(
          theme: AdklTheme.light(),
          home: PortalShell(api: api),
        ),
      );
      await t.pumpAndSettle();
      // A matching build shows no release-transition notice.
      expect(find.textContaining('A different UI build'), findsNothing);
      if (width < 1400) {
        final bar = t.widget<NavigationBar>(find.byType(NavigationBar));
        expect(
          bar.destinations
              .map((d) => (d as NavigationDestination).label)
              .toList(),
          ['Skills', 'Findings'],
        );
        await t.tap(find.text('Findings').last);
        await t.pumpAndSettle();
      } else {
        expect(find.byType(NavigationBar), findsNothing);
        expect(find.byTooltip('Hide supporting panel'), findsOneWidget);
      }
      expect(find.text('Published Doctor report'), findsOneWidget);
      expect(
        find.text('1 errors · 1 warnings · 1 not run · 0 failed'),
        findsOneWidget,
      );
      expect(find.text('3 of 3 findings'), findsOneWidget);
      // Error-bearing categories lead even when absent from the console order.
      final diagnostics = t.getTopLeft(find.text('DIAGNOSTICS')).dy;
      expect(diagnostics, lessThan(t.getTopLeft(find.text('COST')).dy));
      expect(find.text('Explain & next step'), findsNWidgets(3));
      await t.tap(find.text('Entire current agent'));
      await t.pumpAndSettle();
      await t.tap(find.text('Selected skill: a-skill').last);
      await t.pumpAndSettle();
      expect(find.text('1 of 3 findings'), findsOneWidget);
      expect(find.text('Elsewhere'), findsNothing);
      await t.tap(find.text('Explain & next step'));
      await t.pumpAndSettle();
      expect(find.text('Long next step'), findsOneWidget);
      expect(t.takeException(), isNull);
      api.close();
    });
  }
  testWidgets('non-current export is labelled in Checks, not as edits', (
    t,
  ) async {
    t.view.physicalSize = const Size(1440, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final value = snapshot('a');
    (value['report'] as Map)['freshness'] = 'stale';
    final api = PortalApi(
      client: MockClient(
        (request) async => http.Response(
          jsonEncode(
            request.url.path.endsWith('registry.json')
                ? {
                    'schemaVersion': 1,
                    'agents': [
                      {'id': 'a', 'name': 'Agent A', 'snapshot': 'a.json'},
                    ],
                  }
                : value,
          ),
          200,
        ),
      ),
    );
    await t.pumpWidget(
      MaterialApp(
        theme: AdklTheme.light(),
        home: PortalShell(api: api),
      ),
    );
    await t.pumpAndSettle();
    expect(
      find.text('Report outdated or unverified at export.'),
      findsOneWidget,
    );
    expect(find.textContaining('Edits pending'), findsNothing);
    expect(
      find.text('Published declarations · not live execution'),
      findsOneWidget,
    );
    api.close();
  });
  testWidgets(
    'late agent results and failed selection do not leak prior data',
    (t) async {
      final api = FixtureApi();
      await t.pumpWidget(
        MaterialApp(
          theme: AdklTheme.light(),
          home: PortalShell(api: api),
        ),
      );
      await t.pump();
      await switchAgent(t, 'Agent B');
      api.slow.complete(snapshot('a'));
      await t.pumpAndSettle();
      expect(find.text('b-skill'), findsWidgets);
      expect(find.text('a-skill'), findsNothing);
      await switchAgent(t, 'Invalid snapshot');
      expect(
        find.textContaining('Snapshot unavailable or invalid'),
        findsOneWidget,
      );
      expect(find.text('b-skill'), findsNothing);
    },
  );
  test(
    'static transport is GET only and rejects traversal/mutation paths',
    () async {
      var calls = 0;
      final api = PortalApi(
        client: MockClient((request) async {
          calls++;
          expect(request.method, 'GET');
          expect(request.url.toString(), 'data/registry.json');
          return http.Response(jsonEncode({'schemaVersion': 1}), 200);
        }),
      );
      await api.load('registry.json');
      for (final path in [
        '../x.json',
        'https://host/a.json',
        '/api/chat',
        'a.json?edit=true',
      ]) {
        await expectLater(api.load(path), throwsFormatException);
      }
      expect(calls, 1);
      api.close();
    },
  );
}
