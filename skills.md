---
name: identity-governance-openshell-demo
description: Scaffolds an end-to-end runnable demo of an MCP intent-triage server that issues delegated identity credentials to specialist agents running in individual NVIDIA OpenShell sandboxes, with two independent enforcement gates (sandbox + identity) and a unified audit trail. Use this whenever the user wants to build, scaffold, generate code for, or prepare a live demo of "identity governance", "agent identity governance", "OpenShell sandboxing", "MCP triage/routing agent", or a multi-agent banking/servicing demo with segmentation and access control — even if they only describe the concept loosely (e.g. "the demo we talked about", "the triage + sandbox thing", "the identity governance talk demo"). Also use to regenerate or extend that demo (add a new specialist agent, add a new breach scenario, change the credential scheme).
---

# Identity governance + NVIDIA OpenShell demo builder

## What this produces

A runnable Node.js project implementing this architecture:

```
User request
  -> MCP triage server (classifies intent + segment, itself sandboxed)
  -> Identity governance layer (issues a short-lived signed delegated
     credential: User -> Triage -> Specialist)
  -> One of N specialist agents, each in its own OpenShell sandbox, each
     enforcing TWO independent gates before acting:
       1. OpenShell sandbox gate  (OS-level: files, network, process)
       2. Identity credential gate (is this specific delegated credential
          authorized for this specific intent/scope?)
  -> Audit trail logging identity, intent, and decision at every hop
```

The point of the two-gate design is that the gates must be able to
diverge: a request the sandbox would technically allow but the identity
layer denies (and vice versa). That divergence is the core "aha" moment
for a talk or demo — don't collapse the two gates into one check.

## Quickstart

Run the scaffold script to generate the full project:

```bash
python3 scripts/scaffold_demo.py <output-dir>
```

Then, inside `<output-dir>`:

```bash
npm install
node triage-server.js
```

This starts the triage server and three specialist agents (fraud,
offers, servicing) as in-process modules with a CLI driver
(`node demo.js`) that walks through the three live demo scenarios.

## Before presenting this live

NVIDIA OpenShell is a recent (2026) sandboxing runtime. The scaffold
generates OpenShell policy files in `openshell/*.policy.json` using a
best-effort, clearly-commented mock schema (per-agent filesystem paths,
network egress rules, process-spawn rules) so the demo runs standalone
without a real OpenShell install. **Before a live audience**, web-search
for NVIDIA's current OpenShell policy syntax and CLI, and swap the mock
`openshell/sandbox-runner.js` shim for the real `openshell` invocation —
do not present the mock shim as the actual product without saying so.
Everything else (triage classification, JWT delegation chain, the
two-gate check, audit log) is real, working code, not a mock.

## Customizing the scaffold

Before running the script, ask the user (or infer from context) about:
- **Domain**: defaults to banking (fraud/offers/servicing). Swap for
  their actual use case by editing the `AGENTS` list at the top of
  `scripts/scaffold_demo.py` before generating — each entry is
  `{name, intent_labels, scope, mock_data}`.
- **Credential scheme**: defaults to signed JWTs with a 60-second
  expiry (short-lived on purpose, to make the revocation demo fast to
  show live). Don't lengthen this without asking — a long expiry makes
  the revocation scenario boring to watch.
- **Number of specialist agents**: 2–4 works well on a single 680px
  architecture slide; more than 4 gets visually cramped if they're also
  presenting the architecture diagram alongside the demo.

## The three live demo scenarios (generated in `demo.js`)

Walk through these in order — each proves a different claim:

1. **Sandbox-only breach** — the offers agent attempts to read
   fraud-case data. The OpenShell sandbox gate blocks it at the OS
   level (mocked via the sandbox-runner shim) before the identity gate
   is even reached. Proves: OS-level isolation works independently of
   application logic.
2. **Identity-only breach** — a request where the sandbox WOULD allow
   the action (the agent has filesystem/network access to the
   resource) but the delegated credential was not issued for that
   intent/scope. The identity gate denies it anyway. **This is the
   scenario to protect if time runs short** — it's the only one that
   proves identity governance is doing something OpenShell's sandbox
   cannot.
3. **Revocation mid-session** — revoke the triage server's or a
   specialist's credential live (a single CLI command in `demo.js`)
   and show the agent's next action rejected instantly, with its code
   and sandbox completely unchanged. This is the most visually
   memorable moment — do it last.

After running, `audit-log.json` contains the full chain (user, triage
decision, credential issued, gate results, final outcome) for every
scenario — useful to screenshot for the "this is the artifact
regulators actually ask for" talking point.

## Talking points to surface alongside the demo

- Sandboxing answers "what can this process touch"; identity governance
  answers "who is this process, on whose authority, and can that
  authority be revoked independent of its code." Say this explicitly —
  it's the single sentence that differentiates this from a generic
  OpenShell demo.
- The delegation chain (User -> Triage -> Specialist) as a logged
  artifact maps directly to what ISO 42001 assessors and regulators ask
  for from agentic systems — this is a concrete, screenshot-able
  answer to "how do you govern autonomous agents," not an abstract claim.
- Revocation without redeployment is the clip-worthy moment — lead the
  Q&A or a follow-up post with that clip, not the architecture diagram.
- The pattern generalizes past banking (healthcare, insurance, any
  regulated multi-agent system) — say this once near the end so the
  audience self-selects into "this applies to me."

## If asked to extend the demo

- **New specialist agent**: add an entry to the `AGENTS` list in
  `scripts/scaffold_demo.py` and re-run the scaffold; it generates the
  agent file, its OpenShell policy stub, and wires it into the triage
  router automatically.
- **New breach scenario**: add a step to `demo.js`'s `SCENARIOS` array
  following the existing three — each is a plain object with
  `{name, description, run(triage, agents)}`.
- **Swap JWT for a real PKI/mTLS scheme**: isolate this in
  `credential.js`; the rest of the code only calls
  `issueCredential()` / `verifyCredential()`, so the interface doesn't
  change.
