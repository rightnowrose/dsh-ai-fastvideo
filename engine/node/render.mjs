#!/usr/bin/env node
/**
 * 跨平台逐帧渲染器：Edge/Chrome 的 DevTools 协议（CDP）+ ffmpeg。
 *
 * 和 tools/render-cdp.ps1 是同一个活，但没有 PowerShell 依赖：Windows / macOS / Linux
 * 都用这一份。调用契约也和 ps1 一样（参数名一致、stdout 打印进度），方便上层随便换。
 *
 * 用法：
 *   node render.mjs --html foo.html --out out.mp4 --width 1080 --height 1920 \
 *        --fps 24 --duration 6 --mode canvas --format jpeg --crf 21 --timeout 900
 *
 * 退出码：0 成功；非 0 失败（stderr 有原因）。
 */

import { spawn } from 'node:child_process';
import { mkdtemp, rm, writeFile, mkdir, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, dirname } from 'node:path';
import { pathToFileURL } from 'node:url';

// ---------------------------------------------------------------- 参数

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith('--')) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith('--')) out[key] = true;
    else {
      out[key] = next;
      i += 1;
    }
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const htmlPath = args.html && resolve(String(args.html));
const outPath = args.out && resolve(String(args.out));
const width = Number(args.width || 1080);
const height = Number(args.height || 1920);
const fps = Number(args.fps || 24);
const mode = String(args.mode || 'canvas');
const format = String(args.format || (mode === 'canvas' ? 'jpeg' : 'png')).toLowerCase();
const quality = Math.max(1, Math.min(100, Number(args.quality || 93)));
const crf = Number(args.crf || 21);
const preset = String(args.preset || 'medium');
const timeoutSec = Number(args.timeout || 900);
const ioTimeoutMs = Number(args['io-timeout'] || 60000);
const readyTimeoutMs = Number(args['ready-timeout'] || 30000);
const crfValue = Number.isFinite(crf) ? crf : 21;

const browserBin = process.env.VIDEOGEN_EDGE || process.env.VIDEOGEN_BROWSER || '';
const ffmpegBin = process.env.VIDEOGEN_FFMPEG || 'ffmpeg';

if (!htmlPath) fail('缺 --html');
if (!outPath) fail('缺 --out');
if (!browserBin) fail('缺浏览器路径（环境变量 VIDEOGEN_EDGE）');

const duration = args.duration && Number(args.duration) > 0 ? Number(args.duration) : 0;

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function log(message) {
  process.stdout.write(`${message}\n`);
}

// ---------------------------------------------------------------- CDP 客户端

class Cdp {
  constructor(url) {
    this.url = url;
    this.nextId = 1;
    this.pending = new Map();
    this.socket = null;
  }

  async connect() {
    const ws = new WebSocket(this.url);
    this.socket = ws;
    await new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => rejectPromise(new Error('连接 CDP 超时')), readyTimeoutMs);
      ws.addEventListener('open', () => {
        clearTimeout(timer);
        resolvePromise();
      });
      ws.addEventListener('error', (event) => {
        clearTimeout(timer);
        rejectPromise(new Error(`连接 CDP 失败：${event?.message || 'socket error'}`));
      });
    });
    ws.addEventListener('message', (event) => {
      let payload;
      try {
        payload = JSON.parse(typeof event.data === 'string' ? event.data : '');
      } catch {
        return;
      }
      if (payload.id && this.pending.has(payload.id)) {
        const { resolve: done, reject: nope } = this.pending.get(payload.id);
        this.pending.delete(payload.id);
        if (payload.error) nope(new Error(`${payload.error.message} (${payload.error.code})`));
        else done(payload.result ?? {});
      }
    });
    ws.addEventListener('close', () => {
      for (const { reject: nope } of this.pending.values()) nope(new Error('CDP 连接被关闭'));
      this.pending.clear();
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    const socket = this.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error('CDP 未连接'));
    }
    const promise = new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        rejectPromise(new Error(`CDP 读超时（${ioTimeoutMs}ms 没有响应：${method}）`));
      }, ioTimeoutMs);
      this.pending.set(id, {
        resolve: (value) => {
          clearTimeout(timer);
          resolvePromise(value);
        },
        reject: (error) => {
          clearTimeout(timer);
          rejectPromise(error);
        },
      });
    });
    socket.send(JSON.stringify({ id, method, params }));
    return promise;
  }

  async evaluate(expression) {
    const result = await this.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails) {
      const text = result.exceptionDetails.exception?.description
        || result.exceptionDetails.text
        || '页面脚本抛异常';
      throw new Error(text.split('\n')[0]);
    }
    return result.result?.value;
  }

  close() {
    try {
      this.socket?.close();
    } catch {
      /* ignore */
    }
  }
}

async function waitForDevtools(port, deadline) {
  const endpoint = `http://127.0.0.1:${port}/json/version`;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(endpoint);
      if (response.ok) return true;
    } catch {
      /* 浏览器还没起来 */
    }
    await sleep(120);
  }
  return false;
}

async function firstPageTarget(port) {
  const response = await fetch(`http://127.0.0.1:${port}/json/list`);
  const list = await response.json();
  const page = list.find((item) => item.type === 'page' && item.webSocketDebuggerUrl);
  if (!page) throw new Error('浏览器里没有可用的页面目标');
  return page;
}

const sleep = (ms) => new Promise((resolvePromise) => setTimeout(resolvePromise, ms));

// ---------------------------------------------------------------- 主流程

async function main() {
  const startedAt = Date.now();
  const deadline = startedAt + timeoutSec * 1000;
  const scratch = await mkdtemp(join(tmpdir(), 'dsaiv-render-'));
  const profileDir = join(scratch, 'profile');
  const framesDir = join(scratch, 'frames');
  await mkdir(profileDir, { recursive: true });
  await mkdir(framesDir, { recursive: true });
  await mkdir(dirname(outPath), { recursive: true });

  const port = Number(args.port || 9000 + Math.floor(Math.random() * 900));
  const browserArgs = [
    '--headless=new',
    '--disable-gpu',
    '--hide-scrollbars',
    '--mute-audio',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
    '--disable-background-timer-throttling',
    '--disable-renderer-backgrounding',
    '--allow-file-access-from-files',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profileDir}`,
    `--window-size=${width},${height}`,
    'about:blank',
  ];

  const browser = spawn(browserBin, browserArgs, { stdio: 'ignore', windowsHide: true });
  let cdp = null;
  let exitCode = 0;

  try {
    const ok = await waitForDevtools(port, Date.now() + readyTimeoutMs);
    if (!ok) throw new Error(`浏览器 ${readyTimeoutMs}ms 内没开调试端口（${port}）`);

    const target = await firstPageTarget(port);
    cdp = new Cdp(target.webSocketDebuggerUrl);
    await cdp.connect();
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width,
      height,
      deviceScaleFactor: 1,
      mobile: false,
    });

    const url = pathToFileURL(htmlPath).href;
    await cdp.send('Page.navigate', { url });

    // 等页面自己说准备好了：window.seek 是函数、DUR 有值
    const readyDeadline = Date.now() + readyTimeoutMs;
    let ready = '';
    while (Date.now() < readyDeadline) {
      try {
        ready = await cdp.evaluate("(document.readyState||'') + '|' + (typeof window.seek)");
      } catch {
        ready = '';
      }
      if (ready.startsWith('complete|function')) break;
      await sleep(120);
    }
    if (!ready.startsWith('complete|function')) {
      throw new Error(`页面 ${Math.round(readyTimeoutMs / 1000)} 秒内没准备好（readyState|seek = ${ready}）`);
    }

    let span = duration;
    if (!span) {
      const reported = await cdp.evaluate('typeof window.DUR === "number" ? window.DUR : 0');
      span = Number(reported) > 0 ? Number(reported) : 5;
      log(`duration auto: ${span}s`);
    }

    const total = Math.max(1, Math.round(span * fps));
    const ext = format === 'png' ? 'png' : 'jpg';
    const fileFor = (index) => join(framesDir, `f${String(index).padStart(5, '0')}.${ext}`);

    for (let index = 0; index < total; index += 1) {
      if (Date.now() > deadline) {
        throw new Error(`渲染超时：${timeoutSec} 秒还没渲完（${index}/${total} 帧）`);
      }
      const time = index / fps;
      await cdp.evaluate(`window.seek(${time.toFixed(6)}), 0`);

      let base64;
      if (mode === 'canvas') {
        const dataUrl = await cdp.evaluate(
          `(() => {
             const canvas = document.getElementById('cv') || document.querySelector('canvas');
             if (!canvas) return '';
             return canvas.toDataURL(${JSON.stringify(`image/${ext === 'jpg' ? 'jpeg' : 'png'}`)}, ${quality / 100});
           })()`,
        );
        if (!dataUrl || typeof dataUrl !== 'string') {
          throw new Error('canvas 模式取不到画面：页面里没有 canvas（用 --mode screenshot）');
        }
        base64 = dataUrl.slice(dataUrl.indexOf(',') + 1);
      } else {
        const shot = await cdp.send('Page.captureScreenshot', {
          format: ext === 'jpg' ? 'jpeg' : 'png',
          quality,
          fromSurface: true,
          captureBeyondViewport: false,
        });
        base64 = shot.data;
      }
      await writeFile(fileFor(index), Buffer.from(base64, 'base64'));
      if (index % 10 === 9 || index === total - 1) log(`frame ${index + 1}/${total}`);
    }

    log('encoding…');
    await encode(framesDir, ext, total, outPath);
    log(`done: ${outPath} (${total} frames, ${Math.round((Date.now() - startedAt) / 1000)}s)`);
  } catch (error) {
    exitCode = 1;
    process.stderr.write(`${error?.message || error}\n`);
  } finally {
    cdp?.close();
    try {
      browser.kill('SIGKILL');
    } catch {
      /* ignore */
    }
    if (!args['keep-work']) {
      await rm(scratch, { recursive: true, force: true }).catch(() => {});
    } else {
      log(`work kept: ${scratch}`);
    }
  }
  process.exit(exitCode);
}

async function encode(framesDir, ext, total, outFile) {
  const entries = (await readdir(framesDir)).filter((name) => name.endsWith(`.${ext}`));
  if (entries.length !== total) {
    throw new Error(`帧数不对：期望 ${total}，实际 ${entries.length}`);
  }
  const pattern = join(framesDir, `f%05d.${ext}`);
  const encodeArgs = [
    '-y', '-v', 'error',
    '-framerate', String(fps),
    '-i', pattern,
    '-c:v', 'libx264',
    '-preset', preset,
    '-crf', String(crfValue),
    '-pix_fmt', 'yuv420p',
  ];
  if (ext === 'jpg') {
    encodeArgs.push(
      '-vf', 'scale=in_range=full:out_range=tv,format=yuv420p',
      '-color_range', 'tv',
      '-colorspace', 'bt709',
      '-color_primaries', 'bt709',
      '-color_trc', 'bt709',
    );
  }
  encodeArgs.push('-movflags', '+faststart', outFile);

  await new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(ffmpegBin, encodeArgs, { stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true });
    let tail = '';
    child.stderr.on('data', (chunk) => {
      tail = `${tail}${chunk.toString('utf8')}`.slice(-4000);
    });
    child.on('error', (error) => rejectPromise(new Error(`ffmpeg 起不来：${error.message}`)));
    child.on('close', (code) => {
      if (code === 0) resolvePromise();
      else rejectPromise(new Error(`ffmpeg 编码失败（exit ${code}）：${tail.trim().split('\n').slice(-3).join(' ')}`));
    });
  });
}

main().catch((error) => {
  process.stderr.write(`${error?.stack || error}\n`);
  process.exit(1);
});
