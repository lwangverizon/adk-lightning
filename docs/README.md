# ADKL documentation

ADKL Runtime provides shared execution patterns, ADKL Doctor diagnoses wiring and
declarations, and ADKL Console provides local authoring, inspection and testing.
Start with the guide for the job you want to do. These are not hosted services;
see the [product overview](../README.md#runtime-doctor-and-console).

| I want to… | Start here |
| --- | --- |
| Understand what works today | [Project overview](../README.md) |
| Review architecture, validation and remaining acceptance work | [Technical review — September 20](reports/technical-review-2026-09-20.md) |
| Find public APIs, implementation owners and tests | [Architecture and contributor map](architecture.md) |
| Diagnose project wiring and export a fix brief | [ADKL Doctor](doctor.md) |
| Understand free tooling and proposed paid services | [Product direction](../COMPETITIVE_PRODUCT_PLAN.md#free-tooling-and-managed-services) |
| Ask a coding agent to integrate ADKL | [Integration playbook](agent-integration.md) |
| Consume a pre-release checkout in another project | [Checkout wheel workflow](agent-integration.md#consume-a-pinned-main-checkout-today) |
| Review instruction layering, skill complexity and model turns | [Agent design](agent-design.md), [final-request layering audit](instruction-layering.md) |
| Run something without credentials | [SDK quickstart](quickstart.md) |
| Try staged widgets and native A2UI rendering | [Minimal projects](../projects/README.md) |
| Add ADKL to an existing agent | [Incremental adoption](adoption.md) |
| Select across skills, workflows and specialist agents | [Unified Capability Selection](capability-bindings.md), [runnable shortlist example](../examples/capability_selection.py) |
| Launch long-running work and retrieve/cancel it later | [Background tasks through Unified Capability Selection](background-tasks.md) |
| Reuse the full lifecycle in my existing constructor | [Runtime bindings](runtime-bindings.md) |
| Create, inspect or package a new agent | [Project onboarding](project-onboarding.md) |
| Compare baseline and candidate results | [Evaluation comparison](evaluation.md) |
| Run a host evaluator for saved skill changes | [Change verification](verification.md) |
| Build an agent from configuration | [SDK runtime contract](sdk.md) |
| Understand where the console connects after SDK integration | [Console connections and limits](project-onboarding.md#where-the-console-connects) |
| Browse multiple agents without connecting to production | [Static inspection portal and container](static-portal.md) |
| Inspect registered agents in my own FastAPI service | [Embedded inspection](embedded-inspection.md) |
| Browse approved loaded UAT declarations | [Connected inspection candidate](connected-inspection.md) |
| Edit skills in the console | [Authoring guide](authoring.md) |
| Configure project environments | [Execution environments](environments.md) |
| Develop and test ADKL | [Development guide](development.md) |
| Maintain or add a shared feature | [Feature maintenance](feature-maintenance.md) |
| Prepare a merge or understand deployment limits | [Delivery process](ci-cd.md) |
| Prepare a public alpha and track remaining owner decisions | [Community release gates](community-release.md), [unreleased notes](releases/unreleased.md) |
| Version and publish the SDK | [SDK releases and CI research](sdk-releases.md) |

## Runtime reference

Use these when you need exact behavior or extension contracts.

- [SDK](sdk.md): factory, callbacks, retrieval, state and diagnostics.
- [Runtime bindings](runtime-bindings.md): native constructors, preparation, delivery and callback order.
- [Unified Capability Selection](capability-bindings.md): one discovery surface, optional shared shortlisting, parent-model choice and native execution.
- [Background tasks](background-tasks.md): persistent submission, authorized task management and independently hosted native workers; no automatic in-flight resume.
- [Capability policy](capability-policy.md): dispatch guards, events and workflow helpers.
- [Shared features](shared-features.md): reusable features and project boundaries.
- [Doctor boundary evidence](doctor-boundaries.md): output consumers, retained contract probes and assembled-request measurements.
- [Generic tool-result sizing](tool-results.md): application-owned reduction, incomplete previews and TOON assessment.
- [Streaming](streaming.md): progress, final responses and cancellation.
- [Presentation contracts](presentation-contracts.md): opt-in visual reply policy with host-owned eligibility and injection.
- [Presentation design](shared-presentation-design.md): packs and widget responsibilities.
- [A2UI rendering](a2ui-support.md): version baseline, primitive matrix, gallery and interaction limits.
- [Execution-support manifest](../adkl/capabilities.py): implemented, partial and unsupported feature targets; not an inventory of Python-only APIs.

## Product direction

[The product plan](../COMPETITIVE_PRODUCT_PLAN.md) and [roadmap](../ROADMAP.md)
describe the free SDK/local UI and proposed managed inspection, execution and fleet
services. They are current direction, not implemented capability or release evidence.

## Decisions and dated reviews

These live in [reports/](reports/) and explain an assessment at a particular
revision. They are not live capability inventories; use the overview and runtime
reference for current behavior.

| Review | Purpose |
| --- | --- |
| [Technical review — September 20](reports/technical-review-2026-09-20.md) | Reproducible SDK/console checks, review walkthrough and readiness limits |
| [Competitive position — September 6](reports/competitive-position-2026-09-06.md) | Market comparison and recommended priorities |
| [Adoption plan — September 2026](reports/adoption-plan-2026-09.md) | Milestones, interview guide and pilot decision gates |
| [Reusable patterns](reusable-agent-patterns.md) | Earlier extraction analysis |

## How to read claims

**Implemented** means the code path exists for the specified target. **Tested**
means a named check exercised it. **Host-verified** requires evidence from the
application's actual environment. None of these alone means production-ready.

Static audit checks declarations. Evaluation comparison checks host-produced
results. Change verification executes a configured local host evaluator and checks
complete reported observations against declared input identities. The host still
owns grading correctness and business-outcome assertions.

Documentation changes follow [the maintenance checklist](development.md#documentation-maintenance).
