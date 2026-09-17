# NS Japan Autos — Voice Agent

A Retell AI voice agent for **NS Japan Auto Ltd.** ([nsjapanautos.com](https://nsjapanautos.com)).
It answers customer questions about buying and exporting used Japanese vehicles, looks up
live stock, and captures qualified leads into GoHighLevel through n8n.

---

## What it does

**Answers questions** — the whole published FAQ, plus company details, office hours,
payment, FOB/C&F/CIF terms, shipping times and ports, booking requirements, documentation,
CAP tracking, country regulations and the glossary. 98 question-and-answer pairs from the
site, reorganised into eight knowledge-base documents, plus ten live site URLs that Retell
re-crawls daily.

**Looks up real stock** — `search_inventory` searches the actual vehicle list by make,
model, body type, budget, year, transmission, fuel, steering or stock number. The agent is
told never to describe a vehicle it did not get back from this lookup.

**Captures leads** — when a caller shows buying intent, the agent collects their name,
email, phone, destination country and port, what they want and their timeline, then
`capture_lead` creates or updates a GoHighLevel contact with tags, attaches a full call
note, and optionally opens an opportunity valued at their budget.

---

## Architecture

```
  Caller
    │
    ▼
  Retell AI agent  ── knowledge base (8 curated docs + 10 auto-refreshed site URLs)
    │
    ├── search_inventory ──► n8n webhook ──► data/inventory.json (this repo) ──► filtered results
    │
    └── capture_lead     ──► n8n webhook ──► GoHighLevel  contact + tags + note [+ opportunity]

  GitHub Action (daily) ──► scripts/refresh.py ──► re-crawls the site ──► commits data/
```

Two things keep the agent from going stale as the website changes:

- **Stock** is re-crawled nightly by a GitHub Action, which commits `data/inventory.json`.
  The n8n workflow reads that file on every call, so new stock reaches callers without a
  redeploy.
- **Filter options and FAQ wording** are read from the site rather than hardcoded.
  `data/facets.json` holds the makes, body types, fuels, transmissions and steering
  options the site currently offers, and the deploy script injects them into the agent's
  tool schema. The FAQ and About pages are attached to the Retell knowledge base as
  auto-refreshing URL sources.

---

## Repository layout

| Path | What it is |
|---|---|
| `agent/prompt.md` | The agent's system prompt — identity, voice style, guardrails, conversation flow |
| `agent/tools.json` | `search_inventory` and `capture_lead` schemas (enums refreshed from the live site at deploy time) |
| `agent/knowledge-base/` | Eight markdown documents uploaded to Retell as the knowledge base |
| `n8n/*.workflow.json` | Importable n8n workflows |
| `n8n/src/*.js` | The JavaScript that runs inside the n8n Code nodes, kept as real files so it can be tested |
| `data/inventory.json` | The live stock snapshot the agent searches |
| `data/facets.json` | The site's current search filter options |
| `scripts/` | Crawler, deploy, refresh and test scripts |
| `.github/workflows/refresh.yml` | Nightly re-crawl and commit |

---

## Setup

### 1. Install

```bash
pip install -r requirements.txt     # Python 3.9+
node --version                      # Node 18+ for the tests
cp .env.example .env                # then fill it in
```

### 2. Deploy the n8n workflows

```bash
python scripts/deploy_n8n.py
```

This imports both workflows, activates them, creates the GoHighLevel Header Auth
credential from `GHL_TOKEN`, and fills the **Config** nodes from `.env`
(`GHL_LOCATION_ID`, `GHL_PIPELINE_ID`, `GHL_PIPELINE_STAGE_ID`, `INVENTORY_URL`).
It matches on workflow name, so re-running updates in place instead of duplicating.

Leave `GHL_PIPELINE_ID` blank to create only the contact and a note, with no opportunity.

To do it by hand instead, use **Workflows → Import from File** on
`n8n/nsjapan-inventory-search.workflow.json` and `n8n/nsjapan-lead-to-ghl.workflow.json`,
then create a **Header Auth** credential named `GoHighLevel Private Integration Token`
(header `Authorization`, value `Bearer <token>`), attach it to the three GHL nodes, fill
the Config nodes, and activate both.

The agent expects the webhooks at:

```
{N8N_BASE_URL}/webhook/nsjapan-inventory-search
{N8N_BASE_URL}/webhook/nsjapan-lead
```

### 3. Deploy the agent

```bash
python scripts/deploy_retell.py
```

This creates the knowledge base, updates the Retell LLM with the prompt, tools and
knowledge base, and updates the agent settings. It is safe to re-run — it replaces the
knowledge base rather than duplicating it.

If `N8N_BASE_URL` is not set, it deploys the agent **without** the two custom tools and
says so, rather than shipping tools that would fail mid-call.

---

## Day-to-day

```bash
npm test                          # logic tests for both n8n Code nodes (65 checks)
npm run test:live                 # scripted conversations against the deployed agent
python scripts/refresh.py         # re-read stock and filter options from the website
python scripts/refresh.py --deploy  # ... and push the result to Retell
python scripts/build_n8n_workflows.py  # rebuild workflow JSON after editing n8n/src/*.js
python scripts/deploy_n8n.py           # push workflow changes into n8n
```

### Testing without n8n

`scripts/local_test_server.js` runs the exact same JavaScript as the n8n Code nodes, so
you can exercise the tool logic locally:

```bash
node scripts/local_test_server.js 8787
curl -X POST localhost:8787/webhook/nsjapan-inventory-search \
  -H 'Content-Type: application/json' \
  -d '{"name":"search_inventory","args":{"make":"TOYOTA","body_type":"SUV","price_max":5000}}'
```

Set `GHL_TOKEN` and `GHL_LOCATION_ID` in the environment to make it push to GoHighLevel
for real; otherwise the CRM call is simulated and logged.

### Live conversation tests

`scripts/test_agent_live.py` talks to a Retell **chat** agent bound to the same Retell LLM
as the voice agent — identical prompt, tools and knowledge base — so it exercises the real
configuration without placing calls.

```bash
python scripts/test_agent_live.py              # all suites
python scripts/test_agent_live.py qa -v        # one suite, with transcripts
```

Suites: `qa` (facts from the knowledge base), `guardrails` (refusals and things the agent
must never do), `lead` (the full capture flow), `tools` (stock lookup), `timezone` (the
office hours must never carry a timezone), `emotion` (exactly three valid tags per reply,
none banned, none on a number).

---

## Voice and emotion

The agent runs ElevenLabs `eleven_v3` on the custom "Siren" voice, with Retell's
**Expressive Mode** on. Retell accepts exactly ten emotion tags - the API rejects
anything else with a 400 listing the allowed values:

| Group | Tags |
|---|---|
| Feeling | `empathetic` `excited` `happy` `curious` `surprised` |
| Sound | `sigh` `clear throat` |
| Stress | `emphasis` |
| Timing | `pause` `long pause` |

All ten are enabled. `agent/prompt.md` governs taste: two or three tags per reply drawn
from the feeling and stress groups, `sigh` almost never, `clear throat` never, and the
timing tags at most once or twice a call because they slow delivery. Tags are never
placed on numbers, prices, stock numbers, emails or dates - those must come out plain,
because they are what the caller is writing down.

Tune without touching code, then re-run `python scripts/deploy_retell.py --skip-kb`:

```bash
VOICE_MODEL=eleven_v3        # or eleven_flash_v2_5 for lower latency
EXPRESSIVE_MODE=true
VOICE_SPEED=1.05             # 1.0 is the model's natural pace
EXPRESSIVE_TAGS=             # blank = all ten; or e.g. empathetic,curious,happy,excited,emphasis
```

The enabled set is deliberately the six that add **no silence**: `empathetic` `excited`
`happy` `curious` `surprised` `emphasis`. `pause`, `long pause`, `sigh` and `clear throat`
are switched off — they stall the call, and dead air on an international line reads as a
dropped connection, not thoughtfulness.

Every reply is required to carry **exactly three tags**, drawn from the Feeling and
Stress groups. That quota is enforced in two places, because Retell's own default
expressive guidance tells the model to use tags "sparingly - most lines have none",
which fights a fixed quota. `agent/expressive_prompt.md` is pushed to the agent's
`expressive_mode_prompt` field, which **replaces** that default; `agent/prompt.md`
states the same rule in the main prompt.

### Known conflict: expressive mode and the custom voice

Retell's API reference says of `enable_expressive_mode`: *"Only applicable for platform
voices."* This agent uses the custom ElevenLabs voice "Siren". The API accepts the
combination without complaint, and `eleven_v3` supports audio tags natively, so it may
work - but it is explicitly outside what Retell documents.

If a test call shows no emotion, there are two ways out:

1. **Keep Siren, drop Retell's tag set.** ElevenLabs v3 reads its own audio tags
   directly: `[excited]` `[happy]` `[curious]` `[surprised]` `[sighs]` `[whispers]`
   `[laughs]`. Note it does **not** know `[empathetic]`, `[emphasis]`, `[clear throat]`,
   `[pause]` or `[long pause]` - those are Retell's vocabulary, and an unrecognised tag
   risks being read aloud.
2. **Switch to a platform voice**, where expressive mode is supported. Closest matches
   to Siren: `retell-Willa` (British, middle aged), `retell-Marissa` or `retell-Sloane`
   (American, middle aged). Set `voice_id` and redeploy.

### The chat test harness mirrors the LLM, and must

Retell refuses to create a chat agent pinned above LLM version 0 — *"Cannot specify
version > 0 for new agent"* — and refuses to repoint one afterwards. The production LLM
is on version 4+, so a chat agent bound straight to it silently runs **the very first
prompt ever deployed**. That trap made several test runs meaningless before it was
caught: the tests kept reporting old behaviour while the voice agent ran something else.

`ensure_chat_agent()` therefore copies the live prompt, tools and knowledge base into a
throwaway LLM, where version 0 *is* the current prompt, and binds the chat agent to that.
Run `python scripts/cleanup_test_agents.py --yes` afterwards to remove the leftovers; it
refuses to touch the production agent, LLM or knowledge base.

**Verifying tone still needs a real call.** Expressive Mode is a text-to-speech feature and
exists only on voice agents - chat agents silently drop `enable_expressive_mode`, so the
scripted conversation tests cannot observe tags. Confirm delivery with one test call from
the Retell dashboard.

---

## Notes on the source data

**Mileage is published in thousands.** The website prints a Hiace's mileage as `596 km`
where it means 596,000 km — prices across the stock list confirm this reading. The crawler
keeps the published figure as `mileage_published` and exposes the real value as
`mileage_km`, and the agent is told to speak it as an approximate round number and to say
the sales team confirms exact mileage on the quote.

**One vehicle is unreachable.** The site reports 208 vehicles; the pager exposes 207. The
refresh job reports the difference rather than hiding it.

**The stock lookup fails safe.** If the snapshot host is unreachable, the workflow
returns no vehicles and an explicit instruction not to name any vehicle, price or stock
number. The agent then takes the caller's details instead of improvising. This is covered
by tests and was verified against the live agent.

**The n8n credential must use a bare domain.** The GoHighLevel Header Auth credential
restricts the token to `services.leadconnectorhq.com`. n8n wants the bare host there -
writing `https://services.leadconnectorhq.com` silently blocks every request and the node
reports an empty `{"error":{"level":"error"}}` with no status code. `scripts/deploy_n8n.py`
sets this correctly; if you create the credential by hand in the UI, leave the scheme off.

**FOB only.** Every price in the stock list is the vehicle price alone. The agent states
FOB prices but is forbidden from quoting a landed or delivered total — freight, insurance,
duties and city delivery are quoted by the sales team.

---

## Security

`.env` is gitignored and holds every credential. Nothing in this repository contains a
live key. The GitHub Action reads Retell credentials from repository secrets
(`RETELL_API_KEY`, `RETELL_AGENT_ID`, `RETELL_LLM_ID`, `N8N_BASE_URL`), and the GoHighLevel
token lives only in the n8n credential store.

If any key in this project has been shared in plain text, rotate it:
Retell → dashboard API keys; n8n → Settings → API; GoHighLevel → Settings → Private
Integrations.
