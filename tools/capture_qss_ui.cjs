const fs = require('fs');
const path = require('path');
// Optional Playwright is loaded only when running a capture.

process.umask(0o077);
const allowedReads = new Set([
  'check_session_alive.json', 'get_model_name.json', 'status.json',
  'get_ip_and_port.json', 'port_stats.json', 'port_setting_load.json',
  'port_statistics.json', 'port_trunk_cfg.json', 'port_trunk_refresh.json',
  'port_lock_cfg.json', 'port_loop_status.json', 'stp.json',
  'storm_ctrl_cfg.json', 'eee_config.json', 'port_mirror.json',
  'port_vlan_cfg.json', 'tag_vlan.json', 'tag_vlan_cfg.json',
  'all_port_pvid.json', 'get_vlan_list.json', 'dhcp_snooping_cfg.json',
  'system_status.json', 'qos_get_port_mode.json', 'qos_get_rate_limit.json',
  'port_vlan.json', 'acl_add.json', 'igmp_config.json', 'igmp_query_config.json',
  'igmp_rp_config.json', 'igmp_get_entries.json',
  'mac_get_static_mac_entries.json', 'mac_get_dynamic_mac_entries.json',
]);
const pageScripts = {
  'link_aggregation.html': 'port_trunking.js',
  'port_settings.html': 'port_setting.js',
  'loop_stp.html': 'loop_stp.js',
  'storm_control.html': 'storm_ctrl_cfg.js',
  'port_mirror.html': 'port_info_mirror.js',
  'eee.html': 'eee_config.js',
  'port_statistics.html': 'port_info.js',
  'tag_based_vlan.html': 'tag_basedvlan.js',
  'port_based_vlan.html': 'port_basedvlan.js',
  'dhcp_snooping.html': 'dhcp_snoop.js',
  'qos_rate_limit.html': 'qos_rate_limiter.js',
  'acl_config.html': 'acl_configure.js',
  'igmp_snooping.html': 'igmp_config.js',
  'igmp_snooping_querier.html': 'igmp_snoop_querier.js',
  'br_static_mac_entries.html': 'bridge_static_mac_entries.js',
  'br_dynamic_mac_entries.html': 'bridge_dynamic_mac_entries.js',
};

function classifyRequest(method, rawUrl, origin) {
  const url = new URL(rawUrl);
  const name = url.pathname.slice(1);
  const staticAsset = /^(?:js|css|pic)\/[\w./-]+\.(?:js|css|png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf)$/.test(name) ||
    name === 'index.html' || name === 'setup.html' ||
    Object.hasOwn(pageScripts, name);
  return {
    allowed: url.origin === origin && !url.username && !url.password &&
      method === 'GET' && (staticAsset || allowedReads.has(name)),
    staticAsset, name, pathname: url.pathname,
  };
}

let browser;
let manifestPath;
let base, rendered, responses;
let queue = Promise.resolve();
let sequence = 0;
let currentPage = 'startup';
const manifest = {started: new Date().toISOString(), requests: [], blocked: [], pages: [], pageErrors: []};

async function main() {
  let input = '';
  for await (const chunk of process.stdin) input += chunk;
  const config = JSON.parse(input);
  const origin = new URL(config.host).origin;
  const selected = config.pages || Object.keys(pageScripts);
  if (!selected.length || selected.some(p => !Object.hasOwn(pageScripts, p))) {
    throw new Error('unsupported capture page');
  }
  const pages = [...new Set(selected)].map(p => [p, pageScripts[p]]);
  base = config.output;
  rendered = path.join(base, 'rendered');
  responses = path.join(base, 'browser-responses');
  manifestPath = path.join(base, 'browser-manifest.json');
  fs.mkdirSync(rendered, {mode: 0o700});
  fs.mkdirSync(responses, {mode: 0o700});
  const { chromium } = require(config.playwrightModule || 'playwright');
  browser = await chromium.launch({executablePath: config.browserExecutable || undefined, headless: true});
  const context = await browser.newContext({ignoreHTTPSErrors: config.insecure === true,
    viewport: {width: 1440, height: 1100}, serviceWorkers: 'block',
    extraHTTPHeaders: {'Connection': 'close'}});
  await context.addCookies(config.cookies);
  await context.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const {allowed, staticAsset, name} = classifyRequest(request.method(), request.url(), origin);
    if (!allowed) {
      manifest.blocked.push({page: currentPage, method: request.method(), path: url.pathname});
      await route.abort('blockedbyclient');
      return;
    }
    const local = path.join(base, name);
    const mime = name.endsWith('.js') ? 'application/javascript' :
      name.endsWith('.css') ? 'text/css' : name.endsWith('.html') ? 'text/html' : null;
    if (staticAsset && mime && fs.existsSync(local)) {
      await route.fulfill({status: 200, contentType: mime, body: fs.readFileSync(local)});
      return;
    }
    const pageAtRequest = currentPage;
    const work = async () => {
      try {
        const result = await route.fetch({timeout: 15000, maxRedirects: 0,
          headers: {...request.headers(), connection: 'close'}});
        const body = await result.body();
        const headers = result.headers();
        const id = String(++sequence).padStart(4, '0');
        const filename = id + '-' + name.replaceAll('/', '_');
        fs.writeFileSync(path.join(responses, filename), body);
        manifest.requests.push({page: pageAtRequest, method: 'GET', path: url.pathname,
          status: result.status(), bytes: body.length, file: filename});
        if (staticAsset && result.status() === 200) {
          fs.mkdirSync(path.dirname(local), {recursive: true, mode: 0o700});
          fs.writeFileSync(local, body);
        }
        await route.fulfill({status: result.status(),
          contentType: headers['content-type'] || 'application/octet-stream', body});
      } catch (error) {
        manifest.requests.push({page: pageAtRequest, method: 'GET', path: url.pathname,
          error: error.name});
        await route.abort('failed').catch(() => {});
      }
    };
    queue = queue.then(work, work);
    await queue;
  });
  const page = await context.newPage();
  page.on('pageerror', error => manifest.pageErrors.push({page: currentPage, name: error.name, message: error.message}));
  page.on('dialog', dialog => dialog.dismiss());
  await page.goto(origin + '/index.html', {waitUntil: 'domcontentloaded', timeout: 30000});
  await page.waitForFunction(() => typeof loadPage === 'function' &&
    typeof getPageTranslation === 'function', {timeout: 15000});
  await page.waitForTimeout(2500);
  await queue;
  for (const [html, script] of pages) {
    currentPage = html;
    await page.evaluate(([p,s]) => loadPage(p,s), [html,script]);
    let shellLoaded = true;
    try {
      await page.waitForFunction(p => window.currentPage === p &&
        document.querySelector('#content .mainbody'), html, {timeout: 15000});
      await page.waitForTimeout(2500);
      await queue;
    } catch (error) { shellLoaded = false; }
    const details = await page.evaluate(() => ({
      page: window.currentPage,
      text: document.querySelector('#content')?.innerText || '',
      controls: Array.from(document.querySelectorAll('#content input, #content select, #content button')).map(e => ({
        tag: e.tagName, id: e.id, name: e.name, type: e.type, value: e.value,
        checked: e.checked, disabled: e.disabled,
        options: e.tagName === 'SELECT' ? Array.from(e.options).map(o=>({value:o.value,text:o.text,disabled:o.disabled})) : undefined,
      })),
      formData: document.querySelector('#mainform') ?
        Array.from(new FormData(document.querySelector('#mainform')).entries()) : null,
    }));
    const stem = html.replace('.html','');
    fs.writeFileSync(path.join(rendered, stem+'.json'), JSON.stringify(details,null,2));
    fs.writeFileSync(path.join(rendered, stem+'.html'), await page.content());
    await page.screenshot({path:path.join(rendered,stem+'.png'),fullPage:true});
    manifest.pages.push({page: html, shellLoaded, currentPage: details.page,
      textLength: details.text.length, controls: details.controls.length});
    console.log(JSON.stringify(manifest.pages.at(-1)));
  }
  await page.evaluate(() => { if (typeof cleanupPreviousPage === 'function') cleanupPreviousPage(); });
  await queue;
  manifest.finished = new Date().toISOString();
  fs.writeFileSync(manifestPath,JSON.stringify(manifest,null,2));
  await browser.close();
  console.log(JSON.stringify({blocked: manifest.blocked, responses: manifest.requests.length}));
}
module.exports = {classifyRequest, pageScripts};

if (require.main === module) main().catch(async error => {
  console.error('Capture failed: '+error.name);
  if (manifestPath) fs.writeFileSync(manifestPath,JSON.stringify(manifest,null,2));
  if (browser) await browser.close().catch(()=>{});
  process.exitCode = 1;
});
