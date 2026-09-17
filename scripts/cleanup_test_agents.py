"""
Remove the throwaway chat agents and mirror LLMs the live test harness creates.

The harness has to mirror the production LLM (see ensure_chat_agent in
test_agent_live.py), which leaves objects behind in the Retell account. This clears
them without touching the production agent, LLM or knowledge base.

    python scripts/cleanup_test_agents.py          # list what would be removed
    python scripts/cleanup_test_agents.py --yes    # actually remove it
"""
import json
import os
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
API = "https://api.retellai.com"
STATE_FILE = os.path.join(ROOT, "agent", ".deploy-state.json")
MARKER = "TEST HARNESS"


def env(key):
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    return os.environ.get(key, "")


def main():
    apply = "--yes" in sys.argv
    headers = {"Authorization": "Bearer " + env("RETELL_API_KEY")}
    prod_llm = env("RETELL_LLM_ID")
    prod_agent = env("RETELL_AGENT_ID")

    state = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as fh:
            state = json.load(fh)

    # 1. chat agents created by the harness
    chat_agents = []
    r = requests.get(API + "/list-chat-agents", headers=headers, timeout=60)
    if r.ok:
        chat_agents = [a for a in r.json() if MARKER in (a.get("agent_name") or "")]

    # 2. the mirror LLMs behind them, plus any orphaned ones
    mirror_ids = {a["response_engine"]["llm_id"] for a in chat_agents
                  if a.get("response_engine", {}).get("llm_id")}
    if state.get("test_mirror_llm_id"):
        mirror_ids.add(state["test_mirror_llm_id"])
    mirror_ids.discard(prod_llm)

    print(f"chat agents to remove: {len(chat_agents)}")
    for a in chat_agents:
        print(f"   {a['agent_id']}  {a.get('agent_name')}")
    print(f"mirror LLMs to remove: {len(mirror_ids)}")
    for m in sorted(mirror_ids):
        print(f"   {m}")

    if prod_llm in mirror_ids or prod_agent in {a["agent_id"] for a in chat_agents}:
        raise SystemExit("refusing to run: production ids appeared in the removal list")

    if not apply:
        print("\nDry run. Re-run with --yes to delete.")
        return 0

    for a in chat_agents:
        d = requests.delete(API + "/delete-chat-agent/" + a["agent_id"],
                            headers=headers, timeout=60)
        print(f"deleted chat agent {a['agent_id']}: {d.status_code}")
    for m in sorted(mirror_ids):
        d = requests.delete(API + "/delete-retell-llm/" + m, headers=headers, timeout=60)
        print(f"deleted mirror llm {m}: {d.status_code}")

    for k in ("test_chat_agent_id", "test_mirror_llm_id", "test_mirror_prompt_len"):
        state.pop(k, None)
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    print("\ncleaned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
