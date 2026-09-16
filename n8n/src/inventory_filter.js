// Filters the NS Japan Autos stock snapshot against the arguments Retell sent.
// Input: the snapshot JSON from the "Fetch Stock Snapshot" node.
// Output: a single item that is returned verbatim to the Retell agent.

const webhook = $('Retell Webhook').first().json;
const body = webhook.body || webhook || {};
const args = body.args || {};

const snapshot = $('Fetch Stock Snapshot').first().json;
let vehicles = Array.isArray(snapshot.vehicles) ? snapshot.vehicles : [];

const norm = (s) => String(s === undefined || s === null ? '' : s).trim().toLowerCase();
const has = (v) => v !== undefined && v !== null && String(v).trim() !== '';

// An exact stock number beats every other filter.
if (has(args.stock_id)) {
  const want = norm(args.stock_id).replace(/\s+/g, '');
  vehicles = vehicles.filter((v) => norm(v.stock_id).replace(/\s+/g, '') === want);
} else {
  if (has(args.make)) {
    const want = norm(args.make);
    vehicles = vehicles.filter((v) => norm(v.make).includes(want) || want.includes(norm(v.make)));
  }
  if (has(args.model)) {
    const want = norm(args.model);
    vehicles = vehicles.filter(
      (v) => norm(v.model).includes(want) || norm(v.title).includes(want)
    );
  }
  if (has(args.body_type)) {
    const want = norm(args.body_type);
    vehicles = vehicles.filter((v) => {
      const bt = norm(v.body_type);
      if (bt === want) return true;
      // Tolerate loose phrasing: "minivan" vs "Mini Van / 1 Box", "hatch" vs "HatchBack".
      const squash = (s) => s.replace(/[^a-z0-9]/g, '');
      return squash(bt).includes(squash(want)) || squash(want).includes(squash(bt));
    });
  }
  if (has(args.transmission)) {
    const want = norm(args.transmission);
    vehicles = vehicles.filter((v) => norm(v.transmission).includes(want));
  }
  if (has(args.fuel)) {
    const want = norm(args.fuel);
    vehicles = vehicles.filter((v) => norm(v.fuel).includes(want));
  }
  if (has(args.steering)) {
    const want = norm(args.steering);
    vehicles = vehicles.filter((v) => norm(v.steering) === want);
  }
  if (has(args.keyword)) {
    const want = norm(args.keyword);
    const words = want.split(/\s+/).filter(Boolean);
    vehicles = vehicles.filter((v) => {
      const hay = norm(v.title) + ' ' + norm(v.make) + ' ' + norm(v.model) + ' ' + norm(v.body_type);
      return words.every((w) => hay.includes(w));
    });
  }
}

const num = (x) => {
  const n = Number(x);
  return Number.isFinite(n) ? n : null;
};

const priceMin = num(args.price_min);
const priceMax = num(args.price_max);
const yearMin = num(args.year_min);
const yearMax = num(args.year_max);

if (priceMin !== null) vehicles = vehicles.filter((v) => num(v.price_usd) >= priceMin);
if (priceMax !== null) vehicles = vehicles.filter((v) => num(v.price_usd) <= priceMax);
if (yearMin !== null) vehicles = vehicles.filter((v) => num(v.year) >= yearMin);
if (yearMax !== null) vehicles = vehicles.filter((v) => num(v.year) <= yearMax);

const totalMatches = vehicles.length;

// With a budget, the most useful answer is the best vehicle they can afford, so show the
// dearest first. Without one, lead with the cheapest.
vehicles = vehicles.slice().sort((a, b) => {
  const pa = num(a.price_usd) || 0;
  const pb = num(b.price_usd) || 0;
  return priceMax !== null ? pb - pa : pa - pb;
});

let limit = num(args.limit);
if (limit === null || limit < 1) limit = 3;
limit = Math.min(limit, 5);

const results = vehicles.slice(0, limit).map((v) => ({
  stock_id: v.stock_id,
  title: v.title,
  year: v.year,
  make: v.make,
  model: v.model,
  body_type: v.body_type || null,
  price_usd: v.price_usd,
  mileage_km: v.mileage_km || null,
  mileage_note: v.mileage_km ? 'approximate, confirmed by the sales team' : null,
  transmission: v.transmission || null,
  fuel: v.fuel || null,
  engine: v.engine || null,
  colour: v.color || null,
  steering: v.steering || null,
  seats: v.seats || null,
  doors: v.doors || null,
  chassis: v.chassis || null,
  location: v.location || null,
  url: v.url,
}));

const usd = (n) => '$' + Number(n).toLocaleString('en-US', { maximumFractionDigits: 0 });

let spoken;
if (totalMatches === 0) {
  spoken =
    'No vehicles in the published stock list match that. Tell the caller honestly that ' +
    'nothing listed matches right now, mention we hold over twelve thousand vehicles ' +
    'across our network, and offer to have the team source it for them.';
} else {
  const lines = results.map(
    (v) =>
      `${v.year} ${v.make} ${v.model} - ${usd(v.price_usd)} FOB, ` +
      `${v.mileage_km ? 'about ' + v.mileage_km.toLocaleString('en-US') + ' km, ' : ''}` +
      `${v.transmission || ''}` +
      `${v.fuel ? ', ' + v.fuel : ''}, stock number ${v.stock_id}`
  );
  spoken =
    `${totalMatches} vehicle${totalMatches === 1 ? '' : 's'} match. ` +
    `Showing ${results.length}: ` +
    lines.join(' | ') +
    '. Read these out naturally, say prices as words, and do not read the stock number ' +
    'unless the caller asks for it.';
}

return [
  {
    json: {
      ok: true,
      total_matches: totalMatches,
      returned: results.length,
      filters_applied: args,
      stock_last_updated: snapshot.crawled_at || null,
      vehicles: results,
      summary_for_agent: spoken,
      pricing_note:
        'All prices are FOB - the vehicle only. Freight, insurance, duties and port ' +
        'clearing are not included and must be quoted by the sales team.',
      mileage_note:
        'Mileage figures are approximate. Say them as a round number, for example ' +
        '"about one hundred thousand kilometres", and tell the caller the sales team ' +
        'confirms exact mileage on the quote.',
    },
  },
];
