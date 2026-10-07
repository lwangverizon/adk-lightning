# ADKL competitive position — September 6, 2026

ADKL is an engineering alpha with a credible incremental SDK adoption path. It has useful, tested mechanisms for existing Google ADK applications, but has not established the customer outcomes, operating capabilities, or commercial adoption needed to compete as a complete enterprise agent platform. Its most promising initial market is teams that already own ADK applications and want to reduce repeated implementation and make agent changes easier to review and verify.

This assessment uses ADKL `main` at `56ecbe570fe760b09e21911087c4e369c10379aa`, including the shared callback work merged in MR !7. “Sierra” is interpreted as **sierra.ai**, rather than the apparent typo “sierra.ak.” Public sources were accessed September 6, 2026. Competitor capabilities below are vendor-documented, not independently benchmarked. No competitor accounts, production integrations, or external application code were executed. Source inspection and the previously completed release checks establish implementation evidence; they do not establish customer task quality. Recommendations and relative positioning are analytical judgments.

The September 4 [competitive plan](../../COMPETITIVE_PRODUCT_PLAN.md) is a historical baseline. Its claims about missing packaging, missing CI, and incomplete procedure loading should not be used as the current inventory. This document updates the assessment without rewriting that history.

**What ADKL can substantiate today**

| Area | Current evidence | Practical limit |
| --- | --- | --- |
| Adoption inside existing agents | `build_callback_adapter()` composes native hooks; raw result observers precede sequential transforms. | A bounded callback interface, not arbitrary agent import or automatic migration. |
| Shared runtime mechanics | Request-only tool-history repair, declared result projection, history slicing and trimming. | Repair is for completed batches; projection is not a privacy boundary or hard size cap. |
| Native skills | SDK factory and preview share native ADK execution, candidate retrieval, confident activation, state pins, continuation and procedure refresh. | No measured routing advantage over native ADK; lexical backfill is not full BM25. |
| Typed context | Active skills receive declared state with type checks; required missing values block the model call. | Host supplies trustworthy, fresh state; old conversation content is not erased. |
| Authoring | Flutter editor, AST tool catalog, shared CLI/editor lint, compiled prompt inspection and small file diffs. | Domain-author usability and change lead time have not been measured with independent users. |
| Presentation | Reusable packs, staged widgets and A2UI primitive rendering. | Interactive actions, mandatory-widget enforcement and standalone presentation export remain unavailable. |
| Diagnostics | Static audit plus per-turn retrieval, activation and model-call diagnostics. | No complete versioned behavioral evaluation product, production analytics or outcome measurement. |
| Delivery | Installable wheel, locked runtime, required CI checks and hashed console review build; MR !7 merged after CI. | A console review artifact is not a production platform or certified agent release. |
| Execution ownership | Host retains Runner, sessions, model integration and authorization. | Console remote transport is unimplemented; subprocess isolation is not tenant or hostile-code isolation. |

Evidence: [capability inventory](../../adkl/capabilities.py), [SDK contract](../sdk.md), [adapter implementation](../../adkl/adapters.py), [native adoption tests](../../tests/runtime/test_callback_adoption.py), [SDK/preview tests](../../tests/runtime/test_sdk_runtime.py), [execution profiles](../environments.md), and [delivery boundaries](../ci-cd.md).

The preceding release recorded 466 component tests passed and 498 tests passed in the runtime-enabled suite, with four skips in each. These suites overlap and must not be added together. An isolated wheel installation passed 22 focused tests. This research did not rerun them. host-specific private-wheel compatibility, external application integration, live-model quality and performance remain unverified.

**The competitive field**

The buying decisions differ. Sierra and enterprise suites sell business capabilities and an operating environment; native ADK and LangChain serve engineering teams; visual application platforms serve builders who want more packaged infrastructure. ADKL should choose one initial buyer rather than treating all of these as interchangeable rivals.

| Alternative | Publicly documented offering | Where ADKL stands |
| --- | --- | --- |
| **Sierra** | Customer-facing agents across channels, monitoring, testing and an outcome-priced delivery partnership. Ghostwriter adds AI-assisted creation and improvement; Horizon adds long-running customer goals. | Substantially behind as a complete CX platform. A narrower customer-owned ADK SDK is a different purchase, potentially avoiding an application migration. [Product](https://sierra.ai/product), [Ghostwriter](https://sierra.ai/product/ghostwriter), [Horizon announcement](https://sierra.ai/uk/blog/horizon). |
| **Google ADK Visual Builder** | Experimental graphical construction, AI assistant, LLM/sequential/loop/parallel agents, tools and callbacks, with YAML/Python files. | Google covers broader visual composition. ADKL focuses on skill maintenance and reusable runtime policy in existing applications. Google's documented editor accepts agents created with the builder and warns that manual edits may become incompatible. [Visual Builder](https://adk.dev/visual-builder/). |
| **Native ADK plus internal utilities** | Incrementally loaded skills, callbacks and developer evaluation/deployment facilities. | The closest substitute: a team can retain a small internal helper library. ADKL must demonstrate that shared maintenance and verification outweigh dependency and migration costs. [Skills](https://adk.dev/skills/), [evaluation](https://adk.dev/evaluate/), [Cloud Run deployment](https://adk.dev/deploy/cloud-run/). |
| **LangChain / LangSmith** | Durable agent deployment, streaming, scaling and several hosting models; offline/online evaluation; Fleet for no-code agents and approvals. | Far ahead in the engineering lifecycle. ADKL can specialize in ADK semantics and integrate an existing evaluation/observability platform. Self-hosting is not an exclusive ADKL advantage. [Deployment](https://docs.langchain.com/langsmith/deployment), [evaluation](https://docs.langchain.com/langsmith/evaluation), [Fleet](https://docs.langchain.com/langsmith/fleet). |
| **Voiceflow V4** | Agent routing into autonomous playbooks and deterministic workflows; layered context, tools, memory and transcript evaluation. | A close product-design benchmark. Skills and selective context already overlap with its offering; ADKL currently lacks the surrounding operating loop. [V4 announcement, updated May 12, 2026](https://www.voiceflow.com/stories/everything-we-launched-v4). |
| **Dify** | Visual workflows, retrieval, code/tools, recovery paths, release history, DSL portability, publishing and observability integrations. | Ahead as a general application builder. ADKL's distinction is adoption within a native ADK host; Dify DSL portability should not be equated with executable ADK export. [Workflow Studio](https://dify.ai/workflows). |
| **Salesforce Agentforce** | Canvas/script authoring, deterministic logic combined with LLM reasoning, session traces and lifecycle tooling. | Better positioned for Salesforce-centered processes. ADKL can serve custom ADK systems but lacks comparable packaged business administration. [Builder migration](https://www.salesforce.com/agentforce/agent-builder/migration/), [testing lifecycle](https://www.salesforce.com/blog/agentforce-testing-center/). |
| **Microsoft Copilot Studio** | Low-code agents/flows, connectors, evaluation and enterprise governance within the Microsoft environment. | Better positioned for Microsoft-centered deployments. ADKL's value would be preserving a custom ADK application and runtime boundary, not competing on connector breadth. [Overview](https://learn.microsoft.com/en-us/microsoft-copilot-studio/fundamentals-what-is-copilot-studio), [governance](https://learn.microsoft.com/en-us/microsoft-copilot-studio/guidance/sec-gov-intro). |

This is a selected competitor set, not an exhaustive market census. It covers managed CX, native framework tooling, developer operations, visual builders and enterprise suites. No market-share ranking or revenue forecast is implied.

**What changed in the market assessment**

Sierra's July 16, 2026 Horizon announcement describes customer goals pursued across days or longer, using persistent interaction context. Its significance for ADKL is the scope of competition: a capable single conversation is only one part of that product promise. Availability, contract terms and performance for a particular buyer still require validation. [Horizon](https://sierra.ai/uk/blog/horizon).

Sierra also documents turning SOPs and transcripts into journeys, generating simulations, and proposing fixes through Ghostwriter. An authoring assistant alone would therefore be a catch-up feature; the useful product is the complete path from change to verified behavior. [Ghostwriter](https://sierra.ai/product/ghostwriter).

Voiceflow's V4 architecture overlaps directly with ADKL's skill-centered direction. This weakens any claim that routing among modular procedures or limiting context is unique. It strengthens the case for benchmarking the specific ADKL implementation rather than marketing the architecture itself. [Voiceflow V4](https://www.voiceflow.com/stories/everything-we-launched-v4).

Salesforce says new agent creation moved to its new builder in July 2026. LangSmith's current documentation calls its former Agent Builder **Fleet**. Comparisons based only on older screenshots or launch posts would miss both changes. [Salesforce migration](https://www.salesforce.com/agentforce/agent-builder/migration/), [Fleet documentation](https://docs.langchain.com/langsmith/fleet).

**Where there is a credible opportunity**

The strongest offer today is: **“Adopt tested shared behavior in the ADK agents you already operate, while keeping your application, tools and deployment.”** The next product layer could make those procedures easy to author, evaluate and release. That next layer is a direction, not a completed capability.

The shared callback adapter makes this offer concrete. Prospect and customer have different orchestration and domain rules, but they can consume a shared callback contract. Engineers can adopt history repair or result projection individually, keep existing guards, and reverse a substitution if customer tests fail. Native tests establish those mechanics with synthetic tools; the host-specific environment must establish that the integration is useful.

There are three potential advantages worth validating:

1. **Lower adoption effort for existing ADK teams.** Incremental SDK integration avoids requiring a full agent rebuild. Measure actual integration and maintenance hours; the advantage is presently architectural, not an observed customer result.
2. **Reviewable changes to procedures and shared behavior.** Canonical Markdown/YAML and shared lint can help an engineer see what changed. Add outcome tests and release identity so review covers behavior as well as text.
3. **A shared regression corpus for recurring ADK failures.** Callback ordering, missing tool results, state projection and activation are concrete boundaries. Repeated use across independent applications could create valuable compatibility knowledge. One application-derived implementation is insufficient evidence of broad reuse.

Generic features offer weaker differentiation: YAML, skills, bring-your-own-model configuration, widgets, callback wrappers, self-hosting and a canvas are all reproducible. ADKL can still implement them when they improve the product, but should not treat any one as a durable competitive advantage.

**The gaps that determine the next stage**

| Gap | Why it affects adoption | Completion evidence |
| --- | --- | --- |
| host-specific integration | The first intended host has not consumed the new SDK in its actual environment. | Pinned private dependencies; prospect/customer replay results; host guards preserved; canary and rollback evidence. |
| Behavioral evaluation | Passing mechanics tests does not prove the right skill, answer or business action. | Repeatable task sets, tool/state assertions, repeated model runs, version comparisons and reviewed failures. |
| Release identity | Git revisions alone do not capture all prompts, tools, indexes and provider settings used by an agent. | A complete agent manifest binding artifacts and evaluations to a deployable revision, plus a tested rollback. |
| Operator feedback | Current diagnostics do not tell a team which recurring failures to fix. | Host trace integration linking revision, skill, tool result, latency/cost and verified outcome; failures become regressions. |
| Independent usability | A working editor does not prove that domain experts can safely maintain procedures. | A new user completes a realistic change without the original developer, with time and error rate recorded. |
| Production control plane | The development server cannot safely serve as a shared enterprise service. | Authenticated API, scoped persistence, access controls, audit records and tested operational isolation before shared hosting. |

Authorization requires a precise product boundary. An embedded SDK can legitimately leave identity and action authorization with its host; ADKL need not become an identity provider before a controlled integration. It must preserve and test the host's controls. If ADKL promises to operate agents or enforce tool policy itself, those controls become ADKL's implementation responsibility. `identity_gate` is unsupported and `progressive_auth` only records attempts; neither is evidence of enforcement.

SDK runtime reuse is already viable for supported configurations. Standalone generated export remains blocked. Remote execution from the console is also absent. Installing a wheel in a host environment and invoking it there does not depend on completing a remote console transport; keep those milestones distinct.

**Recommended sequence**

The following is an order of investment, not a delivery-time estimate.

1. **Validate reuse in an existing ADK application.** Enable the extracted mechanics individually under existing host flags, with unchanged models/tools and matched scenarios. Cover parallel tool calls, unknown outcomes, cancellation, false/zero/null data, denied actions and retained source state. Restore the baseline on regression. The [adoption guide](../adoption.md) contains the integration boundaries.
2. **Add an ADK evaluation workflow.** Prefer an adapter around native ADK evaluation or an established evaluation system. Own the project-specific contracts and revision mapping. Support deterministic checks as well as human-reviewed or calibrated model grading.
3. **Connect evidence to releases.** Bind project files, tools, retrieval inputs, dependency versions and evaluation results. A draft should become a release only when its required checks pass, with a known rollback target.
4. **Prove a second use case independently.** Use a different organization or application with materially different tools and state. ClawTutor helps test technical generality; it does not by itself establish independent market demand.
5. **Expand the console around observed work.** Prioritize author changes, test failures, diffs and releases. Add broader visual composition, managed channels or long-running automation only when a validated buyer needs them.

Avoid replacing a host application's complete retrieval, prefetch or authorization systems during the initial extraction. Their parity conditions are larger than the generic callbacks, and an expansive migration would make regressions harder to attribute.

**How to establish whether ADKL is better**

Use two comparisons. For the immediate extraction, compare the same host with and without the ADKL substitutions. For the broader SDK, compare an ordinary ADK agent with native SkillToolset against the ADKL factory, holding model, tools, knowledge, tasks and budgets fixed.

| Measure | Definition |
| --- | --- |
| Adoption effort | Engineer time to integrate, validate, upgrade and roll back a shared feature. |
| Change lead time | Procedure change requested to reviewed, tested release. |
| Task success | Correct answer or verified backend outcome, not the model saying “done.” |
| Regressions | Wrong skill, incorrect arguments, duplicate actions, lost state or broken host guards. |
| Cost per success | Model, embedding, runtime and integration costs divided by verified successful tasks. |
| User-visible latency | Median and tail time to useful output and final completion, reported separately. |
| Reuse | Shared implementation consumed without domain-specific forks across independent applications. |

A practical starting set is 50–100 representative and adversarial scenarios per pilot workflow, followed by repeated runs for stochastic cases. This is a proposed test design, not a statistical sufficiency claim. Define acceptable quality loss, sample size and operational thresholds with the host owner before evaluation. No current evidence supports a percentage claim about savings or superiority.

**Commercial interpretation**

The plausible first buyer is an ADK application or platform lead maintaining several agents. The immediate alternative is their own shared utility library. The purchase must justify ongoing compatibility, regression coverage and faster changes; simply moving code into a package is unlikely to sustain a business.

A developer tool/SDK with paid verification and release support is a more attainable initial offer than a managed CX platform. Customer-owned execution can be a deployment preference, but ADKL still carries support and integration costs. Sierra documents outcome-based pricing, whereas ADKL has no validated pricing or outcome-accounting model. There is no evidence here that ADKL is cheaper in total cost. [Sierra pricing approach](https://sierra.ai/product).

**Decision**

Continue with the incremental ADK strategy and use a controlled host integration as the next evidence-producing milestone. Current status is **engineering alpha; ready to attempt a controlled host integration; production and commercial validation pending**. The next meaningful advance is a verified improvement in an existing agent and a repeatable change-to-release process. A larger editor or more registered features would not establish that result.
