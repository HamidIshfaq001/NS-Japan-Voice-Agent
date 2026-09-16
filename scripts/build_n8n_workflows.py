"""
Assemble the two importable n8n workflows from the JS in n8n/src/.

Keeping the JS in real .js files means it stays lintable and reviewable instead of
living as an escaped one-line string inside JSON.

Run:  python scripts/build_n8n_workflows.py
Out:  n8n/nsjapan-inventory-search.workflow.json
      n8n/nsjapan-lead-to-ghl.workflow.json
"""
import json
import os
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "n8n", "src")
OUT = os.path.join(ROOT, "n8n")

NS = uuid.UUID("7b1f1d8e-0000-4000-8000-000000000000")

def _env(key, default=""):
    """Read a key out of .env without pulling in a dependency."""
    path = os.path.join(ROOT, ".env")
    if os.environ.get(key):
        return os.environ[key]
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == key and v.strip():
                        return v.strip()
    return default


# Where the n8n workflow reads live stock from. Set INVENTORY_URL in .env to point at
# your own repo or any other host; this default is only a starting point.
DEFAULT_INVENTORY_URL = _env(
    "INVENTORY_URL",
    "https://raw.githubusercontent.com/HamidIshfaq001/"
    "nsjapan-voice-agent/main/data/inventory.json",
)
GHL_API_BASE = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"


def uid(label):
    return str(uuid.uuid5(NS, label))


def read_js(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as fh:
        return fh.read()


def set_node(name, pos, values):
    """A Set node holding editable configuration."""
    return {
        "parameters": {
            "assignments": {
                "assignments": [
                    {
                        "id": uid(name + ":" + k),
                        "name": k,
                        "value": v,
                        "type": "string",
                    }
                    for k, v in values.items()
                ]
            },
            "includeOtherFields": False,
            "options": {},
        },
        "id": uid(name),
        "name": name,
        "type": "n8n-nodes-base.set",
        "typeVersion": 3.4,
        "position": pos,
    }


def code_node(name, pos, js):
    return {
        "parameters": {"mode": "runOnceForAllItems", "jsCode": js},
        "id": uid(name),
        "name": name,
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": pos,
    }


def webhook_node(name, pos, path):
    return {
        "parameters": {
            "httpMethod": "POST",
            "path": path,
            "responseMode": "responseNode",
            "options": {},
        },
        "id": uid(name + ":" + path),
        "name": name,
        "type": "n8n-nodes-base.webhook",
        "typeVersion": 2,
        "position": pos,
        "webhookId": uid("hook:" + path),
    }


def respond_node(name, pos, body_expr):
    return {
        "parameters": {
            "respondWith": "json",
            "responseBody": body_expr,
            "options": {},
        },
        "id": uid(name),
        "name": name,
        "type": "n8n-nodes-base.respondToWebhook",
        "typeVersion": 1.1,
        "position": pos,
    }


def http_node(name, pos, url, method="GET", body_expr=None, headers=None,
              use_header_auth=False, on_error=None):
    params = {
        "method": method,
        "url": url,
        "options": {"timeout": 15000},
    }
    if use_header_auth:
        params["authentication"] = "genericCredentialType"
        params["genericAuthType"] = "httpHeaderAuth"
    if headers:
        params["sendHeaders"] = True
        params["specifyHeaders"] = "keypair"
        params["headerParameters"] = {
            "parameters": [{"name": k, "value": v} for k, v in headers.items()]
        }
    if body_expr is not None:
        params["sendBody"] = True
        params["specifyBody"] = "json"
        params["jsonBody"] = body_expr
    node = {
        "parameters": params,
        "id": uid(name),
        "name": name,
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": pos,
    }
    if use_header_auth:
        node["credentials"] = {
            "httpHeaderAuth": {
                "id": uid("cred:ghl"),
                "name": "GoHighLevel Private Integration Token",
            }
        }
    if on_error:
        node["onError"] = on_error
    return node


def chain(*names):
    """Linear main-connection chain."""
    conns = {}
    for a, b in zip(names, names[1:]):
        conns[a] = {"main": [[{"node": b, "type": "main", "index": 0}]]}
    return conns


def wrap(name, nodes, connections):
    return {
        "name": name,
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
        "pinData": {},
    }


# --------------------------------------------------------------------------
# Workflow 1 - inventory search
# --------------------------------------------------------------------------
def build_inventory():
    nodes = [
        webhook_node("Retell Webhook", [-240, 0], "nsjapan-inventory-search"),
        set_node("Config", [-20, 0], {"inventory_url": DEFAULT_INVENTORY_URL}),
        http_node(
            "Fetch Stock Snapshot", [200, 0],
            "={{ $json.inventory_url }}",
        ),
        code_node("Filter Stock", [420, 0], read_js("inventory_filter.js")),
        respond_node("Respond to Retell", [640, 0], "={{ JSON.stringify($json) }}"),
    ]
    conns = chain(
        "Retell Webhook", "Config", "Fetch Stock Snapshot",
        "Filter Stock", "Respond to Retell",
    )
    return wrap("NS Japan - Retell Inventory Search", nodes, conns)


# --------------------------------------------------------------------------
# Workflow 2 - lead capture into GoHighLevel
# --------------------------------------------------------------------------
EXTRACT_JS = """// Pull the contact id out of whichever shape GoHighLevel returned.
const res = $json || {};
const contact = res.contact || res.data || res;
const contactId = contact.id || contact._id || contact.contactId || null;

if (!contactId) {
  throw new Error(
    'GoHighLevel did not return a contact id. Response: ' +
    JSON.stringify(res).slice(0, 500)
  );
}

return [{ json: { contactId, ghlContact: contact } }];
"""

BUILD_RESPONSE_JS = """// Assemble what the voice agent hears back.
const normalized = $('Normalize Lead').first().json;

let contactId = null;
try {
  contactId = $('Extract Contact ID').first().json.contactId;
} catch (e) {
  contactId = null;
}

let opportunityId = null;
try {
  const opp = $('GHL Create Opportunity').first().json;
  opportunityId = (opp.opportunity && opp.opportunity.id) || opp.id || null;
} catch (e) {
  opportunityId = null;
}

const ok = Boolean(contactId);

return [
  {
    json: {
      ok,
      crm: 'GoHighLevel',
      contact_id: contactId,
      opportunity_id: opportunityId,
      lead: normalized.lead_summary,
      message: ok
        ? 'Lead saved to the CRM. Confirm to the caller that a specialist will email a ' +
          'full quote including shipping to their port, usually within one business day.'
        : 'The CRM did not confirm the save. Do not alarm the caller - tell them you ' +
          'have their details and the team will be in touch, and offer ' +
          'info@nsjapanautos.com as a backup.',
    },
  },
];
"""


def build_lead():
    base = GHL_API_BASE
    hdrs = {"Version": GHL_API_VERSION, "Content-Type": "application/json"}

    nodes = [
        webhook_node("Retell Webhook", [-460, 0], "nsjapan-lead"),
        set_node("Config", [-240, 0], {
            "ghl_location_id": "REPLACE_WITH_GHL_LOCATION_ID",
            "ghl_pipeline_id": "",
            "ghl_pipeline_stage_id": "",
        }),
        code_node("Normalize Lead", [-20, 0], read_js("lead_normalize.js")),
        http_node(
            "GHL Upsert Contact", [200, 0], base + "/contacts/upsert",
            method="POST", body_expr="={{ JSON.stringify($json.contact) }}",
            headers=hdrs, use_header_auth=True,
            on_error="continueRegularOutput",
        ),
        code_node("Extract Contact ID", [420, 0], EXTRACT_JS),
        http_node(
            "GHL Add Note", [640, 0],
            "={{ '" + base + "/contacts/' + $json.contactId + '/notes' }}",
            method="POST",
            body_expr="={{ JSON.stringify({ body: $('Normalize Lead').first().json.note }) }}",
            headers=hdrs, use_header_auth=True,
            on_error="continueRegularOutput",
        ),
        {
            "parameters": {
                "conditions": {
                    "options": {
                        "caseSensitive": True,
                        "leftValue": "",
                        "typeValidation": "loose",
                        "version": 2,
                    },
                    "conditions": [
                        {
                            "id": uid("cond:pipeline"),
                            "leftValue": "={{ $('Normalize Lead').first().json.hasPipeline }}",
                            "rightValue": True,
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                        }
                    ],
                    "combinator": "and",
                },
                "looseTypeValidation": True,
                "options": {},
            },
            "id": uid("Pipeline Configured?"),
            "name": "Pipeline Configured?",
            "type": "n8n-nodes-base.if",
            "typeVersion": 2.2,
            "position": [860, 0],
        },
        http_node(
            "GHL Create Opportunity", [1080, -110], base + "/opportunities/",
            method="POST",
            body_expr=(
                "={{ JSON.stringify(Object.assign({}, "
                "$('Normalize Lead').first().json.opportunity, "
                "{ contactId: $('Extract Contact ID').first().json.contactId })) }}"
            ),
            headers=hdrs, use_header_auth=True,
            on_error="continueRegularOutput",
        ),
        code_node("Build Response", [1300, 0], BUILD_RESPONSE_JS),
        respond_node("Respond to Retell", [1520, 0], "={{ JSON.stringify($json) }}"),
    ]

    conns = chain(
        "Retell Webhook", "Config", "Normalize Lead", "GHL Upsert Contact",
        "Extract Contact ID", "GHL Add Note", "Pipeline Configured?",
    )
    conns["Pipeline Configured?"] = {
        "main": [
            [{"node": "GHL Create Opportunity", "type": "main", "index": 0}],
            [{"node": "Build Response", "type": "main", "index": 0}],
        ]
    }
    conns["GHL Create Opportunity"] = {
        "main": [[{"node": "Build Response", "type": "main", "index": 0}]]
    }
    conns["Build Response"] = {
        "main": [[{"node": "Respond to Retell", "type": "main", "index": 0}]]
    }
    return wrap("NS Japan - Retell Lead to GoHighLevel", nodes, conns)


def main():
    os.makedirs(OUT, exist_ok=True)
    for fname, wf in [
        ("nsjapan-inventory-search.workflow.json", build_inventory()),
        ("nsjapan-lead-to-ghl.workflow.json", build_lead()),
    ]:
        path = os.path.join(OUT, fname)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(wf, fh, indent=2, ensure_ascii=False)
        print(f"wrote {path} ({len(wf['nodes'])} nodes)")


if __name__ == "__main__":
    main()
