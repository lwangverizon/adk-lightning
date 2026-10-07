# Adoption and product validation plan — September 2026

This is an execution plan, not evidence of traction or a release announcement.
The initial audience is teams maintaining existing Google ADK applications.
Positioning: **Understand, maintain and verify your Google ADK agents. Domain
experts maintain skills; developers share tested runtime behavior.**

## First deliverable

Ship the local [saved-change verification workflow](../verification.md): connect a
host evaluator, record a baseline, edit a skill, run complete cases, compare
results and retain evidence tied to declared files. Demonstrate a broken edit
and its recovery using the maintained native example. Evaluation remains owned
by the host; production publication remains a separate process.

This slice supplies configured local execution, baseline/candidate comparison,
changed-file hashes and a Project panel. It does not yet supply textual diffs,
collaborative review, automatic case generation, hosted evaluation history,
release promotion or production rollback. Validate usage before expanding them.

## Milestones and decision gates

| Period | Work | Completion evidence |
|---|---|---|
| Weeks 1–2 | Interview 8–10 teams maintaining ADK applications; show a working demo | Recorded recent maintenance problems, time spent, existing workaround, owner and buyer; explicit permission for any published quotation |
| Weeks 1–3 | Resolve licensing/redistribution, versioning and distribution; simplify the quickstart | Licensed reproducible release, compatibility notes, fresh-machine install and an unfamiliar developer's observed first inspection |
| Weeks 3–6 | Verify representative applications from independent organizations | Host-owned acceptance records, actual adoption effort, adapter differences, rollback path and repeated use |
| Weeks 4–8 | Observe domain specialists making representative changes | Saved revision, accepted cases, detected regression, elapsed time and developer assistance required |
| Weeks 8–12 | Offer a bounded paid collaboration/verification pilot | Named recurring workflow, repeated use, measured improvement and a payment commitment |

Targets to test: first useful inspection within 15 minutes on a supported setup;
a representative saved skill change evaluated within 30 minutes; three independent
teams using the project weekly; two willing to pay for a recurring capability.
These are proposed decision thresholds, not achieved metrics or market benchmarks.
Record unsuccessful attempts as well as successful ones.

The maintained examples provide synthetic integration evidence. Real applications
need host-specific acceptance. Two applications owned by one team can test
technical reuse; they do not establish independent customer demand.

## Interview guide

Use recent concrete work instead of asking whether someone likes the idea:

1. Walk through the last agent behavior change from request to production.
2. Who edited it, who reviewed it, and where did it wait?
3. Which callbacks, context handling or skill conventions are copied across agents?
4. What regression escaped your checks? How was it diagnosed and rolled back?
5. What can domain specialists change today, and where do they require engineering?
6. Which tools do you already use for tracing, evaluations and prompt management?
7. Try the demo on a representative change. Where does it save work or add work?
8. Who would approve a paid pilot, and what measurable outcome would justify one?

Record organization alias, role, agent count, recent task, current tools, baseline
time, observed ADKL time, assistance needed, return-use date and willingness to
pay. Keep sensitive customer information outside this public repository. No
outreach messages have been sent by this plan.

## Pilot offer for review

Proposed scope: one existing ADK application, two recurring skill-maintenance
workflows, its host-owned acceptance cases, and a four-week evaluation period.
Measure change lead time, regressions caught, integration/upgrade effort and
weekly use. Define excluded application work and a fixed commercial agreement
before starting. Price and customer commitments remain to be established.

The developer connects the host; a domain specialist performs representative
changes; the application owner reviews evidence and controls release. Success
means the team continues this workflow without bespoke intervention for every
change. Repeated requests for the same review/history capability justify the
next product increment. Mostly one-off integration work suggests a service
offering or a need to simplify adoption before building a platform.

## Distribution and ownership decisions

- Repository owner: choose licensing and complete redistribution review before
  a public licensed release. No license is selected by this plan.
- Maintainers: follow [SDK release guidance](../sdk-releases.md) and
  [delivery requirements](../ci-cd.md); publish only reproducible, identified builds.
- Application owners: provide approved application access, representative cases and their
  [host acceptance evidence](../host-acceptance.md).
- Product owner: identify interview participants and authorize any outreach.
- Team: publish a short reproducible demo and sanitized findings once approved;
  track activation and weekly use rather than treating stars as demand.

Keep the first public offer focused on existing-agent adoption and verified
maintenance. Broaden framework support, visual orchestration or hosted serving
only when independent users' recurring needs justify the additional surface.
