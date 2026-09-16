"""
Import (or update) the NS Japan workflows in n8n and activate them.

Idempotent: matches on workflow name, updates in place if it already exists, so
re-running never leaves duplicates behind.

    python scripts/deploy_n8n.py                 # import + activate both
    python scripts/deploy_n8n.py --no-activate   # import only
    python scripts/deploy_n8n.py --list          # show what is there now
"""
import argparse
import json
import os
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

STATE_FILE = os.path.join(ROOT, "agent", ".deploy-state.json")

WORKFLOWS = [
    "nsjapan-inventory-search.workflow.json",
    "nsjapan-lead-to-ghl.workflow.json",
]


def load_env():
    env = {}
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    for k in ("N8N_BASE_URL", "N8N_API_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


class N8n:
    def __init__(self, base, key):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"X-N8N-API-KEY": key, "Accept": "application/json"})

    def _check(self, r, what):
        if not r.ok:
            raise SystemExit(f"{what} failed: HTTP {r.status_code}\n{r.text[:1200]}")
        return r.json() if r.text else {}

    def list(self):
        out, cursor = [], None
        while True:
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            data = self._check(
                self.s.get(self.base + "/api/v1/workflows", params=params, timeout=60),
                "list workflows")
            out.extend(data.get("data", []))
            cursor = data.get("nextCursor")
            if not cursor:
                return out

    def create(self, payload):
        return self._check(
            self.s.post(self.base + "/api/v1/workflows", json=payload, timeout=120),
            "create workflow")

    def update(self, wf_id, payload):
        return self._check(
            self.s.put(self.base + f"/api/v1/workflows/{wf_id}", json=payload, timeout=120),
            "update workflow")

    def activate(self, wf_id):
        r = self.s.post(self.base + f"/api/v1/workflows/{wf_id}/activate", timeout=60)
        return r.ok, r.text[:400]

    def create_credential(self, name, cred_type, data):
        r = self.s.post(self.base + "/api/v1/credentials",
                        json={"name": name, "type": cred_type, "data": data},
                        timeout=60)
        if not r.ok:
            raise SystemExit(f"create credential failed: HTTP {r.status_code}\n"
                             f"{r.text[:800]}")
        return r.json()["id"]


GHL_CRED_NAME = "GoHighLevel Private Integration Token"


def ensure_ghl_credential(api, env, state):
    """
    Create the Header Auth credential the GHL nodes use, if a token is configured.

    n8n's public API cannot list credentials, so the id is remembered in
    agent/.deploy-state.json and reused on later runs.
    """
    token = env.get("GHL_TOKEN", "").strip()
    if not token:
        return None
    if state.get("n8n_ghl_credential_id"):
        return state["n8n_ghl_credential_id"]

    value = token if token.lower().startswith("bearer ") else "Bearer " + token
    cred_id = api.create_credential(
        GHL_CRED_NAME, "httpHeaderAuth",
        {
            "name": "Authorization",
            "value": value,
            # Scope the token to GoHighLevel so it can never be sent anywhere else,
            # even if a node in this instance is later pointed at another host.
            "allowedHttpRequestDomains": "domains",
            "allowedDomains": "services.leadconnectorhq.com",
        },
    )
    state["n8n_ghl_credential_id"] = cred_id
    print(f"created n8n credential '{GHL_CRED_NAME}' ({cred_id})")
    return cred_id


def apply_config(payload, env, cred_id):
    """Point the workflow at the configured GHL location and credential."""
    for node in payload["nodes"]:
        if cred_id and node.get("credentials", {}).get("httpHeaderAuth"):
            node["credentials"]["httpHeaderAuth"] = {
                "id": cred_id, "name": GHL_CRED_NAME,
            }
        if node["name"] != "Config":
            continue
        for a in node["parameters"]["assignments"]["assignments"]:
            override = {
                "ghl_location_id": env.get("GHL_LOCATION_ID", ""),
                "ghl_pipeline_id": env.get("GHL_PIPELINE_ID", ""),
                "ghl_pipeline_stage_id": env.get("GHL_PIPELINE_STAGE_ID", ""),
                "inventory_url": env.get("INVENTORY_URL", ""),
            }.get(a["name"])
            if override:
                a["value"] = override
    return payload


def payload_for(path):
    """The n8n public API only accepts these four keys on write."""
    with open(path, encoding="utf-8") as fh:
        wf = json.load(fh)
    return {
        "name": wf["name"],
        "nodes": wf["nodes"],
        "connections": wf["connections"],
        "settings": wf.get("settings", {"executionOrder": "v1"}),
    }


def webhook_paths(payload):
    return [n["parameters"]["path"] for n in payload["nodes"]
            if n["type"] == "n8n-nodes-base.webhook"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-activate", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    env = load_env()
    for k in ("N8N_BASE_URL", "N8N_API_KEY"):
        if not env.get(k):
            raise SystemExit(f"{k} missing from .env")

    api = N8n(env["N8N_BASE_URL"], env["N8N_API_KEY"])

    state = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as fh:
            state = json.load(fh)

    existing = {w["name"]: w for w in api.list()}

    if args.list:
        for name, w in sorted(existing.items()):
            print(f"  {w['id']}  active={str(w['active']):5}  {name}")
        return 0

    cred_id = ensure_ghl_credential(api, env, state)
    if not cred_id and not env.get("GHL_TOKEN"):
        print("note: GHL_TOKEN is not set, so the lead workflow's GoHighLevel nodes")
        print("      have no credential yet and lead capture will fail until one is")
        print("      attached in the n8n UI (or GHL_TOKEN is set and this is re-run).")

    base = env["N8N_BASE_URL"].rstrip("/")
    for fname in WORKFLOWS:
        path = os.path.join(ROOT, "n8n", fname)
        payload = apply_config(payload_for(path), env, cred_id)
        name = payload["name"]

        if name in existing:
            wf_id = existing[name]["id"]
            api.update(wf_id, payload)
            print(f"updated  {name}  ({wf_id})")
        else:
            created = api.create(payload)
            wf_id = created["id"]
            print(f"created  {name}  ({wf_id})")

        if not args.no_activate:
            ok, msg = api.activate(wf_id)
            print(f"         active: {ok}" + ("" if ok else f"  -> {msg}"))

        for p in webhook_paths(payload):
            print(f"         webhook: {base}/webhook/{p}")

    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)

    if cred_id and env.get("GHL_LOCATION_ID"):
        print("\nGoHighLevel is wired: credential attached and location id set.")
    else:
        print("\nStill to do before leads will save: set GHL_TOKEN and GHL_LOCATION_ID")
        print("in .env, then re-run this script.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
