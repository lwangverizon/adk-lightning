# Screen-reader review — 2026-09-12

## Confirmed findings and fixes

The Chrome accessibility tree exposed unnamed, already-filled workflow fields,
repeated actions without target context, and collection/item controls without
selected-state information. Code inspection also found an unnamed icon-only
chat send button.

- Workflow name, activation condition, step, gotcha and prose fields now retain
  semantic labels when their placeholder disappears. Step and gotcha labels
  include their context and position.
- Delete/reorder/unbind controls identify the workflow, step, item or tool they
  affect.
- Skill description and raw instructions have explicit semantic names.
- Chat input and send button have persistent accessible names.
- Navigation collections and selected skill/tool rows expose selected state.

`test/accessibility_test.dart` checks populated field labels, contextual actions,
selected states, keyboard traversal from chat input to Send, and Flutter's
labeled-tap-target guideline for the chat fixture. These are semantic/keyboard
checks, not a substitute for spoken-output testing.

The original checkout passed 181 widget tests. After integration onto main
(5787b1d), all 204 widget tests pass, including upstream adaptive-layout, draft
preservation and keyboard-focus regressions. Flutter analyzer reports no issues.
Existing main labels and the Material typeface are retained; this change adds
context to actions and the remaining unnamed fields.

## Live test status: blocked

Opened the release console in Chrome and inspected its accessibility tree.
System Settings initially showed VoiceOver off; enabling it changed the setting
to on. The app inventory nevertheless continued to report VoiceOver not running,
and no VoiceOver speech/caption output was verified. Opening VoiceOver Utility
subsequently failed because the Mac was locked and the computer-control tool
could not unlock it. The user was asked to unlock the Mac.

VoiceOver's setting was last observed **on**. It could not be restored while the
Mac was locked. On resumption, first check that setting and restore its original
off state after testing.

Pending: spoken names and announcements, VoiceOver rotor/navigation, modal focus
entry/return, error and completed-chat announcements, and live verification of
the updated release build. No VoiceOver, TalkBack or NVDA pass is claimed.
