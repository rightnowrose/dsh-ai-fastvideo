/**
 * 离线自检：假装成 DSH 的 Host 环境，加载 index.js 的路由，打几个 GET 看返回。
 *
 *   node scripts/selftest.mjs
 *
 * 不依赖 DSH 本体，只验证「插件能被 import、路由能注册、catalog/health 能返回」。
 * 真正的出片验证仍然要走面板或 build_job.py。
 */

import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';

const routes = [];
let applied = 0;

const fakeCtx = {
  webServer: {
    register(route) {
      routes.push(route);
      return () => {};
    },
  },
  connection: {
    requestRejection() {
      return undefined; // 放行
    },
  },
  effect(fn) {
    const disposer = fn();
    applied += 1;
    return () => {
      if (typeof disposer === 'function') disposer();
    };
  },
  logger: { info() {}, warn() {}, error() {} },
};

function fakeRes() {
  const res = new EventEmitter();
  res.statusCode = 200;
  res.headers = {};
  res.parts = [];
  res.setHeader = (key, value) => {
    res.headers[key.toLowerCase()] = value;
  };
  res.end = (chunk) => {
    if (chunk) res.parts.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk)));
    res.emit('finish');
  };
  res.body = () => Buffer.concat(res.parts).toString('utf8');
  return res;
}

async function request(method, path, payload = '') {
  const route = routes.find((item) =>
    item.kind === 'prefix' ? path.startsWith(item.path) : item.path === path,
  );
  if (!route) return { status: 404, body: `no route for ${path}` };
  const req = Readable.from(payload ? [Buffer.from(payload)] : []);
  Object.assign(req, { method, url: path, headers: { host: '127.0.0.1', origin: 'http://127.0.0.1' } });
  req.socket = { remoteAddress: '127.0.0.1' };
  const res = fakeRes();
  await route.handler(req, res);
  return { status: res.statusCode, body: res.body() };
}

const mod = await import('../index.js');
mod.apply(fakeCtx);

const out = {};
out.name = mod.name;
out.inject = mod.inject;
out.routes = routes.map((r) => `${r.kind} ${r.path}`);

const health = await request('GET', '/ai-video/health');
out.health = { status: health.status, body: safeParse(health.body) };

const catalog = await request('GET', '/ai-video/catalog');
const parsed = safeParse(catalog.body);
out.catalog = {
  status: catalog.status,
  count: parsed && parsed.count,
  voices: parsed && Array.isArray(parsed.voices) ? parsed.voices.length : null,
  hasThumb: parsed && Array.isArray(parsed.styles)
    ? parsed.styles.filter((s) => s.hasThumb).length
    : null,
};

const bad = await request('POST', '/ai-video/jobs', JSON.stringify({ mode: 'prompt' }));
out.rejectBadPayload = { status: bad.status, body: safeParse(bad.body) };

function safeParse(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

console.log(JSON.stringify(out, null, 2));

const ok = out.health.status === 200 && out.catalog.count > 0 && out.routes.length >= 7;
console.log(ok ? '\nRESULT OK' : '\nRESULT FAILED');
process.exit(ok ? 0 : 1);
