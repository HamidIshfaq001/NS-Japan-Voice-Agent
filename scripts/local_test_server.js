/**
 * Local stand-in for the two n8n webhooks, used to live-test the Retell agent before
 * (or without) n8n being reachable.
 *
 * It executes the SAME JavaScript that the n8n Code nodes run - n8n/src/*.js - against
 * a stubbed n8n runtime, so what you test here is what n8n will do.
 *
 * Routes (both accept the n8n path style and a bare style):
 *   POST /webhook/nsjapan-inventory-search
 *   POST /webhook/nsjapan-lead
 *   GET  /health
 *   GET  /calls              - everything Retell has sent this session
 *
 * If GHL_TOKEN and GHL_LOCATION_ID are set in the environment, leads are pushed to
 * GoHighLevel for real. Otherwise the CRM call is simulated and clearly logged as such.
 *
 *   node scripts/local_test_server.js [port]
 */
const fs = require('fs');
const http = require('http');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const PORT = Number(process.argv[2]) || 8787;

const GHL_TOKEN = process.env.GHL_TOKEN || '';
const GHL_LOCATION_ID = process.env.GHL_LOCATION_ID || '';
const GHL_PIPELINE_ID = process.env.GHL_PIPELINE_ID || '';
const GHL_PIPELINE_STAGE_ID = process.env.GHL_PIPELINE_STAGE_ID || '';
const GHL_BASE = 'https://services.leadconnectorhq.com';
const GHL_VERSION = '2021-07-28';

const snapshot = JSON.parse(
  fs.readFileSync(path.join(ROOT, 'data', 'inventory.json'), 'utf8')
);
const filterJs = fs.readFileSync(path.join(ROOT, 'n8n', 'src', 'inventory_filter.js'), 'utf8');
const leadJs = fs.readFileSync(path.join(ROOT, 'n8n', 'src', 'lead_normalize.js'), 'utf8');

const log = [];

function runN8nCode(code, nodes) {
  const $ = (name) => {
    if (!nodes[name]) throw new Error('unknown n8n node: ' + name);
    return { first: () => nodes[name] };
  };
  const sandbox = { $, console, JSON, Number, Math, String, Array, Object, Boolean, Date };
  return vm.runInNewContext(`(function(){${code}})()`, sandbox);
}

function handleInventory(body) {
  const out = runN8nCode(filterJs, {
    'Retell Webhook': { json: { body } },
    'Fetch Stock Snapshot': { json: snapshot },
  });
  return out[0].json;
}

async function ghl(endpoint, payload) {
  const res = await fetch(GHL_BASE + endpoint, {
    method: 'POST',
    headers: {
      Authorization: 'Bearer ' + GHL_TOKEN,
      Version: GHL_VERSION,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });
  const text = await res.text();
  let json;
  try {
    json = JSON.parse(text);
  } catch (e) {
    json = { raw: text };
  }
  return { status: res.status, json };
}

async function handleLead(body) {
  const normalized = runN8nCode(leadJs, {
    'Retell Webhook': { json: { body } },
    Config: {
      json: {
        ghl_location_id: GHL_LOCATION_ID || 'loc_SIMULATED',
        ghl_pipeline_id: GHL_PIPELINE_ID,
        ghl_pipeline_stage_id: GHL_PIPELINE_STAGE_ID,
      },
    },
  })[0].json;

  console.log('\n----- NORMALISED LEAD -----');
  console.log(JSON.stringify(normalized.contact, null, 2));
  console.log('----- CRM NOTE -----');
  console.log(normalized.note);
  console.log('---------------------------\n');

  if (!GHL_TOKEN || !GHL_LOCATION_ID) {
    return {
      ok: true,
      crm: 'GoHighLevel (SIMULATED - no credentials configured on this test server)',
      contact_id: 'simulated_' + Date.now(),
      opportunity_id: null,
      lead: normalized.lead_summary,
      message:
        'Lead saved to the CRM. Confirm to the caller that a specialist will email a ' +
        'full quote including shipping to their port, usually within one business day.',
    };
  }

  const contactRes = await ghl('/contacts/upsert', normalized.contact);
  const c = contactRes.json.contact || contactRes.json;
  const contactId = c.id || c._id || null;
  console.log('GHL upsert ->', contactRes.status, 'contactId:', contactId);

  let opportunityId = null;
  if (contactId) {
    await ghl(`/contacts/${contactId}/notes`, { body: normalized.note });
    if (normalized.hasPipeline) {
      const oppRes = await ghl('/opportunities/',
        Object.assign({}, normalized.opportunity, { contactId }));
      opportunityId = (oppRes.json.opportunity && oppRes.json.opportunity.id) || oppRes.json.id || null;
    }
  }

  const ok = Boolean(contactId);
  return {
    ok,
    crm: 'GoHighLevel',
    contact_id: contactId,
    opportunity_id: opportunityId,
    lead: normalized.lead_summary,
    message: ok
      ? 'Lead saved to the CRM. Confirm to the caller that a specialist will email a ' +
        'full quote including shipping to their port, usually within one business day.'
      : 'The CRM did not confirm the save. Do not alarm the caller - tell them you have ' +
        'their details and the team will be in touch, and offer info@nsjapanautos.com ' +
        'as a backup.',
  };
}

const server = http.createServer((req, res) => {
  const url = req.url.split('?')[0];
  const send = (code, obj) => {
    const payload = JSON.stringify(obj);
    res.writeHead(code, {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(payload),
    });
    res.end(payload);
  };

  if (req.method === 'GET' && url === '/health') {
    return send(200, { ok: true, vehicles: snapshot.count, crawled_at: snapshot.crawled_at });
  }
  if (req.method === 'GET' && url === '/calls') {
    return send(200, { count: log.length, calls: log });
  }

  let raw = '';
  req.on('data', (c) => {
    raw += c;
  });
  req.on('end', async () => {
    let body = {};
    try {
      body = raw ? JSON.parse(raw) : {};
    } catch (e) {
      body = { _unparsed: raw };
    }

    const entry = {
      at: new Date().toISOString(),
      url,
      function: body.name || null,
      args: body.args || null,
      call_id: (body.call && body.call.call_id) || null,
    };
    console.log(`\n>>> ${req.method} ${url}  fn=${entry.function}`);
    console.log('    args:', JSON.stringify(entry.args));

    try {
      let out;
      if (url.includes('inventory-search')) {
        out = handleInventory(body);
        console.log(`    <- ${out.total_matches} matches, returning ${out.returned}`);
      } else if (url.includes('lead')) {
        out = await handleLead(body);
        console.log(`    <- ok=${out.ok} contact=${out.contact_id}`);
      } else {
        entry.response = { error: 'no such route' };
        log.push(entry);
        return send(404, { ok: false, error: 'no such route: ' + url });
      }
      entry.response = out;
      log.push(entry);
      return send(200, out);
    } catch (err) {
      console.error('    !! handler error:', err.message);
      entry.response = { error: err.message };
      log.push(entry);
      return send(200, {
        ok: false,
        error: err.message,
        message:
          'Something went wrong saving this. Do not alarm the caller - tell them the ' +
          'team will follow up and offer info@nsjapanautos.com.',
      });
    }
  });
});

server.listen(PORT, () => {
  console.log(`NS Japan test webhook server on http://127.0.0.1:${PORT}`);
  console.log(`  stock snapshot: ${snapshot.count} vehicles (${snapshot.crawled_at})`);
  console.log(`  GoHighLevel: ${GHL_TOKEN && GHL_LOCATION_ID ? 'LIVE' : 'SIMULATED'}`);
});
