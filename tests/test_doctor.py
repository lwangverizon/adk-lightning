"""Doctor evidence, safe handoff, and comparison behavior without host imports."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from adkl import doctor, execution, server


class TestDoctor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / 'skills/teach/SKILL.md'
        self.path.parent.mkdir(parents=True)
        self.path.write_text('---\nname: teach\ndescription: Teach supplied facts\n---\n' + 'Explain. ' * 1800)
        self.doc = {'version': 1, 'agent': {'name': 'tutor', 'factory': 'agent:build'},
                    'authoring': {'skills': {'teach': 'skills/teach/SKILL.md'}}}
        (self.root / 'agent.yaml').write_text(yaml.safe_dump(self.doc))
        (self.root / 'agent.py').write_text('raise RuntimeError("must not import")')
        (self.root / '.env').write_text('SECRET=do-not-export-this')

    def test_static_flags_mapped_content_without_import_or_secrets(self):
        with patch.object(execution, 'Worker', side_effect=AssertionError('no host import')):
            report = doctor.diagnose(self.root)
        self.assertIn('SKILL_BODY_LARGE', {f['rule'] for f in report['findings']})
        self.assertEqual(next(c for c in report['checks'] if c['id'] == 'native')['status'], 'not_checked')
        brief = doctor.render(report)
        self.assertIn('skills/teach/SKILL.md', brief)
        self.assertNotIn('do-not-export-this', json.dumps(report) + brief)
        self.assertNotIn('Explain. Explain.', brief)
        self.assertEqual(report['behavioralEvaluation'], 'not_run')

    def test_rerun_tracks_cost_finding_until_fixed(self):
        before = doctor.diagnose(self.root)
        self.path.write_text(self.path.read_text() + ' Extra explanation.' * 50)
        changed = doctor.diagnose(self.root, previous=before)
        cost = next(f['id'] for f in before['findings'] if f['rule'] == 'SKILL_BODY_LARGE')
        self.assertIn(cost, changed['comparison']['remaining'])
        self.path.write_text('---\nname: teach\ndescription: Teach supplied facts\n---\nExplain briefly.')
        after = doctor.diagnose(self.root, previous=changed)
        self.assertIn(cost, after['comparison']['resolved'])
        self.assertNotEqual(before['sourceDigest'], after['sourceDigest'])

    def test_failed_check_does_not_resolve_prior_findings(self):
        before = doctor.diagnose(self.root)
        self.path.unlink()
        after = doctor.diagnose(self.root, previous=before)
        cost = next(f['id'] for f in before['findings'] if f['rule'] == 'SKILL_BODY_LARGE')
        self.assertIn(cost, after['comparison']['notVerified'])
        self.assertFalse(after['passed'])

    def test_inspection_is_explicit_fresh_and_closed(self):
        worker = Mock()
        worker.request.return_value = {'agent': {'adkl': [], 'children': []}, 'lastRequest': {'instruction': 'private runtime text'}}
        with patch.object(execution, 'Worker', return_value=worker) as factory:
            report = doctor.diagnose(self.root, inspect=True, profile='local')
        factory.assert_called_once_with(self.root, 'local')
        worker.request.assert_called_once_with('inspect')
        worker.close.assert_called_once()
        self.assertNotIn('private runtime text', json.dumps(report))
        self.assertEqual(next(c for c in report['checks'] if c['id'] == 'native')['status'], 'not_checked')

    def test_worker_failure_is_sanitized_and_closed(self):
        worker = Mock()
        worker.request.side_effect = ValueError('secret-model-payload')
        with patch.object(execution, 'Worker', return_value=worker):
            report = doctor.diagnose(self.root, inspect=True)
        worker.close.assert_called_once()
        self.assertNotIn('secret-model-payload', json.dumps(report))
        self.assertEqual(next(c for c in report['checks'] if c['id'] == 'native')['status'], 'error')

    def test_native_evidence_deduplicates_mapped_cost_and_reports_missing_tool(self):
        before = doctor.diagnose(self.root)
        cost = next(f for f in before['findings'] if f['rule'] == 'SKILL_BODY_LARGE')
        worker = Mock()
        worker.request.return_value = {'agent': {'adkl': [{
            'skills': [{'name': 'teach', 'tools': ['missing_tool'], 'description': 'Teach supplied facts',
                        'instructions': 'Explain. ' * 1800}], 'tools': [],
            'audit': {'findings': [{'rule': cost['rule'], 'severity': cost['severity'],
                                   'skill': 'teach', 'message': cost['message']}]}}]}}
        with patch.object(execution, 'Worker', return_value=worker):
            report = doctor.diagnose(self.root, inspect=True)
        costs = [f for f in report['findings'] if f['rule'] == 'SKILL_BODY_LARGE']
        self.assertEqual(len(costs), 1)
        self.assertEqual(costs[0]['id'], cost['id'])
        self.assertEqual(costs[0]['evidence'], 'native_declarations')
        self.assertIn('TOOL_UNRESOLVED', {f['rule'] for f in report['findings']})

    def test_stale_scan_and_invalid_baselines(self):
        with patch.object(execution, 'source_digest', side_effect=['before', 'after']):
            report = doctor.diagnose(self.root)
        self.assertEqual(report['freshness'], 'stale')
        self.assertFalse(report['passed'])
        for previous in ('bad', {}, {'schemaVersion': 1, 'project': str(self.root), 'profile': None, 'findings': [{}]}):
            with self.assertRaises(ValueError):
                doctor.compare(previous, report)

    def test_malformed_input_has_report_and_cli_exit_two_without_excerpt(self):
        (self.root / 'agent.yaml').write_text('agent: [secret-parser-line')
        with patch('builtins.print') as output:
            self.assertEqual(doctor.main([str(self.root), '--format', 'json']), 2)
        self.assertNotIn('secret-parser-line', output.call_args.args[0])

    def test_static_catalog_cannot_read_declared_credential_file(self):
        (self.root / 'agent.yaml').write_text(yaml.safe_dump({
            'agent': {'name': 'example'}, 'tools': {'catalog': 'credentials.json'}}))
        (self.root / 'credentials.json').write_text('{"secret": "private-value"}')
        (self.root / 'execution.yaml').write_text(yaml.safe_dump({
            'profiles': {'local': {'env_file': 'credentials.json'}}}))
        with patch.object(doctor.audit, 'audit', side_effect=AssertionError('must not read catalog')) as audit:
            report = doctor.diagnose(self.root)
        audit.assert_not_called()
        self.assertEqual(next(c for c in report['checks'] if c['id'] == 'source')['status'], 'error')
        self.assertNotIn('private-value', doctor.render(report))

    def _presentation_project(self, meta=''):
        """A declarative project whose catalog records a widget-emitting tool.

        The class fixture is a *factory* descriptor, which takes doctor's
        mapped_content path and has no catalog at all -- so the coverage rule
        cannot run there. This is the shape that arms it.
        """
        root = self.root / 'declarative'
        directory = root / 'skills' / 'answer'
        directory.mkdir(parents=True)
        (directory / 'SKILL.md').write_text(
            f'---\nname: answer\ndescription: Answer a billing question.\n{meta}---\n'
            'Answer in one paragraph.\n')
        (root / 'catalog.json').write_text(json.dumps({'tools': [
            {'name': 'render_fact_card',
             'emits_widget': {'value': True, 'basis': 'configured presentation pack',
                              'inferred': False}}]}))
        (root / 'instruction.md').write_text('Help the customer.')
        (root / 'agent.yaml').write_text(yaml.safe_dump({
            'version': 1,
            'agent': {'name': 'declarative', 'model': 'fixture', 'provider': 'gemini',
                      'instruction': {'template': 'instruction.md'}},
            'skills': {'include': ['answer']}, 'tools': {'catalog': 'catalog.json'},
            'eject': {'target': 'generated.py'}}))
        return root

    def test_widget_coverage_reaches_the_report_and_clears_when_annotated(self):
        root = self._presentation_project()
        before = doctor.diagnose(root)
        finding = next(f for f in before['findings'] if f['rule'] == 'WIDGET_OUTPUT_MISSING')
        self.assertEqual(finding['severity'], 'WARN')
        self.assertEqual(finding['category'], 'Presentation')
        self.assertEqual(finding['check'], 'static')
        self.assertEqual(finding['location']['path'], 'skills/answer/SKILL.md')
        self.assertIn('adkl_presentation', finding['remediation'])
        self.assertIn('> WARN WIDGET_OUTPUT_MISSING', doctor.render(before))
        # Advisory only: an unannotated skill must not raise the static check's
        # severity. (The fixture has no locked environment, so `passed` is False
        # for ENVIRONMENT reasons and cannot show this.)
        self.assertNotIn('ERROR', {f['severity'] for f in before['findings']
                                   if f['check'] == 'static'})

        (root / 'skills/answer/SKILL.md').write_text(
            '---\nname: answer\ndescription: Answer a billing question.\n'
            'metadata:\n  adkl_presentation: text_only\n---\nAnswer in one paragraph.\n')
        after = doctor.diagnose(root, previous=before)
        self.assertIn(finding['id'], after['comparison']['resolved'])

    def test_a_misspelled_annotation_is_an_error_not_a_silent_opt_out(self):
        root = self._presentation_project('metadata:\n  adkl_presentation: text-only\n')
        report = doctor.diagnose(root)
        by_rule = {f['rule']: f for f in report['findings']}
        # An unrecognized value records no intent, so it is an ERROR *and* the
        # coverage rule still fires -- the opposite of a silent opt-out.
        self.assertEqual(by_rule['PRESENTATION_METADATA_INVALID']['severity'], 'ERROR')
        self.assertEqual(by_rule['PRESENTATION_METADATA_INVALID']['category'], 'Presentation')
        self.assertIn('WIDGET_OUTPUT_MISSING', by_rule)

    def test_a_project_without_display_tools_reports_no_coverage_finding(self):
        root = self._presentation_project()
        (root / 'catalog.json').write_text(json.dumps({'tools': [{'name': 'search_orders'}]}))
        report = doctor.diagnose(root)
        self.assertNotIn('WIDGET_OUTPUT_MISSING', {f['rule'] for f in report['findings']})

    def test_wire_report_matches_cli_for_native_missing_tools_and_mappings(self):
        worker = Mock()
        worker.request.return_value = {'agent': {'adkl': [{
            'skills': [{'name': 'unmapped', 'tools': ['missing'], 'description': 'Describe facts',
                        'instructions': 'Explain facts.'}], 'tools': [], 'audit': {'findings': []}}]}}
        handler = object.__new__(server.Handler)
        handler.path = '/api/project/doctor'
        handler.headers = {'Origin': 'http://localhost:8787'}
        handler.command = 'POST'
        handler._body = lambda: {'inspect': True}
        handler.send_response = lambda code: self.assertEqual(code, 200)
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        handler.wfile = io.BytesIO()
        with patch.object(server._workspace, 'root', self.root), patch.object(server._workspace, 'profile', None), \
                patch.object(execution, 'Worker', return_value=worker):
            handler._route_post()
            with patch('builtins.print') as output:
                doctor.main([str(self.root), '--inspect', '--format', 'json'])
        wire = json.loads(handler.wfile.getvalue())['report']
        self.assertEqual(wire, json.loads(output.call_args.args[0]))
        rules = {f['rule']: f for f in wire['findings']}
        self.assertIn('AUTHORING_SOURCE_DRIFT', rules)
        self.assertIn('AUTHORING_SOURCE_UNMAPPED', rules)
        self.assertEqual(rules['TOOL_UNRESOLVED']['title'], 'Tool not found')
        self.assertEqual(rules['TOOL_UNRESOLVED']['subject'], {'kind': 'skill', 'name': 'unmapped'})
        self.assertEqual(wire['context']['source'], 'saved_base')
        self.assertEqual(wire['context']['overlays'], 'not_checked')

    def test_tool_subject_does_not_invent_skill_source_location(self):
        from adkl.diagnostics import describe, enrich_payload
        raw = {'rule': 'TOOL_NOT_IN_ANY_SKILL', 'severity': 'INFO', 'skill': 'teach', 'message': 'No link'}
        finding = doctor._finding(raw, 'native', 'native_declarations', self.root, self.doc)
        self.assertEqual(finding['subject'], {'kind': 'tool', 'name': 'teach'})
        self.assertIsNone(finding['location'])
        self.assertEqual(enrich_payload({'findings': [finding]})['findings'], [finding])
        self.assertEqual(describe(raw)['title'], finding['title'])
        self.assertEqual(describe(raw)['remediation'], finding['remediation'])
        other_report = {'findings': ['runtime observation', {'kind': 'unrelated'}]}
        self.assertEqual(enrich_payload(other_report), other_report)

    def test_shared_native_workspace_includes_doctor_only_checks(self):
        from adkl.native_authoring import Workspace
        workspace = Workspace(self.root, self.doc, {'agent': {'adkl': [{
            'skills': [{'name': 'new', 'tools': ['missing'], 'description': 'A skill',
                        'instructions': 'Explain.'}], 'tools': [], 'audit': {'findings': []}}]}})
        handler = object.__new__(server.Handler)
        handler.path = '/api/findings'
        handler._native_workspace = lambda **kwargs: workspace
        handler._send = lambda code, data: (code, data)
        code, data = handler._route_get()
        self.assertEqual(code, 200)
        self.assertEqual(data['findings'], workspace.diagnostic_findings())
        self.assertEqual({f['rule'] for f in data['findings']},
                         {'AUTHORING_SOURCE_DRIFT', 'AUTHORING_SOURCE_UNMAPPED', 'TOOL_UNRESOLVED'})

    def test_saved_api_parity_covers_static_text_only_and_failed_inspection(self):
        root = self._presentation_project('metadata:\n  adkl_presentation: text_only\n')
        handler = object.__new__(server.Handler)
        handler.path = '/api/project/doctor'
        handler.headers = {'Origin': 'http://localhost:8787'}
        handler._send = lambda code, data: (code, data)
        for inspect in (False, True):
            with self.subTest(inspect=inspect):
                handler._body = lambda: {'inspect': inspect}
                with patch.object(server._workspace, 'root', root), patch.object(server._workspace, 'profile', 'uat'), \
                        patch.object(execution, 'Worker', side_effect=RuntimeError('secret unavailable')):
                    _, response = handler._route_post()
                    expected = doctor.diagnose(root, inspect=inspect, profile='uat')
                self.assertEqual(response['report'], expected)
                self.assertNotIn('WIDGET_OUTPUT_MISSING', {f['rule'] for f in expected['findings']})
                self.assertEqual(next(c['status'] for c in expected['checks'] if c['id'] == 'native'),
                                 'error' if inspect else 'not_checked')
                self.assertNotIn('secret unavailable', json.dumps(expected))

    def test_selected_brief_and_api_share_report(self):
        handler = object.__new__(server.Handler)
        handler.path = '/api/project/doctor'
        handler.headers = {'Origin': 'http://localhost:8787'}
        handler._body = lambda: {'selected': []}
        handler._send = lambda code, data: (code, data)
        with patch.object(server._workspace, 'root', self.root), patch.object(server._workspace, 'profile', None):
            status, response = handler._route_post()
        self.assertEqual(status, 200)
        self.assertEqual(response['report'], doctor.diagnose(self.root))
        self.assertNotIn('> WARN SKILL_BODY_LARGE', response['brief'])
        handler.headers['Origin'] = 'https://example.com'
        self.assertEqual(handler._route_post()[0], 403)
