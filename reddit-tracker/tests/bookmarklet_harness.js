/* Runs the real bookmarklet code in Node with a fake reddit.com: fetch answers from a routes file,
   the page DOM is stubbed, and the downloaded export is printed as JSON. Used by test_browser_import.py.
   Usage: node bookmarklet_harness.js <bookmarklet.js> <routes.json> */
const fs = require('fs');
const [jsPath, routesPath] = process.argv.slice(2);
const routes = JSON.parse(fs.readFileSync(routesPath, 'utf8'));

global.window = global;
global.location = { hostname: 'www.reddit.com', origin: 'https://www.reddit.com' };
global.alert = (m) => { process.stderr.write('ALERT ' + m + '\n'); };
const element = () => ({ style: {}, appendChild() {}, click() {}, remove() {} });
global.document = { createElement: element, body: { appendChild() {} } };
let blob = null;
URL.createObjectURL = (b) => { blob = b; return 'blob:export'; };

const calls = [];
const counters = {};
/* logged-out requests (credentials: "omit") are looked up as "anon:<path>" */
global.fetch = async (path, opts) => {
  const key = (opts && opts.credentials === 'omit' ? 'anon:' : '') + path;
  calls.push(key);
  let route = routes[key];
  if (Array.isArray(route)) {
    const i = counters[key] || 0;
    counters[key] = i + 1;
    route = route[Math.min(i, route.length - 1)];
  }
  const r = route || { status: 404, body: { error: 404 } };
  const status = r.status || 200;
  return {
    ok: status >= 200 && status < 300,
    status,
    url: r.url || 'https://www.reddit.com' + path,
    headers: { get: () => r.type || 'application/json; charset=UTF-8' },
    json: async () => r.body,
  };
};

eval(fs.readFileSync(jsPath, 'utf8'));
const timer = setInterval(async () => {
  if (!global.__nbLastExport) return;
  clearInterval(timer);
  const text = await blob.text();
  process.stdout.write(JSON.stringify({ export: JSON.parse(text), calls }));
  process.exit(0);
}, 10);
setTimeout(() => { process.stderr.write('timed out\n'); process.exit(2); }, 30000);
