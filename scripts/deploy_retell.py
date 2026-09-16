"""
Push the NS Japan Autos agent configuration into Retell.

Does three things, idempotently:
  1. Creates (or replaces) the knowledge base from agent/knowledge-base/*.md
  2. Updates the Retell LLM with the prompt, the tools and the knowledge base
  3. Updates the agent settings (name, timezone, voice behaviour)

Usage:
    python scripts/deploy_retell.py              # full deploy
    python scripts/deploy_retell.py --skip-kb    # prompt + tools only, reuse the KB
    python scripts/deploy_retell.py --dry-run    # show what would be sent
"""
import argparse
import glob
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
API = "https://api.retellai.com"
KB_NAME = "NS Japan Autos Knowledge"

# Live pages Retell re-crawls on a daily auto-refresh. The curated markdown in
# agent/knowledge-base gives the agent clean, well-structured answers; these keep it
# honest when the site itself changes.
KB_URLS = [
    "https://nsjapanautos.com/about/",
    "https://nsjapanautos.com/contact/",
    "https://nsjapanautos.com/faq-gq/",
    "https://nsjapanautos.com/faq-i/",
    "https://nsjapanautos.com/faq-bp/",
    "https://nsjapanautos.com/faq-bs/",
    "https://nsjapanautos.com/faq-d/",
    "https://nsjapanautos.com/faq-sr/",
    "https://nsjapanautos.com/faq-cr/",
    "https://nsjapanautos.com/faq-gt/",
]
STATE_FILE = os.path.join(ROOT, "agent", ".deploy-state.json")


def load_env():
    env = {}
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    for k, v in os.environ.items():
        if k.startswith(("RETELL_", "N8N_", "GHL_", "INVENTORY_")) and v:
            env[k] = v
    return env


def read_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def write_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


class Retell:
    def __init__(self, api_key):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": "Bearer " + api_key})

    def _check(self, r, what):
        if not r.ok:
            raise SystemExit(f"{what} failed: HTTP {r.status_code}\n{r.text[:1500]}")
        return r.json() if r.text else {}

    def list_kbs(self):
        return self._check(self.s.get(API + "/list-knowledge-bases", timeout=60),
                           "list knowledge bases")

    def delete_kb(self, kb_id):
        r = self.s.delete(API + "/delete-knowledge-base/" + kb_id, timeout=60)
        return r.ok

    def create_kb(self, name, texts, urls=None):
        # This endpoint is multipart/form-data only, and each array has to arrive as a
        # single JSON-encoded field - repeated fields return a 500.
        files = [("knowledge_base_texts", (None, json.dumps(texts)))]
        if urls:
            files.append(("knowledge_base_urls", (None, json.dumps(urls))))
        r = self.s.post(
            API + "/create-knowledge-base",
            data={
                "knowledge_base_name": name,
                # Retell re-crawls the URL sources daily, so wording changes on the
                # live site reach the agent without a redeploy.
                "enable_auto_refresh": "true" if urls else "false",
            },
            files=files,
            timeout=300,
        )
        return self._check(r, "create knowledge base")

    def get_kb(self, kb_id):
        return self._check(self.s.get(API + "/get-knowledge-base/" + kb_id, timeout=60),
                           "get knowledge base")

    def update_llm(self, llm_id, payload):
        r = self.s.patch(API + "/update-retell-llm/" + llm_id, json=payload, timeout=120)
        return self._check(r, "update LLM")

    def get_agent(self, agent_id):
        return self._check(self.s.get(API + "/get-agent/" + agent_id, timeout=60), "get agent")

    def update_agent(self, agent_id, payload):
        r = self.s.patch(API + "/update-agent/" + agent_id, json=payload, timeout=120)
        return self._check(r, "update agent")


def build_kb_texts():
    texts = []
    for path in sorted(glob.glob(os.path.join(ROOT, "agent", "knowledge-base", "*.md"))):
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        title = os.path.splitext(os.path.basename(path))[0]
        # "02-buying-and-payment" -> "Buying And Payment"
        title = " ".join(w.capitalize() for w in title.split("-")[1:])
        texts.append({"title": title, "text": content})
    return texts


def apply_live_facets(tools):
    """
    Replace the hand-written enums in search_inventory with whatever the site is
    actually offering today, so a new make or body type does not need a code change.
    """
    facets_path = os.path.join(ROOT, "data", "facets.json")
    if not os.path.exists(facets_path):
        print("      (no data/facets.json - keeping the enums in agent/tools.json)")
        return tools
    with open(facets_path, encoding="utf-8") as fh:
        facets = json.load(fh)

    # Only advertise makes we currently hold stock in; the site lists 51 makes but
    # most have zero vehicles, and a long enum just invites the model to guess.
    in_stock_makes = []
    inv_path = os.path.join(ROOT, "data", "inventory.json")
    if os.path.exists(inv_path):
        with open(inv_path, encoding="utf-8") as fh:
            inv = json.load(fh)
        seen = {}
        for v in inv.get("vehicles", []):
            mk = (v.get("make") or "").strip()
            if mk:
                seen[mk] = seen.get(mk, 0) + 1
        in_stock_makes = [m for m, _ in sorted(seen.items(), key=lambda kv: -kv[1])]

    for tool in tools:
        if tool.get("name") != "search_inventory":
            continue
        props = tool["parameters"]["properties"]
        if facets.get("body_types"):
            props["body_type"]["enum"] = facets["body_types"]
        if facets.get("transmissions"):
            props["transmission"]["enum"] = facets["transmissions"]
        if facets.get("fuels"):
            # The stock grid spells hybrids "Petrol-Hybrid" while the filter says
            # "Hybrid"; accept both so neither phrasing misses.
            fuels = list(facets["fuels"])
            if "Hybrid" in fuels and "Petrol-Hybrid" not in fuels:
                fuels.append("Petrol-Hybrid")
            props["fuel"]["enum"] = fuels
        if facets.get("steering"):
            props["steering"]["enum"] = facets["steering"]
        if in_stock_makes:
            props["make"]["description"] = (
                "Vehicle manufacturer. Makes currently in stock: "
                + ", ".join(in_stock_makes)
                + ". Other makes can be requested for sourcing. Omit if the caller "
                  "did not name a make."
            )
        print(f"      enums refreshed from the live site "
              f"({len(facets.get('body_types', []))} body types, "
              f"{len(in_stock_makes)} makes in stock)")
    return tools


def build_tools(n8n_base):
    with open(os.path.join(ROOT, "agent", "tools.json"), encoding="utf-8") as fh:
        raw = fh.read()
    if not n8n_base:
        # Without a reachable webhook the custom tools would fail mid-call, which is
        # worse than not offering them at all. Ship the built-ins only, and say so loudly.
        tools = [
            t for t in json.loads(raw.replace("{{N8N_BASE_URL}}", "https://unset"))
            if t.get("type") != "custom"
        ]
        print("\n  !! N8N_BASE_URL is not set in .env.")
        print("     Deploying WITHOUT search_inventory and capture_lead.")
        print("     The agent will answer questions but cannot look up stock or save leads.")
        print("     Set N8N_BASE_URL and re-run to enable them.\n")
        return apply_live_facets(tools)
    raw = raw.replace("{{N8N_BASE_URL}}", n8n_base.rstrip("/"))
    return apply_live_facets(json.loads(raw))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-kb", action="store_true",
                    help="reuse the knowledge base recorded in agent/.deploy-state.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    env = load_env()
    for required in ("RETELL_API_KEY", "RETELL_AGENT_ID", "RETELL_LLM_ID"):
        if not env.get(required):
            raise SystemExit(f"{required} missing from .env")

    state = read_state()
    api = Retell(env["RETELL_API_KEY"])

    with open(os.path.join(ROOT, "agent", "prompt.md"), encoding="utf-8") as fh:
        prompt = fh.read()
    tools = build_tools(env.get("N8N_BASE_URL", ""))
    kb_texts = build_kb_texts()

    print(f"prompt: {len(prompt)} chars")
    print(f"tools:  {', '.join(t['name'] for t in tools)}")
    print(f"kb:     {len(kb_texts)} documents, "
          f"{sum(len(t['text']) for t in kb_texts)} chars "
          f"+ {len(KB_URLS)} auto-refreshing site URLs")
    for t in tools:
        if t.get("url"):
            print(f"        {t['name']} -> {t['url']}")

    if args.dry_run:
        print("\n[dry run] nothing sent")
        return 0

    # ---------------------------------------------------------------- 1. KB
    kb_id = state.get("knowledge_base_id")
    if args.skip_kb and kb_id:
        print(f"\n[1/3] reusing knowledge base {kb_id}")
    else:
        print("\n[1/3] creating knowledge base ...")
        existing = api.list_kbs()
        for kb in existing if isinstance(existing, list) else []:
            if kb.get("knowledge_base_name") == KB_NAME:
                print(f"      removing previous '{KB_NAME}' ({kb['knowledge_base_id']})")
                api.delete_kb(kb["knowledge_base_id"])

        kb = api.create_kb(KB_NAME, kb_texts, KB_URLS)
        kb_id = kb["knowledge_base_id"]
        print(f"      created {kb_id}, status={kb.get('status')}")

        for _ in range(60):
            status = api.get_kb(kb_id).get("status")
            if status == "complete":
                print("      indexing complete")
                break
            if status == "error":
                raise SystemExit("knowledge base indexing failed")
            time.sleep(3)
        else:
            print("      WARNING: still indexing; the agent will pick it up when ready")

        state["knowledge_base_id"] = kb_id
        write_state(state)

    # ---------------------------------------------------------------- 2. LLM
    print("\n[2/3] updating the response engine ...")
    llm_payload = {
        "model": "gpt-5.6-terra",
        "model_temperature": 0.25,
        "general_prompt": prompt,
        "general_tools": tools,
        "knowledge_base_ids": [kb_id],
        "begin_message": (
            "Thank you for calling NS Japan Autos, this is Sara. How can I help you today?"
        ),
        "start_speaker": "agent",
        "tool_call_strict_mode": False,
        "kb_config": {"filter_score": 0.5, "top_k": 5},
    }
    llm = api.update_llm(env["RETELL_LLM_ID"], llm_payload)
    print(f"      llm {llm.get('llm_id')} updated, model={llm.get('model')}, "
          f"tools={len(llm.get('general_tools', []))}, "
          f"kb={llm.get('knowledge_base_ids')}")

    # ---------------------------------------------------------------- 3. Agent
    print("\n[3/3] updating agent settings ...")
    agent_payload = {
        "agent_name": "NS Japan Autos - Sara (Sales & Support)",
        "language": "en-US",
        "timezone": "Asia/Tokyo",
        "interruption_sensitivity": 0.9,
        "responsiveness": 1,
        "enable_backchannel": True,
        "backchannel_frequency": 0.7,
        "backchannel_words": ["mm-hmm", "I see", "right", "okay"],
        "reminder_trigger_ms": 12000,
        "reminder_max_count": 2,
        "max_call_duration_ms": 1800000,
        "end_call_after_silence_ms": 30000,
        "normalize_for_speech": True,
        "voicemail_option": {
            "action": {
                "type": "static_text",
                "text": (
                    "Hello, this is Sara from NS Japan Autos. Sorry we missed you. "
                    "Please call us back, or email info@nsjapanautos.com and our team "
                    "will help you with your vehicle enquiry. Thank you."
                ),
            }
        },
        "post_call_analysis_data": [
            {"type": "string", "name": "customer_name",
             "description": "The caller's full name, if they gave one.",
             "examples": ["James Mwangi"]},
            {"type": "string", "name": "customer_email",
             "description": "The caller's email address, if they gave one.",
             "examples": ["james@example.com"]},
            {"type": "string", "name": "destination_country",
             "description": "Country the caller wants the vehicle shipped to.",
             "examples": ["Kenya", "Zambia"]},
            {"type": "string", "name": "vehicle_interest",
             "description": "What vehicle or part the caller was interested in.",
             "examples": ["2010 Toyota Alphard"]},
            {"type": "number", "name": "budget_usd",
             "description": "Budget in US dollars the caller stated, if any."},
            {"type": "boolean", "name": "lead_captured",
             "description": "Whether the agent successfully submitted the lead to the CRM."},
            {"type": "enum", "name": "call_outcome",
             "description": "How the call ended.",
             "choices": ["lead_captured", "question_answered", "sourcing_request",
                         "existing_order_support", "no_interest", "wrong_number"]},
        ],
    }
    agent = api.update_agent(env["RETELL_AGENT_ID"], agent_payload)
    print(f"      agent {agent.get('agent_id')} updated: {agent.get('agent_name')}")
    print(f"      timezone={agent.get('timezone')} voice={agent.get('voice_id')}")

    state["last_deploy"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state["agent_id"] = env["RETELL_AGENT_ID"]
    state["llm_id"] = env["RETELL_LLM_ID"]
    write_state(state)

    print("\nDone. Open the agent in the Retell dashboard to test:")
    print(f"  https://dashboard.retellai.com/agents/{env['RETELL_AGENT_ID']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
