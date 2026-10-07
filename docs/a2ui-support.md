# A2UI primitive rendering

ADKL's Flutter Web console renders the 18 components in the official v0.9 basic
catalog and the v1.0 **candidate** basic catalog. This is component rendering and
local interaction support, not a claim of complete A2UI/A2A protocol conformance.
`Badge` remains a legacy extension, outside the 18-component basic catalog.

## Implementation plan and acceptance

1. Compare the official catalogs and pin the reviewed revision.
2. Preserve surface identity, version, bindings and updates through the Python
   converter bridge; preserve legacy forest-root snapshots.
3. Render every basic component, including local form edits, tabs, modals,
   images and native browser media controls. Resolve repeated-list bindings in
   their item scope and inspect resolved button events.
4. Exercise every type under both version labels, test meaningful interactions
   and invalid payloads, build the release console and inspect it in a browser.

The acceptance boundary is that no basic type falls through to the custom
component placeholder. Media playback also depends on browser codecs, resource
availability and origin policies. A visible control is not evidence that an
agent-side business action has executed.

## Version baseline

Reviewed September 6, 2026 against upstream revision
[`8ff4651232ab0e02b0123730b502711170637a3a`](https://github.com/a2ui-project/a2ui/tree/8ff4651232ab0e02b0123730b502711170637a3a/specification).

- [v0.9 basic catalog](https://github.com/a2ui-project/a2ui/blob/8ff4651232ab0e02b0123730b502711170637a3a/specification/v0_9/catalogs/basic/catalog.json):
  SHA-256 `8cc94d0a482e67048f9fc989964ca5da56fe42f531d919315a508989fb22e13e`.
- [v1.0 candidate basic catalog](https://github.com/a2ui-project/a2ui/blob/8ff4651232ab0e02b0123730b502711170637a3a/specification/v1_0/catalogs/basic/catalog.json):
  SHA-256 `cba4420f576d575ab2a27f55a00d678ce6851297642c091cd06aa27222415b38`.

Both catalogs define the same component names. Version 1.0 adds properties such
as `TextField.placeholder`, `Slider.steps` and `Video.posterUrl`; these render in
the console. Legacy heading variants remain accepted for existing snapshots;
v1.0 authors should use Markdown headings rather than `h1`–`h5` variants.

## Component support matrix

| Components | v0.9 and v1.0 console behavior |
| --- | --- |
| Text | Selectable text, basic Markdown and caption styles |
| Image | Network image, fit/size hints, description and load-error finding |
| Icon | All 59 named catalog icons; SVG path rendered by the browser |
| Video, AudioPlayer | Native browser controls, no autoplay, no preload; video poster |
| Row, Column | Referenced children, alignment and spacing; row flex weights |
| List | Vertical/horizontal children and repeated templates with relative bindings |
| Card | Referenced child in a themed card |
| Tabs | Local tab selection with the selected child rendered |
| Modal | Trigger opens a dialog; content edits update shared surface bindings |
| Divider | Horizontal and vertical orientations |
| Button | Primary/default/borderless styles; resolved event inspection |
| TextField | Short/long/number/obscured input, binding writes, v0.9 regex validation |
| CheckBox | Boolean binding reads and writes |
| ChoicePicker | Single/multiple selection, string-list values, chips/checkboxes and filtering |
| Slider | Numeric binding, range and v1.0 discrete divisions |
| DateTimeInput | Date/time picker and editable ISO value with min/max feedback |

Accessibility labels/descriptions are applied to components. v1.0 hidden and
live-region hints are represented through Flutter semantics; the framework
does not expose separate polite/assertive announcement priorities here.

## SDK and converter integration

```python
from adkl.a2ui_protocol import reduce_operations

surfaces = reduce_operations([
    {"version": "v1.0", "createSurface": {
        "surfaceId": "example",
        "dataModel": {"name": "Ada"},
        "components": [{"id": "root", "component": "Text",
                        "text": {"path": "/name"}}],
    }},
])
# Each surface is directly consumable by A2uiView(a2ui: surface).
```

The reducer handles create/update/delete surface operations, component
replacement by ID, root data replacement and JSON Pointer updates (including
escaped keys and list indices). v0.9 omission of `value` deletes a data key;
v1.0 requires `value` and uses explicit null to delete it. v1.0 inline initial
components/data are preserved. Unsupported versions/operations and invalid
surface lifecycles raise `ValueError`; this is not a full JSON Schema validator.

Existing external converters still load through their existing v09 registry;
their emitted v0.9 or v1.0 operations pass through the reducer in compatibility
mode. This mode allows their historical missing catalog IDs and inferred roots.
Multiple surfaces remain separate even when they reuse component IDs. Existing
built-in presentation packs continue to emit their compatible snapshots.

`A2uiView.onAction` is an optional **host callback**, receiving version, surface
ID, component ID and the resolved event. It is not an A2A wire message and does
not supply transport, authorization, validation or business-action execution.
Without a callback, clicking a button opens an explicit “not sent” inspector.
Input changes stay local and do not mutate the caller's snapshot. A replacement
snapshot resets local edits; the server remains authoritative.

## Review and limits

Open the running console with `?a2ui-gallery=1` to inspect each primitive under
either version. The gallery works without an agent or model credentials. Its
media examples use public Flutter/MDN sample URLs; regular tests do not require
these hosts.

The console uses content-sized cards. It does not reproduce a host's exact
theme or viewport layout: vertical flex growth in an unbounded conversation and
horizontal-list item sizing are console layout policies. Custom catalogs are
not downloaded; unknown components stay named diagnostic placeholders.

Dynamic literal values and JSON Pointer bindings are supported. Catalog
function expressions and function actions produce explicit unsupported
findings; the v1.0 function RPC extension, catalog negotiation, agent transport,
full schema validation and remote action execution remain outside this slice.
Do not advertise complete v1.0 protocol conformance. Future candidate changes
need another catalog/schema review and regression fixtures.

## Native staged payloads

Host tools can stage a payload shaped as `{"type": "a2ui", "operations": [...]}`
through `adkl.widgets.stage` and a `WidgetSpec` whose builder returns those staged
items. The console converts it directly with the shared reducer; it does not
import an application converter registry. The
[native example](../projects/native-agent/README.md) executes both versions with
an installed SDK and no credentials. For common semantic cards, the
[staged-ui example](../projects/staged-ui/README.md) uses the shared pack instead.

Operations must be a nonempty list with supported versions and valid surface
lifecycles, including `createSurface` before updates. Each widget is reduced
independently; include its full surface snapshot in that payload. Multiple
surfaces within one payload are retained separately. This is not a persistent
cross-turn A2UI transport. Invalid payloads produce visible diagnostics without
dropping other valid widgets. Local rendering still has the interaction and
protocol limitations described above.

## Verification

Offline coverage lives in `tests/test_a2ui_protocol.py`, shared presentation and
converter tests, `tests/runtime/test_minimal_examples.py`, and
`console/test/a2ui_primitives_test.dart`.
Flutter VM tests use explicit browser-only media/SVG fallbacks; actual browser
rendering must also be reviewed with a release build.

Run the separate native-browser checks from the repository root with:

```bash
bash scripts/test_a2ui_browser.sh
```

These tests exercise the renderer's native-element configuration, decode and
play a generated WAV, and verify video/SVG attributes. The test-only Chrome
process permits playback without a manual user gesture; the application still
uses no autoplay. Standard `flutter test` excludes these browser-only checks.
Chrome must be installed (or set `CHROME_EXECUTABLE`). Video codec playback and
customer media origins need host/browser integration evidence.

Historical verification for the primitive-renderer implementation: 539 Python tests passed (four
integration skips and six existing warnings); 137 console tests passed; two
native-browser checks passed. Flutter analysis and the release web build passed.
The release gallery was inspected for text, image loading, video controls,
editable fields and modal binding updates. No remote CI pipeline or customer
media/agent integration was run for this change.
