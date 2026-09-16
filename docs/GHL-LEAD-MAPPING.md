# What a captured lead looks like in GoHighLevel

Generated from `n8n/src/lead_normalize.js` with a representative call, so this page
cannot drift from the code. Regenerate with:

```bash
node scripts/sample_lead.js
```

## 1. Contact upsert

`POST https://services.leadconnectorhq.com/contacts/upsert`

Headers: `Authorization: Bearer <private integration token>`, `Version: 2021-07-28`

```json
{
  "locationId": "<YOUR_LOCATION_ID>",
  "firstName": "James",
  "lastName": "Mwangi Kariuki",
  "name": "James Mwangi Kariuki",
  "source": "Retell Voice Agent",
  "tags": [
    "nsjapan-voice-lead",
    "source-retell",
    "interest-vehicle-in-stock",
    "country-kenya",
    "budget-6k-10k",
    "stock-ns10632"
  ],
  "email": "james.mwangi@example.com",
  "phone": "+254712345678",
  "country": "KE",
  "city": "Mombasa"
}
```

Upsert matches on email or phone, so a repeat caller updates their existing contact
instead of creating a duplicate.

## 2. Note attached to the contact

`POST /contacts/{contactId}/notes`

```
NS JAPAN AUTOS - VOICE AGENT LEAD
Captured: 2026-09-16 12:51 UTC

Name: James Mwangi Kariuki
Email: james.mwangi@example.com
Phone: +254712345678
Destination country: Kenya
Nearest port / city: Mombasa

Interest: vehicle_in_stock
Looking for: 2010 Toyota Alphard, automatic, around 6000 USD
Stock number: NS10632
Budget (USD): 6,500
Timeline: within a month

Agent notes: Buying for a family transport business. Asked about JEVIC inspection and CIF to Mombasa.

Retell call ID: call_9f2b
Caller ID: +254700111222
```

## 3. Opportunity

`POST /opportunities/`

Only created when `ghl_pipeline_id` is set in the workflow's **Config** node. Leave it
blank and the workflow creates just the contact and the note.

```json
{
  "pipelineId": "<PIPELINE_ID>",
  "locationId": "<YOUR_LOCATION_ID>",
  "name": "James Mwangi Kariuki - 2010 Toyota Alphard, automatic, around 6000 USD",
  "status": "open",
  "source": "Retell Voice Agent",
  "pipelineStageId": "<STAGE_ID>",
  "monetaryValue": 6500
}
```

## Tags applied

- `nsjapan-voice-lead`
- `source-retell`
- `interest-vehicle-in-stock`
- `country-kenya`
- `budget-6k-10k`
- `stock-ns10632`

Tags are what make this useful in GoHighLevel: filter smart lists by
`nsjapan-voice-lead`, route by `interest-*`, prioritise by `budget-*`, and segment by
`country-*`. Leads carrying `no-email-captured` need a phone follow-up.

## Data quality rules applied on the way in

- The name is split into first and last; a single-word name does not break it.
- The email is lowercased, and spoken forms like `name (at) gmail (dot) com` are
  repaired. An address that still does not look valid is **dropped rather than sent**,
  and the contact is tagged `no-email-captured`.
- The phone is reduced to digits and a leading `+`; if the caller gave no number, the
  caller ID is used instead.
- The destination country is mapped to the ISO-2 code GoHighLevel expects. A country
  that is not in the map is left off the contact rather than guessed, but still appears
  as a `country-*` tag and in the note.
- The opportunity name is truncated to 120 characters.
