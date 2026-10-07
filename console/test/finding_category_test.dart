import 'package:flutter_test/flutter_test.dart';

import 'package:adkl_console/api.dart';

/// Findings group by OUTCOME, not by severity.
///
/// With ~20 rules a flat severity list cannot tell "this skill is wired wrong"
/// from "this skill is expensive" from "this skill contradicts the prompt" --
/// three different people fix those, and only the first blocks a save.
///
/// The category is derived from the rule NAME rather than a hand-maintained map,
/// so a new rule lands somewhere automatically. That only works while the naming
/// convention holds, which is what this test pins: every rule the linter can emit
/// is asserted here, so a rule named outside the convention fails rather than
/// silently landing in Correctness.
void main() {
  Finding f(String rule) => Finding('WARN', rule, 'skill', 'message');

  test('every emittable rule lands in its intended category', () {
    const expected = {
      // Cost: token arithmetic. Never blocks a save -- cost is a tradeoff to
      // see, not a defect.
      'SKILL_BODY_LARGE': 'Cost',
      'TURN_COST_HIGH': 'Cost',
      'REFERENCES_DOMINATE': 'Cost',
      'TOOL_DECLARATION_LARGE': 'Cost',
      'TOOL_DOCSTRING_HEAVY': 'Cost',

      // Conflict: two layers disagreeing. The category that cost a real
      // debugging session ("Answer from sources, not memory" plus no tool).
      'PROMPT_PROHIBITION_CONTRADICTED': 'Conflict',
      'WIDGET_PROMISED_NOT_EMITTABLE': 'Conflict',
      'TOOL_ORDER_CONTRADICTED': 'Conflict',
      'TOOL_INSTRUCTED_DIFFERENTLY': 'Conflict',

      // Tools: binding and authorization.
      'TOOL_UNRESOLVED': 'Tools',
      'TOOL_USED_NOT_DECLARED': 'Tools',
      'TOOL_NOT_IN_ANY_SKILL': 'Tools',
      'TOOL_PARAMS_UNDOCUMENTED': 'Tools',
      'SENSITIVE_TOOL_NO_AUTH_GATE': 'Tools',

      // Routing: whether retrieval can find and separate this skill.
      'DESCRIPTION_OVERLAP': 'Routing',
      'DESCRIPTION_MISSING': 'Routing',
      'NAME_MISSING': 'Routing',
      'NAME_DIR_MISMATCH': 'Routing',

      // Presentation: advisory widget-output coverage. A text-only turn may be
      // exactly what the author wanted, so these must not sit with the rules
      // that block a save.
      'WIDGET_OUTPUT_MISSING': 'Presentation',
      'WIDGET_OUTPUT_ANNOTATION_STALE': 'Presentation',

      // Correctness: the file is malformed. These block a save.
      'FRONTMATTER_INVALID': 'Correctness',
      'TOOLS_NOT_A_LIST': 'Correctness',
      'SKILL_MISSING': 'Correctness',
      // An unrecognised `metadata.adkl_presentation` value records no intent and
      // returns 409 on save, so it belongs here rather than with the advisory
      // presentation rules above.
      'PRESENTATION_METADATA_INVALID': 'Correctness',
    };

    expected.forEach((rule, category) {
      expect(f(rule).category, category, reason: rule);
    });
  });

  test('an unrecognised rule falls back to Correctness, not to nothing', () {
    // A new rule must appear SOMEWHERE. Dropping it would hide a finding, which
    // is worse than filing it under the wrong heading.
    expect(f('SOME_FUTURE_RULE').category, 'Correctness');
  });

  test('severity is independent of category', () {
    // Cost findings are never errors and correctness findings usually are, but
    // the grouping must not imply either -- it answers "what kind of problem",
    // not "how bad".
    expect(Finding('INFO', 'TURN_COST_HIGH', 's', 'm').category, 'Cost');
    expect(Finding('ERROR', 'TURN_COST_HIGH', 's', 'm').category, 'Cost');
    expect(Finding('ERROR', 'TURN_COST_HIGH', 's', 'm').isError, isTrue);
  });
}
