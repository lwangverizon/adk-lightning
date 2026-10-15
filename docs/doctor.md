# ADKL Doctor and coding-agent handoff

Native skill audits preserve matching-exclusion and presentation metadata.
Skills explicitly excluded from matching remain in the declared inventory but
are not treated as competing candidates by description-overlap lint. Negative
capability mentions are not instructions to add tools. These text rules remain
heuristics, not behavioral or authorization checks.

Token findings are static estimates. Where native tools have no token-cost
estimate, turn cost is a lower bound with unknown tool costs, not a zero-cost
toolset or measured billing. Caching and dynamic request assembly require host
measurements.

Prompt-prohibition lint requires an ordered action phrase, not scattered word
overlap. Articles and Markdown emphasis are ignored; unrelated empty fields,
quoted examples and repeated prohibitions are not contradictions. This narrow
heuristic can miss paraphrases. Cross-skill tool-polarity findings request scope
review; they do not establish coactivation or a runtime conflict.

`SENSITIVE_TOOL_NO_AUTH_GATE` is a name-based candidate plus a missing auth signal
in skill text, not a tool/backend authorization audit. Signals use word boundaries
(`author` is not `auth`). Neither adding auth prose nor silencing this heuristic
implements an authorization gate. Inspect host routing, deterministic guards and
backend enforcement. Markdown briefs include subjects even without source paths.


Doctor assembles deterministic checks into a versioned report with stable rule
codes, finding IDs, evidence, source identity and remediation guidance. The CLI
and console use the same report. It does not fix files or use an LLM reviewer.

Doctor is the diagnostic side of ADKL, not the Runtime's execution engine. It can
be used before adopting shared callbacks or full runtime bindings. Static checks
inspect saved project files; native coverage depends on inspectable declarations
and is reported explicitly. Use [RuntimeBindings](runtime-bindings.md) for shared
execution, and a [host evaluator](verification.md) for behavioral evidence.
The [Console](authoring.md) exposes the same Doctor workflow locally; no hosted
inspection service or live production-session attachment is implied.

## Run from the host's locked environment

```bash
python -m adkl.doctor /path/to/project
python -m adkl.doctor /path/to/project --format json > /tmp/doctor-before.json
python -m adkl.doctor /path/to/project --inspect --format markdown
python -m adkl.doctor /path/to/project --format json --baseline /tmp/doctor-before.json
python -m adkl.doctor /path/to/project --base-ref origin/main   # MR: partial variant edits
```

`--inspect` explicitly constructs trusted factory code in a fresh local project
worker and closes it afterwards. It uses the project's execution profile and
credentials; factory construction can have side effects. It does not send a chat
turn, execute domain tools intentionally, or replace the console's conversation.
Use `--profile NAME` to select an execution profile. Static scans neither import
the host nor read credential values. `python -m adkl.projects doctor /path/to/project`
is the smaller environment-readiness command; `python -m adkl.doctor` is the
broader diagnostic report and fix-brief workflow.

Exit codes follow common linter conventions: **0** means no ERROR findings or
failed checks, **1** means ERROR findings, and **2** means a failed/incomplete scan,
stale source during the scan, or invalid CLI/baseline input. Warnings are advisory.
A zero exit does not mean all possible areas were inspected: always read coverage.
For an explicit retained-evidence gate, repeat `--require-contract NAME` for each
required contract (see below). This changes neither inspection nor execution.
Text and Markdown currently use the same human-readable fix brief; JSON is the
machine-readable interface (`schemaVersion: 1`). Keep report output outside the
project source tree, such as `/tmp`, to avoid making the report its own input.

## Coverage and evidence

| Check | Evidence and limits |
| --- | --- |
| Instruction layers | Optional `doctor-instructions.json`: attributed saved final-request fixtures; checks owner bypass, inactive/duplicated/misplaced instructions, unknown additions, ordering and budgets. No runtime recapture or semantic certification; see [layering audit and strict CI gate](instruction-layering.md). |
| Environment | Descriptor, profile, interpreter and dependency-file prerequisites, plus installed distributions in the profile interpreter versus `uv.lock` (metadata only); no dependency compatibility or credential test |
| Static | Existing project audit, configured prompts, skill files and catalog declarations; factory descriptors remain descriptor-only |
| Mapped content | Factory `authoring.skills` metadata and instruction-cost rules without importing the host; no claim that mappings match runtime |
| Skill variants | Same-named `SKILL.md` copies under the descriptor's skill roots, compared by declared tools, plus ADK's description limit on copies no content check covers; with `--base-ref`, also by section bodies against that Git revision. No runtime-selection comparison |
| Skill references | Explicit redirects such as `(use other-skill)` checked against skill names in the scanned roots; plain prose mentions are not checked |
| Artifacts | `uv.lock` path wheels: presence, lock hash, root `Dockerfile*` `ARG` wheel paths and the running Doctor's identity versus the locked ADKL wheel. No image build or registry check |
| Host code | Opt-in `doctor-host.json`: AST patterns in listed host Python paths, never imported |
| Native | Opt-in ADKL binding declaration audit, source-mapping reconciliation and construction logs, after any declared `agent.startup` serving modules; no dynamic instruction evaluation or state-dependent toolset enumeration |
| Retained verification | Reads configured host evidence, validates complete observations and current input/profile identity; never runs the evaluator |
| Behavior | Not executed by Doctor; host evaluations own outcomes, authorization and delivery |

`AUTHORING_SOURCE_UNMAPPED` (INFO) reports native skills without an editable source
mapping. Inspection remains available; register the actual source to enable editing.
This distinguishes incomplete mapping coverage from drift in an existing mapping.

## Variant drift and construction fallbacks

Hosts often serve one skill from several files, such as environment, channel or
surface copies. `SKILL_VARIANT_TOOLS_DIFFER` (INFO) groups same-named copies under
the top-level directories of `authoring.skills` (or `skills.dir`) and names each
tool declared by only some copies. A differently named copy joins a family with
`metadata.adkl_variant_of: <name>`. Hidden and symlinked directories are skipped.
Differences may be intentional. Findings are fingerprinted by content: with
`--baseline`, a fix applied to one copy appears as a new finding until every
served copy matches, rather than remaining hidden in an existing finding.

With `--inspect`, records logged at WARNING or above while the factory is
imported and constructed become `FACTORY_CONSTRUCTION_ERROR` (WARN) or
`FACTORY_CONSTRUCTION_WARNING` (INFO). A host that catches a construction error,
logs it and serves a fallback lane otherwise yields a valid-looking inventory.
Doctor keeps the logger, level, exception type and only parameterized message
templates or a leading event key. Formatted arguments, exception text and
tracebacks are never retained. Offline fixtures can log expected infrastructure
warnings; Doctor cannot tell whether a fallback is intended. Assert the expected
runtime lane in host startup tests.

### Serving import order

`--inspect` imports the factory module directly. A host's serving entry point can
import the same code in another order and fail where the factory alone succeeds.
An eager package re-export can close an import cycle only under `import api`,
crashing startup or making a caught capability-runtime failure serve a native
fallback. Declare the modules the server imports first:

```yaml
version: 1
agent:
  name: example_agent
  factory: example_agent.adkl_inspect:build
  startup: [api]          # imported in order before the factory, same worker
```

- `STARTUP_IMPORT_FAILED` (ERROR) names the module and exception class when a
  startup import raises. The factory is not built afterwards, because modules a
  cycle left partially initialized would make any inventory unrepresentative;
  the native check reports findings with no inventory, so earlier native findings
  become `notVerified`, not resolved.
- Records logged during startup imports become the existing
  `FACTORY_CONSTRUCTION_*` findings, worded "while declared startup modules were
  imported". This is the caught-fallback case.
- Without `agent.startup`, the native check detail says the factory was imported
  directly. Console chat and portal exports use the same order once declared.

Startup modules are trusted host code with the same side-effect caveats as the
factory. Supply synthetic credentials and deny network access as the host's own
startup tests do. Use one profile per flag set that changes startup: an import
cycle can depend on a rollout flag being on. Doctor does not run the server's
lifespan or readiness hooks.

Factories without inspectable ADKL binding declarations report native checks as
`not_checked`. Their absence is not an empty successful inventory. Construction
failures produce a failed check, not a guessed missing-tool diagnosis. Static
catalog checks likewise establish declarations rather than executed availability.

Check statuses are `passed`, `findings`, `not_checked`, or `error`. Whole-report
freshness is `current`, `stale`, or `unknown`; it compares source digests before and
after scanning. `current` applies at scan completion, not indefinitely. The source
digest follows [execution source tracking](environments.md), including its limits.
Doctor checks saved base files, not unsaved editor buffers or environment overlays.

Findings retain existing lint rule codes, such as `SKILL_BODY_LARGE` and
`TOOL_UNRESOLVED`. Each includes severity, category, check, evidence, skill,
project-relative location when available, impact, suggested remediation and
verification. Locations with no reliable line have `line: null`; the doctor does
not invent line numbers. Cost rules are estimates and do not establish provider
billing. Other heuristic findings require domain review.

Large instructions are a maintenance signal, not an instruction to split a
stateful workflow. Separate optional explanations from required routing, consent,
state transitions and recovery. Establish ownership before splitting skills; test
continuation and rejected-call recovery after compression. Moving detail into
references can add calls or remove necessary context. Source size and tool
registration do not establish actual exposure, provider caching or latency gains.

## Incident-derived host checks

These rules come from merged host regressions. Each is deterministic, cites its
evidence and stays narrow; none executes host code.

- `SKILL_VARIANT_PARTIAL_EDIT` (WARN, requires `--base-ref REV`): a Markdown
  section that was identical (whitespace-normalized) in several copies of a skill
  at `REV` changed in some copies but not others. Pass the MR target, for example
  `--base-ref origin/test`. Copies added after `REV` and sections that already
  differed are not reported. Fenced headings are ignored, and repeated headings
  stay separate. The finding is anchored on an unchanged copy and disappears once
  every copy matches. Intentional environment differences still need review.
  An invalid ref fails the `skill_variants` check (exit 2) rather than skipping it.
- `SKILL_REFERENCE_UNKNOWN` (WARN): a skill description or body redirects with
  `(use|see|route to|handled by|owned by|belongs to NAME)`, or with
  ``use/load/route to/hand off to/defer to/switch to `NAME` ``, and no scanned skill
  has that name. The model is told to load a skill that the surface cannot load.
  Only hyphenated lowercase names are matched, so `(see Gotchas)` and tool names
  are ignored. A skill served only by another agent outside the descriptor's
  skill roots is reported, because this agent cannot load it either.
- `ARTIFACT_WHEEL_MISSING` / `ARTIFACT_WHEEL_HASH_MISMATCH` (ERROR): a `uv.lock`
  path wheel is absent or its bytes differ from the lock hash.
- `ARTIFACT_DOCKER_WHEEL_MISMATCH` (ERROR): a root `Dockerfile*` `ARG NAME=…whl`
  default has a locked wheel's filename but a different path. A stale wheel that
  still exists copies successfully, then the frozen sync fails in the image build.
- `DOCTOR_SDK_DIFFERS_FROM_LOCK` (INFO): Doctor's checker modules differ from the
  `adk-lightning` wheel the project locks. This is expected for a diagnostic
  overlay, but the report then describes that SDK rather than the served one.
- `DESCRIPTION_TOO_LONG` (ERROR on a served copy): ADK's `Frontmatter` rejects
  a description over 1024 characters, so `load_skill_from_dir` raises and a
  host that loads skills at import fails to start. Length is measured on the
  parsed YAML value, as ADK does. The `skill_variants` scan also reports
  unmapped copies (WARN), such as an MVA twin, before another surface loads it.
- `REFERENCE_READ_BEFORE_ACTION` (INFO): the body orders a reference read before
  acting: "read `references/x.md`, then call `tool`", "load … before `tool`",
  "before `tool`, read …", or "read … and follow it". Each read is a sequential
  `load_skill_resource` model step on that branch; inlining such procedures cut
  one model call per turn in the host's measurements. Plain on-demand mentions
  ("see references/x.md if asked") and fenced examples are not matched. Keep
  rarely needed detail in references; measure calls per branch before and after.
- `ENVIRONMENT_NOT_LOCKED` (WARN, environment check): the profile interpreter has
  distributions outside `uv.lock`, versions the lock does not list, or a
  path-locked wheel installed from another file (per `direct_url.json`). A host
  inspection export once rendered 181 of 182 tool schemas differently from the
  image for this reason. Locked-but-absent packages and local project sources
  are not reported. One finding per project; `uv sync --locked` resolves it.
- `HISTORY_ROLE_USER_SCAN` (WARN, opt-in): a function loops over ADK history
  (`…events` or `…contents`), tests `role == "user"`, and exits the loop without
  first requiring user authorship (`author`) or non-empty text. It also never
  mentions function responses. ADK records tool results as `role="user"`, so the
  scan can treat a tool result as the user's latest turn, or append media to it.
  Negated text checks (`if not text: return`) are not guards. AG-UI `messages`
  are skipped because they carry tool results under a separate role. Mark a
  reviewed line with `# doctor: ignore[HISTORY_ROLE_USER_SCAN]`.

Enable host-code patterns with `doctor-host.json` at the project root:

```json
{"version": 1, "python": {"include": ["tools", "sub_agents"], "exclude": ["tests"]}}
```

Paths are project-relative. Hidden, excluded and symlinked paths are skipped. Files
that are too large or unparsable are counted in the check detail, not guessed.
Host-code findings carry a source line. The line is display-only and not part of
the finding ID, so an unrelated edit that shifts lines does not create a new
finding in baseline comparison.

## Capability LLM-call budgets

Doctor prominently reports per-capability inference counts through optional
`doctor-calls.json`. Default warning: **more than 3 inference attempts per customer
invocation**, including nested model work and retries. Missing, partial, stale,
scripted and measured evidence are clearly distinguished; no model is executed
and no runtime cutoff is installed. This differs from `TURN_COST_HIGH`, which is
a token-context estimate. See the [schema and rollout guide](capability-call-budgets.md)
for coverage, freshness, baseline comparison and measurement limitations.
The detailed coverage table is currently CLI/JSON only, not a new portal widget.

## Boundary contracts and request measurements

See [boundary evidence](doctor-boundaries.md) for explicit tool-output consumers,
retained consent/outcome/handoff probes, and assembled-request byte/proxy-token
reports. These use the same Doctor JSON and exported brief; no host execution or
runtime enforcement is added to Doctor. A dedicated Console cost panel is not
included. Missing configuration remains `not_checked`, not affirmative coverage.

## Retained lifecycle-contract evidence

Doctor automatically reads local [verification](verification.md) evidence when
`verification.yaml` exists. This is a read-only check, **not** an additional
execution mode: neither static scanning nor `--inspect` runs an evaluator.
`behavioralEvaluation` remains `not_run`. The additive `verificationEvidence`
field records the retained host state, profile, input/suite/evaluator digests,
run ID and contract coverage; `verification_evidence` appears in Check coverage.
The CLI, Console API and exported brief use the same findings.

Optional `verification.yaml` `contracts` declares which lifecycle checks the host
expects. Suite cases opt into coverage with `adkl_contracts` (see the runnable
native example and [probe contracts](verification.md#lifecycle-contract-probes)).
Supported names are `no_replay`, `single_emitter`, `state_isolation`,
`rejected_call_recovery`, `consent_binding`, `outcome_consistency`,
`handoff_continuity`, `result_integrity`, `capability_reachability` and
`delivery_consistency`. These are host declarations, not inferred test coverage.
Cases without tags still contribute to ordinary verification. Existing configs
and reports require no migration; a suite/config edit requires new evidence.

- `VERIFICATION_FAILED` (ERROR): the current retained attempt failed or regressed.
- `VERIFICATION_NOT_CURRENT` (WARN): no current passing candidate: not run,
  baseline only, stale, running, or a different execution profile.
- `CONTRACT_PROBE_FAILED` (ERROR): a tagged case has a failed observation. Every
  repetition must pass even if the host's aggregate pass-rate policy is looser.
- `CONTRACT_EVIDENCE_MISSING` (WARN): a declared contract has no tagged cases or
  lacks current passing candidate evidence.
- Invalid/missing records, incomplete observations, contradictory stored success,
  symlinked storage and changes during evidence inspection fail the check (exit 2).
  Host exception messages and case inputs are not exported.

No verification config means `not_checked`, not a clean behavioral bill of health.
The brief lists supported contracts not configured without inventing failures for
irrelevant workflows. Warning-only missing coverage retains normal exit semantics;
use `adkl.verification candidate` as the explicit host execution gate.

A passing candidate is checked against the current declared source, lock,
evaluator, suite, configuration and profile. A new failed attempt supersedes an
old pass. A baseline is not a passing candidate. Removing configuration, recording
a new baseline, changing coverage or leaving stale evidence cannot resolve old
contract failures. A current rerun with identical coverage can resolve them.
Verification evidence is checked for races separately because `.adkl/` is excluded
from Doctor's source digest. Freshness applies at inspection time, not forever.

Host graders and instrumentation remain trusted: a tag and a passing boolean do
not prove that a case exercised the claimed boundary. These unsigned local records
cannot certify backend authorization, distributed exactly-once effects, frontend
acceptance or live-model quality. Include all relevant host inputs and use one
explicit profile for each surface/variant. No cross-profile parity is inferred.

### Required-evidence gate

Hosts can opt into a failing gate without changing default advisory behavior:

```bash
uv run --no-sync python -m adkl.doctor /path/to/project --profile local \
  --require-contract result_integrity \
  --require-contract capability_reachability \
  --require-contract delivery_consistency
```

The Python equivalent is `diagnose(root, require_contracts=[...])`. Supported
names are validated before scanning; repeated CLI names are deduplicated. Each
requested contract needs tagged cases and a current passing candidate for the
selected profile. An absent `verification.yaml`, missing cases, baseline-only,
stale, running or profile-mismatched evidence yields
`CONTRACT_EVIDENCE_REQUIRED` (ERROR, exit 1). Failed observations remain
`CONTRACT_PROBE_FAILED`; a malformed/incomplete scan still takes precedence with
exit 2. No evaluator or business tool is executed by this gate.

Required names appear in `context.requiredContracts` and in the exported rerun
command. Changing/removing requirements changes comparison coverage: it cannot
resolve prior findings. Default scans without requirements preserve their previous
exit behavior. Use the same explicit requirements in your host CI command; there
is no new Console control or automatic repository merge policy.

See [incident-derived probes](doctor-boundaries.md#incident-derived-probes) for
broken/fixed examples, boundary instrumentation and remaining coverage limits.

## Prompt ownership and consumer compatibility

Doctor and editor cross-layer audits share these advisory checks over the
**supplied prompt and inventory**, including inspectable native declarations:

- `PROMPT_POLICY_REPEATED` (INFO): an exact normalized sentence of at least
  12 words occurs twice in the prompt.
- `PROMPT_SKILL_POLICY_DUPLICATED` (INFO): such a sentence appears in both the
  prompt and a skill. Review ownership, not automatic deletion.
- `PROMPT_SURFACE_CONFLICT` (WARN): literal standalone directives both require
  and forbid Markdown, tables or bullet points. Conditional and paraphrased
  conflicts are intentionally not inferred.
- `PROMPT_SKILL_LOADER_NOT_IN_INVENTORY` (WARN): a literal delegation such as
  “the safety-and-refusal skill carries …” has neither supplied skills nor a
  recorded `load_skill` tool. Unknown inventory suppresses this check too.
- `PROMPT_TOOL_NOT_IN_INVENTORY` (WARN): an imperative call/use/invoke directive
  names a backticked underscore-style tool absent from the supplied inventory.
  Missing inventory is unknown, not empty. Skill loaders are treated as implicit
  when skills are supplied. Dynamic tools and host-added capabilities still need
  manual verification; never broaden tool access just to clear a warning.

Checks ignore fenced examples and do not evaluate callable instructions or
per-request callback assembly. They do not classify product-specific content,
prove instruction equivalence, or measure token savings. No finding certifies
runtime capability, authorization or correct model behavior.

The reusable design lesson is **share universal policy, compose by capability**:
keep privacy, authorization and trust boundaries independent of skill selection;
put domain workflows and mutable product facts in their owning skills/references;
share execution rules once; attach visual or voice policy only to the consumers
that implement it. Before moving a rule, enumerate every prompt consumer and its
actual tools/callbacks. A voice consumer cannot rely on a refusal skill it cannot
load. Retain required business restrictions even when they look domain-specific.
Measure the assembled request (including skills, schemas and state), not just the
source prompt; validate positive, neighboring, refusal and recovery turns in the
host. Static heuristics are review aids, not automatic prompt optimizers.

## Widget output coverage

`WIDGET_OUTPUT_MISSING` (WARN) reports a skill that binds no widget-emitting
tool, so its turns are text only. It is the inverse of
`WIDGET_PROMISED_NOT_EMITTABLE`, which reports a skill claiming UI none of its
tools can emit. The rule speaks only from recorded evidence and stays quiet
otherwise:

- It runs only when the checked catalog *declares* a widget-emitting tool, which
  a configured [presentation pack](shared-features.md) or result binding does.
  Emission inferred from module source does not arm it: that scan is per module,
  so one file mentioning a render call would put a warning on every text-only
  skill. An agent with no display tools reports nothing.
- A declared tool with no recorded `emits_widget` evidence — what a
  native/host-factory inventory produces — is unknown, not absent, and silences
  the rule for that skill.
- Factory descriptors have no catalog, so they take the mapped-content path and
  this rule does not run on them.

Record an intentionally text-only skill with `metadata: adkl_presentation:
text_only` in its `SKILL.md` frontmatter. The annotation is a declaration of
intent, so a stale one — set on a skill that does bind a display tool — reports
`WIDGET_OUTPUT_ANNOTATION_STALE` (INFO) rather than being honored silently, and
any other value reports `PRESENTATION_METADATA_INVALID` (ERROR) rather than
reading as an opt-out. Being an ERROR, it also refuses a console skill save with
409 until corrected or forced. A bound display tool is a declaration; it is not
evidence that a widget reached a customer in a turn.

Findings identify the rule, location, skill and diagnostic subject. Single-subject
rules (cost estimates, repeated policy, unlinked/large tool declarations, unmapped
sources and widget coverage) exclude message text from their IDs. Counts and
wording can change without inventing a new issue. Multi-issue rules still include
the message until their producers supply semantic subkeys; different missing tools
or conflicts must not collapse into one finding. IDs for newly stabilized rules
may differ from legacy reports. Identical static/native findings share an ID;
native evidence takes precedence.

Baseline comparison requires the same project path, profile and schema version.
It reports `new`, `remaining`, `resolved`, and `notVerified` IDs. New additive v1
fields are `comparisonIdentity` (coverage version and installed checker-source
fingerprint), per-check `scopeDigest`, and top-level `comparisonNotes`. The four
comparison lists remain unchanged for existing console clients.

A disappeared finding is resolved only when both reports are current, checker
identity and scan context agree, and its check completed in both reports with
identical coverage. Coverage fingerprints track the descriptor, profile
configuration and tracked source inventory; native coverage also tracks the
agent tree, binding ownership, features and skill/tool names. Bodies and audit
messages are not coverage: correcting instructions with the same inventory can
resolve a finding. Descriptor/profile edits and added/removed files conservatively
require a new baseline. This may retain unverified findings even for legitimate
configuration fixes; it does not mean those fixes failed.

Legacy reports remain readable, but lack affirmative resolution evidence: absent
findings become `notVerified`. Changed checker code likewise requires a fresh
baseline; matching package versions alone are insufficient for pre-release wheels.
Missing, skipped, failed or narrower checks cannot silently erase earlier issues.
Fix briefs explain comparison exclusions. `remaining` only means a finding ID is
still present, not that both scans have equivalent coverage. Fingerprints do not
attest installed dependencies, dynamic state, or deployed behavior. Comparisons
across moved checkout paths and owner-qualified multi-agent diagnostics remain
future work; current editor naming restrictions are unchanged.

## Offline multi-agent publication

The [static portal](static-portal.md) publishes the same report alongside an agent
inventory. `doctor.diagnose(..., include_snapshot=True)` optionally includes
`inspectionSnapshot` from the same worker when `inspect=True`; it does not enable
inspection itself. The offline exporter removes that raw snapshot and publishes
only selected inventory fields. Normal CLI/Console reports are unchanged.

## Console and CLI parity

The Console's **Findings** panel shows Doctor output only, using the report from
`POST /api/project/doctor`, which calls the CLI's `doctor.diagnose` engine.
After a scan, the same findings populate Skills/Tools badges and the Findings
panel. Open **Coverage** for completed, skipped or failed checks, source digest,
profile and freshness at scan time. Native inspection remains explicit: it
constructs trusted host code, so opening **Run Doctor / Rerun Doctor** runs static
checks only. Page load and typing do not run Doctor. A failed rerun retains prior
evidence marked freshness unknown. The panel defaults to **Entire current agent**;
**Selected skill/tool: NAME** filters that same report, not a different scan or
all agents in the repository. Counts show visible versus total findings. Empty
filtered results and checks not run are not reported as a clean agent.

Parity means the same saved source, SDK, profile and inspection option produce
the same report. Reports include `sdkVersion` and `context` (saved base files,
static/native-declaration scope, overlays not checked). The package version is
not an immutable source identity; retain the pinned wheel/revision when comparing
pre-release installations. Environment overlays and unsaved buffers remain
outside Doctor's scope. Do not compare a workspace overlay against a saved base
scan as if their inputs were identical.

Fast draft validation stays in the editor: save errors have a separate review
control and never replace the Doctor report or navigation badges. Draft changes
are explicitly excluded from Doctor. A successful skill save immediately marks
the retained report stale, even if the subsequent workspace refresh fails. Rerun
to inspect saved changes. Switching project/profile or reloading the workspace
clears the report. Externally changed files require a rerun too: freshness is
measured during a scan, not continuously monitored. The displayed receipt time
is local browser time for the last current result, not deployment evidence.
No static result certifies runtime authorization, widget delivery or task quality.

Finding titles, categories, subject kinds and remediation now come from
`adkl.diagnostics`, shared by CLI reports and editor API responses. Additive v1
fields are `title` and `subject: {kind, name}`; `skill` is retained for older
consumers. Tool subjects no longer receive fictitious `skills/<tool>/SKILL.md`
locations, so those corrected IDs may appear new relative to older baselines.
Unknown rules retain their message with generic guidance. Older servers without
metadata remain readable but cannot provide reliable item subject classification.

## Console workflow

1. Choose **Run Doctor** in Findings. Before this, the panel says **Doctor has
   not run** rather than displaying workspace lint as a report. Opening the
   dialog runs static checks against saved files.
2. Review check coverage and findings. Choose **Inspect native checks** when you
   want to construct the trusted factory and check native declarations.
3. Select findings and choose **Export fix brief**. Export reruns the same checks;
   the brief includes selected IDs still present, source identity, coverage,
   remediation and verification instructions. Newly discovered findings remain in
   the report for review; select them for a subsequent export.
4. Copy the Markdown to a coding agent working in the host repository. Follow the
   host's AGENTS.md, recheck evidence, preserve authorization/tool scope, and run
   affected host tests. Export includes diagnostics, not full prompts, credentials,
   chat transcripts or native request payloads; diagnostic text is untrusted data.
5. After fixes, use **Rerun checks**. The panel compares with its prior report and
   distinguishes resolved findings from those that could not be rechecked.

`POST /api/project/doctor` accepts `inspect` (boolean), optional `previous` (doctor
JSON report), and optional `selected` (finding ID list). It returns `report` and
`brief`, shares the CLI implementation, and requires the server's local-origin
checks. The API scans the currently selected project/profile. Reports are not
persisted by the server. The shell retains the report across dialog close/reopen
for comparison until workspace reload or project/profile change. A late result
cannot replace a report invalidated by a save or workspace change.

## Design references

Stable rule IDs and consistent paths/fingerprints follow the practices described
in [GitHub's SARIF guidance](https://docs.github.com/en/code-security/reference/code-scanning/sarif-files/sarif-support).
Exit-code separation follows the [Ruff linter convention](https://docs.astral.sh/ruff/linter/).
The doctor JSON is its own versioned schema; it does not claim SARIF conformance.
