---
name: explain
description: Explain a short process using supplied facts. Exclude requests to perform external actions.
metadata:
  adk_additional_tools: [render_steps]
---
## Workflow

### Explain a process

- [ ] Step 1: Identify the requested outcome and the facts already supplied.
- [ ] Step 2: When ordered steps help, call `render_steps` with a short list.
- [ ] Step 3: Give a brief companion reply without repeating the card.

## Recovery

Ask one focused question when an essential fact is missing. State when an
external action requires an integration this example does not provide.
