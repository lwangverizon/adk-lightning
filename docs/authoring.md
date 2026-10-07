# ADKL Console: author skills and inspect locally

ADKL Console is the free local UI for skill authoring, factory inspection,
disposable test conversations, [Doctor](doctor.md) and host-provided
[change verification](verification.md). It does not provide live production
attachment, durable hosted traces or fleet management. Installing the SDK alone
starts no UI; use the explicit setup below.

The console edits project files and shares validation with the CLI. For an SDK
example without a browser or credentials, start with the [quickstart](quickstart.md).
For a new project or an installed SDK with the separate console ZIP, use
[project onboarding](project-onboarding.md). The empty workspace now supports
Create agent, runtime setup, local credentials, inspection and bundle creation.
Use [agent design](agent-design.md) to review instruction ownership, skill scope
and avoidable model turns before testing the authored behavior.

## Run the demo

Two terminals. Flutter 3.44.0 (the CI version) and uv for Python.
See [project execution environments](environments.md) for profile isolation,
credential migration and the customer-hosted execution boundary.

The split is deliberate. The authoring half -- catalog, lint, schema, skill_doc
-- is stdlib + PyYAML, so CI runs it without the ADK wheel or any credentials.
The `runtime` extra adds Google ADK and LiteLLM directly; `adkl/gateway.py`
routes configured project providers through LiteLLM. Injected SDK models and
host-factory models retain their own provider path. It avoids ADK's broad `extensions` extra
and its unrelated integrations.

```bash
# 1. API + a project (stdlib http.server)
# Start in the repository root.
uv sync --locked
uv run --no-sync python scripts/build_sdk.py
uv run --no-sync python -m adkl.projects prepare projects/native-agent --wheel dist/adk_lightning-0.1.0-py3-none-any.whl
uv run --no-sync python -m adkl.server --root projects/native-agent

# 2. console (dev server, hot reload)
cd console && flutter run -d chrome
```

Or serve a built bundle from the same origin, which avoids CORS entirely:

```bash
cd console && flutter pub get --enforce-lockfile && flutter build web --release
cd .. && uv run --no-sync python -m adkl.server --root projects/native-agent --web console/build/web
# open http://127.0.0.1:8787/
#
# The server scans the enclosing project for every agent.yaml, so the console's
# header dropdown switches between all of them without a restart.
```

## Desktop, tablet and phone layouts

The editor keeps its usable width as the window changes. Wide windows show the
skill/tool library and one supporting panel, with **Checks** and **Chat** tabs.
Drag the supporting panel divider to resize it, or hide it from the toolbar.
On smaller windows use the bottom **Editor**, **Findings**, **Test** and
**Project** navigation. The menu button opens the library when it cannot fit
beside the editor. Drafts and the test conversation survive layout changes.
The workspace actions menu contains create, reload, configuration (where
supported) and environment selection. Layout decisions also account for enlarged
text. These are responsive web layouts; native iOS/Android packages are not
provided by the console build.

Native tool details report factory provenance and declared scope. Both ADK
parameter declaration formats are supported; an unavailable schema is labeled
separately from a known empty parameter list. These are declarations, not proof
of tool execution or authorization.

## Skill browsing and freshness

Selecting a skill highlights it and shows loading feedback immediately. Late
responses cannot replace a newer skill/tool selection; a failed load restores
the previous editor and reports the error.

Native skill, catalog and finding reads reuse a worker-local inspection snapshot.
Each cached read checks tracked file/directory metadata, including inode, size,
mtime and ctime. Directory changes detect additions, deletions and renames. A
changed inventory requires a fresh byte-validated inspection; changed source
still reports a stale conversation rather than executing an old factory.
This metadata hint is for local browsing, not an execution/security identity.
Chat, streaming, edits, dry-run lint and explicit **Inspect runtime** retain full
source hashing. Worker operations, close/reset and project/profile switches
invalidate the snapshot. Initial inspection still pays factory startup costs.

## Request limits

The local API accepts JSON object bodies up to 1 MiB. Body reads have a ten-second
overall deadline and happen before the workspace lock, so an incomplete upload
cannot block other console requests. Invalid JSON/framing returns 400, oversized
bodies 413, and read timeouts 408. Workspace/runtime operations remain serialized;
these bounds do not make the server suitable for multi-tenant hosting.

## The demo path

The native example executes without credentials. Inspect it in **Project**, then
send `Show v0.9`, `Show v1.0` and `thanks` in **Test** to see staged cards and
follow-up isolation. Its Python constructor remains host-owned. For skill-file
editing, use the [staged-ui example](../projects/staged-ui/README.md) or a project
you create in the console. The same prepare/serve commands accept that root;
interactive staged-ui chat additionally requires a Google API key.

1. **Read the activation boundary.** Check when the skill applies, what it should
   accomplish and which tools it needs.
2. **Open the tool picker.** Compare tool declarations with the procedure. The
   linter identifies unresolved names and can suggest tools referenced in prose;
   it does not prove that an instruction is correct or a tool call is authorized.
3. **Edit and inspect findings.** The live buffer uses the same server-side lint
   rules as CLI checks. Keep prompts, tool descriptions and skill procedures
   consistent, then verify behavior with representative agent cases.
   Skills and Tools show up to two plain-language diagnostic badges per row,
   with `+N` opening the remaining rule types. Click a badge for the original
   finding, suggested next step and expandable technical rule code. **Needs
   attention** filters the current collection to items with findings, including
   informational advisories; search still applies. Unsaved edits label results
   as potentially outdated. A neutral icon or no findings is not a health check.
   **No widget declared** describes static wiring, not failed delivery;
   intentional text-only skills can use `metadata.adkl_presentation: text_only`.
   **Not linked to a skill** does not mean a tool is unused: native hosts may
   bind or call it directly. Runtime widget evidence stays in the turn panel.
   Choose **Run Doctor** to populate Findings and navigation badges from the
   CLI-equivalent saved report. **Entire current agent** and **Selected skill/tool**
   filter one report; they do not change scan scope. Fast draft validation stays
   in the editor, not a competing Findings mode. Coverage identifies skipped
   checks, profile, source digest and freshness at scan time; saved scans exclude
   unsaved buffers and environment overlays. See
   [Console and CLI parity](doctor.md#console-and-cli-parity).
4. **Watch the token badge.** Attaching a tool is a free gesture in a UI and a
   recurring per-turn charge; the badge turns red past 3,000 est. input
   tokens/turn.
5. **Save.** The file on disk changes by exactly the lines you added. A console
   write is indistinguishable from a hand edit -- and if it is not, the
   byte-identical round-trip tests in `tests/test_skill_doc.py` fail.
6. **Switch agents** from the header dropdown and inspect Configuration →
   Features. The test panel names unsupported features instead of executing a
   different agent from the one configured.


## Design rules, and where they are enforced

- **Text on disk is canonical.** Every read re-parses the tree; every write goes
  to the file and is re-linted. There is no server-side skill model to drift.
- **The console cannot express what the validator rejects.** Save returns 409 on
  lint errors. Override is explicit and visible.
- **One rule implementation.** `skill_lint.check_one()` serves the CLI, CI, and
  the editor's live buffer, so they cannot disagree.
- **Diffs stay reviewable.** Tool order is preserved and only new entries are
  appended; an unchanged description is written back byte-identical rather than
  re-wrapped. Both were found by round-tripping a real skill (see below).
- **Inference is labeled.** Side-effect class shows a trailing `?` and a tooltip
  saying it was inferred from the tool name.
- **One wire path per capability.** `agent.provider` picks the backend
  (`gateway` for a host-configured LiteLLM gateway, `gemini` for Google's API with a
  `GOOGLE_API_KEY`), but both route through LiteLLM. A second client library
  would mean a second place to forward thinking config, and that is the bug
  `_ThinkingAwareLiteLlm` exists to fix. The backend is stamped into the vector
  file, so an index built for one provider cannot be silently scored by another.


## Two bugs this process caught in itself

Recorded because they are the failure modes to guard against.

1. **Description collapse.** YAML folds a `>` block into one logical string, so
   a naive write-back turned a 10-line description into a single 900-char line.
   Every future edit would then show as one whole-line diff, making review
   useless. Fixed by re-wrapping at 76 cols *and* preserving the original block
   when the text is unchanged.
2. **Tool re-sorting.** Alphabetizing rewrote all 11 lines of a tool list to add
   one. Now on-disk order is preserved and new tools are appended.

Neither is cosmetic — both would have quietly destroyed the reviewability that
justifies keeping text canonical.

## Not built yet

- **Complete runtime coverage** across preview, evaluation, deployment and export.
  Preview executes history slicing, tool-result trimming, output cleanup and PII
  redaction; semantic cache, trusted identity enforcement and analytics remain
  blocked until their execution boundaries exist.
- **Complete generated-code export parity.** SDK-backed project bundles already
  support native ADK serving; see [project onboarding](project-onboarding.md).
- **Integrated embedding lifecycle**: `adkl.embed` and stale checks exist, but
  background rebuild, readiness and atomic publication do not.
- **Auth, RBAC, durable traces, tenant isolation and controlled releases.**

See [the competitive product plan](../COMPETITIVE_PRODUCT_PLAN.md) for priorities
and acceptance gates, and [dated reviews](reports/) for point-in-time assessments.
Neither is a current capability inventory; use [the overview](../README.md#current-boundaries)
and [capability inventory](../adkl/capabilities.py) for that.
