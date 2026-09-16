/**
 * Runs n8n/src/inventory_filter.js against the real snapshot with a stubbed n8n
 * runtime, so the search logic can be tested without deploying anything.
 *
 *   node scripts/test_inventory_filter.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const snapshot = JSON.parse(
  fs.readFileSync(path.join(ROOT, 'data', 'inventory.json'), 'utf8')
);
const code = fs.readFileSync(path.join(ROOT, 'n8n', 'src', 'inventory_filter.js'), 'utf8');

function runFilter(args) {
  const nodes = {
    'Retell Webhook': { json: { body: { name: 'search_inventory', call: {}, args } } },
    'Fetch Stock Snapshot': { json: snapshot },
  };
  const $ = (name) => {
    if (!nodes[name]) throw new Error('unknown node ' + name);
    return { first: () => nodes[name] };
  };
  const sandbox = { $, console, JSON, Number, Math, String, Array, Object, Boolean };
  const result = vm.runInNewContext(`(function(){${code}})()`, sandbox);
  return result[0].json;
}

let pass = 0;
let fail = 0;

function check(label, condition, detail) {
  if (condition) {
    pass++;
    console.log(`  PASS  ${label}`);
  } else {
    fail++;
    console.log(`  FAIL  ${label}${detail ? '  ->  ' + detail : ''}`);
  }
}

console.log('\n--- search_inventory logic tests ---\n');

// 1. No filters at all
let r = runFilter({});
check('no filters returns 3 of the full list', r.returned === 3 && r.total_matches === snapshot.count,
  `returned=${r.returned} total=${r.total_matches}`);

// 2. Make filter
r = runFilter({ make: 'TOYOTA' });
check('make=TOYOTA matches 127', r.total_matches === 127, `got ${r.total_matches}`);
check('make=TOYOTA results are all Toyota',
  r.vehicles.every((v) => v.make === 'TOYOTA'));

// 3. Lowercase / loose make
r = runFilter({ make: 'toyota' });
check('make is case-insensitive', r.total_matches === 127, `got ${r.total_matches}`);

// 4. Model filter
r = runFilter({ make: 'TOYOTA', model: 'ALPHARD' });
check('Toyota Alphard matches 8', r.total_matches === 8, `got ${r.total_matches}`);

// 5. Partial model
r = runFilter({ model: 'land cruiser' });
check('partial model "land cruiser" finds stock', r.total_matches >= 5, `got ${r.total_matches}`);

// 6. Body type, exact
r = runFilter({ body_type: 'SUV' });
check('body_type=SUV matches 64', r.total_matches === 64, `got ${r.total_matches}`);

// 7. Body type, loose phrasing
r = runFilter({ body_type: 'Mini Van / 1 Box' });
const minivanExact = r.total_matches;
r = runFilter({ body_type: 'minivan' });
check('loose "minivan" maps to Mini Van / 1 Box',
  r.total_matches === minivanExact && r.total_matches === 28, `got ${r.total_matches}`);

// 8. Budget cap
r = runFilter({ price_max: 3000 });
check('price_max=3000 excludes anything dearer',
  r.vehicles.every((v) => v.price_usd <= 3000));
check('price_max sorts dearest-first (best they can afford)',
  r.vehicles[0].price_usd >= r.vehicles[r.vehicles.length - 1].price_usd,
  JSON.stringify(r.vehicles.map((v) => v.price_usd)));

// 9. No budget sorts cheapest-first
r = runFilter({ make: 'SUBARU' });
check('no budget sorts cheapest-first',
  r.vehicles[0].price_usd <= r.vehicles[r.vehicles.length - 1].price_usd,
  JSON.stringify(r.vehicles.map((v) => v.price_usd)));

// 10. Combined realistic query
r = runFilter({ body_type: 'SUV', price_max: 5000, transmission: 'Automatic' });
check('SUV + under 5000 + automatic returns matches', r.total_matches > 0, `got ${r.total_matches}`);
check('combined filters all hold',
  r.vehicles.every((v) => v.body_type === 'SUV' && v.price_usd <= 5000 && v.transmission === 'Automatic'));

// 11. Exact stock id
const sample = snapshot.vehicles[10];
r = runFilter({ stock_id: sample.stock_id });
check('stock_id returns exactly one', r.total_matches === 1 && r.vehicles[0].stock_id === sample.stock_id);

// 12. stock_id overrides other filters
r = runFilter({ stock_id: sample.stock_id, make: 'FERRARI' });
check('stock_id overrides other filters', r.total_matches === 1);

// 13. Zero results path
r = runFilter({ make: 'FERRARI' });
check('unknown make returns zero', r.total_matches === 0 && r.vehicles.length === 0);
check('zero results tells the agent to offer sourcing',
  /source/i.test(r.summary_for_agent), r.summary_for_agent);

// 14. Steering
r = runFilter({ steering: 'Left' });
check('left-hand drive matches the 2 in stock', r.total_matches === 2, `got ${r.total_matches}`);

// 15. Fuel
r = runFilter({ fuel: 'Diesel' });
check('diesel matches 11', r.total_matches === 11, `got ${r.total_matches}`);

// 16. Year range
r = runFilter({ year_min: 2010, year_max: 2011 });
check('year range 2010-2011 matches 102', r.total_matches === 102, `got ${r.total_matches}`);

// 17. Keyword fallback
r = runFilter({ keyword: 'hiace van' });
check('keyword "hiace van" finds the Hiace vans', r.total_matches >= 8, `got ${r.total_matches}`);

// 18. Limit handling
r = runFilter({ make: 'TOYOTA', limit: 5 });
check('limit=5 honoured', r.returned === 5, `got ${r.returned}`);
r = runFilter({ make: 'TOYOTA', limit: 50 });
check('limit is capped at 5 for voice', r.returned === 5, `got ${r.returned}`);

// 19. Shape the agent depends on
r = runFilter({ make: 'TOYOTA' });
check('response carries a spoken summary', typeof r.summary_for_agent === 'string' && r.summary_for_agent.length > 20);
check('response carries the FOB pricing caveat', /FOB/.test(r.pricing_note));
check('vehicles carry price, year, model, stock id',
  r.vehicles.every((v) => v.price_usd && v.year && v.model && v.stock_id));
check('mileage is expanded from the published thousands to real km',
  r.vehicles.every((v) => !v.mileage_km || v.mileage_km >= 1000),
  JSON.stringify(r.vehicles.map((v) => v.mileage_km)));
check('summary speaks mileage as a full km figure',
  /about [\d,]{4,} km/.test(r.summary_for_agent), r.summary_for_agent);
check('response warns that mileage is approximate', /approximate/i.test(r.mileage_note));

console.log(`\n--- ${pass} passed, ${fail} failed ---\n`);
if (fail > 0) process.exit(1);
