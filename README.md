# izin

**A framework-agnostic human approval layer for AI agents.**

_izin_ is Indonesian for _permission_.

> ⚠️ **Status: early design stage.** Nothing here is ready to use yet. The API and SDK described below are the target design and will change.

---

## Why

AI agents are starting to take actions with real consequences: issuing refunds, emailing customers, modifying records, deploying code. Every agent framework offers _some_ way to pause for human approval, but they all stop at the code boundary.

None of them ship the part a human actually touches:

- a queue the approver can see
- a notification that reaches them where they are
- timeout and escalation behaviour when nobody responds
- an audit record an auditor would accept
- a rendering of the request that a non-engineer can understand

So every team rebuilds the same thing, usually as a Slack message with a JSON blob in it. `izin` aims to be that missing layer, built once, framework-agnostic, and self-hostable.

## What it will look like

```python
from izin import Approvals

approvals = Approvals(url=..., token=..., agent_run_id=run.id)

decision = await approvals.request(
    action="refund_order",
    args={"order_id": "ORD-1182", "amount_idr": 4_500_000,
          "customer": {"name": "Dewi S."}},
    rationale="Customer reported item never arrived; courier tracking "
              "shows delivery failure.",
    timeout="4h",
)

if decision.approved:
    await refund(**decision.args)
else:
    await escalate_to_cs(decision.comment)
```

The approver doesn't see JSON. They get a notification and a plain-language screen:

> **Refund Rp 4.500.000 to Dewi S. for order ORD-1182.**

and two buttons.

## Key ideas

- **Durable resume.** The agent process can die while waiting. On restart, the same call picks up the same request, and returns immediately if a decision was already made.
- **Legible requests.** Each action has a template that turns arguments into a sentence a non-technical approver can decide on in seconds.
- **Audit by default.** Every request and decision is append-only, including a snapshot of exactly what the approver saw.
- **Framework-agnostic.** Planned support for bare Python, LangGraph, CrewAI, the Claude Agent SDK, and the OpenAI Agents SDK.
- **Simple to run.** FastAPI + Postgres. No Redis, no message broker. One `docker compose up`.
- **Notifications where people are.** Webhook, email, Slack, and WhatsApp.

## Roadmap

| Milestone                       | Focus                                                                                                                |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| **v0.1** >> Prove the hard part | Idempotent requests, durable resume, minimal React inbox, webhook notifications, bare-Python SDK + LangGraph adapter |
| **v0.2** >> Make it usable      | Timeouts, escalation, Slack / WhatsApp / email, `modify` verdict, CrewAI + Claude Agent SDK adapters, audit export   |
| **v0.3** >> Survive at volume   | Policy engine and auto-decisions, OIDC, analytics, bulk decisions                                                    |

## Non-goals

`izin` is not a security gateway, an orchestration framework, or an observability platform. It assumes an agent has already decided what to do and asks one question: _does a human agree?_

## Contributing

The project is in its design phase. Issues, ideas, and use cases are very welcome, especially if you've built something like this yourself and know where it hurts.

## License

[Apache 2.0](LICENSE)
