#!/usr/bin/env python3
"""
Scaffolds an end-to-end runnable demo of:
  MCP triage server -> identity governance layer (delegated JWT credentials)
  -> N specialist agents, each in its own (mocked) OpenShell sandbox,
  each enforcing two independent gates (sandbox + identity) -> audit log.

Usage:
    python3 scaffold_demo.py <output-dir>

Edit AGENTS below before running to adapt the demo to a different domain.
"""
import json
import os
import sys

AGENTS = [
    {
        "name": "fraud",
        "label": "Fraud agent",
        "intents": ["dispute_charge", "report_fraud"],
        "scope": "fraud-case API only",
        "mock_resource": "fraud_cases.json",
    },
    {
        "name": "offers",
        "label": "Offers agent",
        "intents": ["view_offers", "segment_lookup"],
        "scope": "read-only offer catalog",
        "mock_resource": "offer_catalog.json",
    },
    {
        "name": "servicing",
        "label": "Servicing agent",
        "intents": ["update_profile", "account_lookup"],
        "scope": "account records",
        "mock_resource": "account_records.json",
    },
]

CREDENTIAL_TTL_SECONDS = 60


def w(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def scaffold(out_dir):
    # -------------------------------------------------------------- package.json
    w(
        os.path.join(out_dir, "package.json"),
        json.dumps(
            {
                "name": "identity-governance-openshell-demo",
                "version": "0.1.0",
                "type": "commonjs",
                "scripts": {"demo": "node demo.js"},
                "dependencies": {"jsonwebtoken": "^9.0.0"},
            },
            indent=2,
        )
        + "\n",
    )

    # -------------------------------------------------------------- mock data
    w(
        os.path.join(out_dir, "data", "personas.json"),
        json.dumps(
            {
                "user_alice": {"segment": "standard", "risk_flag": False},
                "user_bob": {"segment": "high-net-worth", "risk_flag": False},
                "user_carol": {"segment": "fraud-risk", "risk_flag": True},
            },
            indent=2,
        )
        + "\n",
    )
    for agent in AGENTS:
        w(
            os.path.join(out_dir, "data", agent["mock_resource"]),
            json.dumps({"note": f"mock {agent['scope']} data for the {agent['label']}"}, indent=2)
            + "\n",
        )

    # -------------------------------------------------------------- credential.js
    w(
        os.path.join(out_dir, "credential.js"),
        f'''\
// Identity governance layer: issues and verifies short-lived delegated
// credentials. Kept isolated so a real PKI/mTLS scheme can replace JWTs
// without touching triage-server.js or the agents.
const jwt = require("jsonwebtoken");

const SECRET = "demo-only-secret-do-not-use-in-production";
const TTL_SECONDS = {CREDENTIAL_TTL_SECONDS};

const revoked = new Set();

function issueCredential({{ userId, issuedBy, intent, scope }}) {{
  const payload = {{
    sub: userId,
    delegatedBy: issuedBy, // e.g. "triage-server"
    intent,
    scope, // which agent/resource this credential authorizes
    jti: `${{userId}}-${{Date.now()}}`,
  }};
  return jwt.sign(payload, SECRET, {{ expiresIn: TTL_SECONDS }});
}}

function verifyCredential(token, {{ requiredScope, requiredIntent }}) {{
  try {{
    const decoded = jwt.verify(token, SECRET);
    if (revoked.has(decoded.jti)) {{
      return {{ ok: false, reason: "credential revoked" }};
    }}
    if (decoded.scope !== requiredScope) {{
      return {{ ok: false, reason: `credential scoped for "${{decoded.scope}}", not "${{requiredScope}}"` }};
    }}
    if (requiredIntent && decoded.intent !== requiredIntent) {{
      return {{ ok: false, reason: `credential issued for intent "${{decoded.intent}}", not "${{requiredIntent}}"` }};
    }}
    return {{ ok: true, decoded }};
  }} catch (e) {{
    return {{ ok: false, reason: e.message }};
  }}
}}

function revoke(token) {{
  try {{
    const decoded = jwt.decode(token);
    if (decoded && decoded.jti) revoked.add(decoded.jti);
    return true;
  }} catch {{
    return false;
  }}
}}

module.exports = {{ issueCredential, verifyCredential, revoke }};
''',
    )

    # -------------------------------------------------------------- openshell sandbox shim
    w(
        os.path.join(out_dir, "openshell", "sandbox-runner.js"),
        '''\
// MOCK shim standing in for the real `openshell` runtime/CLI.
// Before presenting this live, replace checkSandbox() with an actual
// call into NVIDIA OpenShell (verify current syntax via NVIDIA's docs --
// this shim predates/approximates it and is for demo purposes only).
const fs = require("fs");
const path = require("path");

function loadPolicy(agentName) {
  const p = path.join(__dirname, `${agentName}.policy.json`);
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

// Returns { allowed, reason } -- OS-level check ONLY. Knows nothing
// about identity or credentials; that's a separate, independent gate.
function checkSandbox(agentName, { resource, action }) {
  const policy = loadPolicy(agentName);
  const allowedResources = policy.filesystem.allow;
  if (!allowedResources.includes(resource)) {
    return {
      allowed: false,
      reason: `sandbox policy for "${agentName}" does not permit access to "${resource}"`,
    };
  }
  if (!policy.actions.allow.includes(action)) {
    return {
      allowed: false,
      reason: `sandbox policy for "${agentName}" does not permit action "${action}"`,
    };
  }
  return { allowed: true };
}

module.exports = { checkSandbox, loadPolicy };
''',
    )

    for agent in AGENTS:
        w(
            os.path.join(out_dir, "openshell", f"{agent['name']}.policy.json"),
            json.dumps(
                {
                    "_comment": "Mock OpenShell policy schema -- confirm real field names against NVIDIA's current docs before live use.",
                    "agent": agent["name"],
                    "filesystem": {"allow": [agent["mock_resource"]]},
                    "network": {"allow_egress_to": []},
                    "process": {"allow_spawn": []},
                    "actions": {"allow": ["read"]},
                },
                indent=2,
            )
            + "\n",
        )

    # -------------------------------------------------------------- agent files
    for agent in AGENTS:
        w(
            os.path.join(out_dir, "agents", f"{agent['name']}-agent.js"),
            f'''\
const {{ verifyCredential }} = require("../credential.js");
const {{ checkSandbox }} = require("../openshell/sandbox-runner.js");
const {{ logDecision }} = require("../audit-log.js");

const AGENT_NAME = "{agent['name']}";
const RESOURCE = "{agent['mock_resource']}";

// Two INDEPENDENT gates. Do not short-circuit or merge them -- the
// whole point of the demo is that they can disagree.
// `resource` defaults to this agent's own scope, but callers can pass a
// different resource to simulate an attempted out-of-scope access.
function handle({{ credential, intent, action = "read", resource = RESOURCE }}) {{
  const sandboxResult = checkSandbox(AGENT_NAME, {{ resource, action }});
  const identityResult = verifyCredential(credential, {{
    requiredScope: AGENT_NAME,
    requiredIntent: intent,
  }});

  const outcome = {{
    agent: AGENT_NAME,
    intent,
    sandbox: sandboxResult,
    identity: identityResult,
    executed: sandboxResult.allowed && identityResult.ok,
  }};
  logDecision(outcome);

  if (!sandboxResult.allowed) {{
    return {{ status: "denied_sandbox", reason: sandboxResult.reason }};
  }}
  if (!identityResult.ok) {{
    return {{ status: "denied_identity", reason: identityResult.reason }};
  }}
  return {{ status: "executed", scope: "{agent['scope']}" }};
}}

module.exports = {{ handle, AGENT_NAME }};
''',
        )

    # -------------------------------------------------------------- audit-log.js
    w(
        os.path.join(out_dir, "audit-log.js"),
        '''\
const fs = require("fs");
const path = require("path");

const LOG_PATH = path.join(__dirname, "audit-log.json");

function logDecision(entry) {
  const record = { ...entry, ts: new Date().toISOString() };
  let log = [];
  if (fs.existsSync(LOG_PATH)) {
    log = JSON.parse(fs.readFileSync(LOG_PATH, "utf8"));
  }
  log.push(record);
  fs.writeFileSync(LOG_PATH, JSON.stringify(log, null, 2));
  return record;
}

module.exports = { logDecision, LOG_PATH };
''',
    )

    # -------------------------------------------------------------- triage-server.js
    intent_map = {}
    for agent in AGENTS:
        for intent in agent["intents"]:
            intent_map[intent] = agent["name"]

    w(
        os.path.join(out_dir, "triage-server.js"),
        f'''\
// MCP-style triage server: classifies intent + segment, then issues a
// delegated credential naming exactly one downstream agent + intent.
// In production this endpoint itself should run inside its own
// OpenShell sandbox, since it's the first thing touching raw user input.
const {{ issueCredential }} = require("./credential.js");

const INTENT_TO_AGENT = {json.dumps(intent_map, indent=2)};

function triage({{ userId, intentGuess }}) {{
  const agentName = INTENT_TO_AGENT[intentGuess];
  if (!agentName) {{
    throw new Error(`No agent registered for intent "${{intentGuess}}"`);
  }}
  const credential = issueCredential({{
    userId,
    issuedBy: "triage-server",
    intent: intentGuess,
    scope: agentName,
  }});
  return {{ agentName, intent: intentGuess, credential }};
}}

module.exports = {{ triage, INTENT_TO_AGENT }};
''',
    )

    # -------------------------------------------------------------- demo.js
    agent_names = [a["name"] for a in AGENTS]
    w(
        os.path.join(out_dir, "demo.js"),
        f'''\
// Walks through the three live demo scenarios in order. Run: node demo.js
const {{ triage }} = require("./triage-server.js");
const {{ issueCredential, revoke }} = require("./credential.js");
const agents = {{
{",".join(f'''
  {name}: require("./agents/{name}-agent.js")''' for name in agent_names)}
}};

function section(title) {{
  console.log("\\n=== " + title + " ===");
}}

function run() {{
  section("Scenario 1: sandbox-only breach");
  console.log("Offers agent has a VALID, correctly-scoped credential, but attempts to read fraud-case data.");
  const validOffersCredential = issueCredential({{
    userId: "user_alice",
    issuedBy: "triage-server",
    intent: "view_offers",
    scope: "offers",
  }});
  // Identity gate passes (credential is legitimately scoped to "offers"
  // for "view_offers") -- but the sandbox gate blocks it because the
  // offers agent's OpenShell policy never grants access to fraud data.
  console.log(agents.offers.handle({{
    credential: validOffersCredential,
    intent: "view_offers",
    resource: "fraud_cases.json",
  }}));

  section("Scenario 2: identity-only breach (sandbox would allow it)");
  console.log("A credential scoped for offers is used to call the offers agent for the WRONG intent.");
  const mismatchedIntent = issueCredential({{
    userId: "user_carol",
    issuedBy: "triage-server",
    intent: "segment_lookup",
    scope: "offers",
  }});
  console.log(agents.offers.handle({{ credential: mismatchedIntent, intent: "view_offers" }}));

  section("Scenario 3: revocation mid-session");
  const {{ agentName, intent, credential }} = triage({{ userId: "user_bob", intentGuess: "account_lookup" }});
  console.log("Issued credential for", agentName, "-- first call:");
  console.log(agents[agentName].handle({{ credential, intent }}));
  console.log("Revoking credential now...");
  revoke(credential);
  console.log("Same credential, second call:");
  console.log(agents[agentName].handle({{ credential, intent }}));

  console.log("\\nFull audit trail written to audit-log.json");
}}

run();
''',
    )

    # -------------------------------------------------------------- README.md
    w(
        os.path.join(out_dir, "README.md"),
        f'''\
# Identity governance + NVIDIA OpenShell demo

Generated by the identity-governance-openshell-demo skill. See SKILL.md
in the skill package for the full architecture explanation and talking
points.

## Run it

```bash
npm install
node demo.js
```

Then inspect `audit-log.json` for the full decision trail.

## Before a live audience

Replace `openshell/sandbox-runner.js`'s mock `checkSandbox()` with a
real call into NVIDIA OpenShell -- this scaffold uses a JSON-file mock
so the demo runs standalone without an OpenShell install. Verify
current OpenShell policy syntax against NVIDIA's docs first.

## Agents in this build

{chr(10).join(f"- **{a['label']}** ({a['name']}): {a['scope']}, intents: {', '.join(a['intents'])}" for a in AGENTS)}
''',
    )

    print(f"Scaffolded demo project at: {out_dir}")
    print("Next: cd into it, run `npm install`, then `node demo.js`.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 scaffold_demo.py <output-dir>")
        sys.exit(1)
    scaffold(sys.argv[1])
