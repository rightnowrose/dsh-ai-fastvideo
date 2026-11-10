/**
 * Host half of AI速成视频 (dsh-ai-video): routes under `ai-video/` that the browser
 * panel uses to read the style library, start a render, follow its progress and
 * play the result.
 *
 * Everything machine-specific lives in `<dataDir>/config.json` (see README):
 *   ffmpeg / ffprobe / browser / python  -> explicit paths (empty = auto detect)
 *   styleDir / extraStyleDirs            -> where the style prompts live
 *   llm { baseUrl, apiKey, model }       -> used by the "AI 写页面" modes
 *   bgm { file, volume }                 -> optional background music
 *   stickersDir                          -> optional GIF stickers
 *
 * Why a Host half at all: the browser cannot spawn processes. Rendering is one
 * long `python engine/python/tools/build_job.py job.json` run whose stdout is a
 * JSON-lines progress feed; keeping the child process here means the panel can
 * close and reopen without losing the job.
 */

import { spawn } from 'node:child_process';
import { createReadStream, existsSync, readFileSync } from 'node:fs';
import { readdir, readFile, realpath, stat, writeFile, mkdir } from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, join, normalize, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { zstdDecompressSync } from 'node:zlib';

// ---------------------------------------------------------------- layout
// 包内 = 只读（引擎、内置风格、模板）；用户数据 = <dataDir>，默认 ~/.dsh/data/dsh-ai-video。
const PLUGIN_DIR = dirname(fileURLToPath(import.meta.url));
const ENGINE_DIR = join(PLUGIN_DIR, 'engine', 'python');
const BUILTIN_STYLE_DIR = join(PLUGIN_DIR, 'assets', 'styles');

function envFirst(...names) {
  for (const name of names) {
    const value = process.env[name];
    if (value && value.trim()) return value.trim();
  }
  return '';
}

function firstExisting(list) {
  for (const item of list) {
    if (!item) continue;
    try {
      if (existsSync(item)) return item;
    } catch {
      /* ignore */
    }
  }
  return '';
}

/** 在 PATH 里找可执行文件（Windows 会补 PATHEXT）。 */
function whichSync(command) {
  const pathVar = process.env.PATH || process.env.Path || '';
  const dirs = pathVar.split(process.platform === 'win32' ? ';' : ':').filter(Boolean);
  const exts =
    process.platform === 'win32'
      ? ['', ...(process.env.PATHEXT || '.EXE;.CMD;.BAT;.COM').split(';').filter(Boolean)]
      : [''];
  for (const dir of dirs) {
    for (const ext of exts) {
      const candidate = join(dir, command + ext);
      try {
        if (existsSync(candidate)) return candidate;
      } catch {
        /* ignore */
      }
    }
  }
  return '';
}

function detectPython() {
  const explicit = envFirst('DSH_AI_VIDEO_PYTHON', 'VIDEOGEN_PYTHON');
  if (explicit) return explicit;
  const names = process.platform === 'win32' ? ['python', 'python3', 'py'] : ['python3', 'python'];
  for (const name of names) {
    const found = whichSync(name);
    if (found) return found;
  }
  return firstExisting([
    join(process.env.LOCALAPPDATA || '', 'Programs', 'Python', 'Python312', 'python.exe'),
    join(process.env.LOCALAPPDATA || '', 'Programs', 'Python', 'Python311', 'python.exe'),
    '/usr/bin/python3',
    '/usr/local/bin/python3',
  ]);
}

function detectFfmpeg(name) {
  const explicit = envFirst(name === 'ffmpeg' ? 'DSH_AI_VIDEO_FFMPEG' : 'DSH_AI_VIDEO_FFPROBE',
    name === 'ffmpeg' ? 'VIDEOGEN_FFMPEG' : 'VIDEOGEN_FFPROBE');
  if (explicit) return explicit;
  const onPath = whichSync(name);
  if (onPath) return onPath;
  const exe = process.platform === 'win32' ? '.exe' : '';
  return firstExisting([
    `C:\\ffmpeg\\bin\\${name}${exe}`,
    `D:\\ffmpeg\\bin\\${name}${exe}`,
    `/usr/local/bin/${name}`,
    `/opt/homebrew/bin/${name}`,
    `/usr/bin/${name}`,
  ]);
}

function detectBrowser() {
  const explicit = envFirst('DSH_AI_VIDEO_BROWSER', 'VIDEOGEN_EDGE', 'VIDEOGEN_BROWSER');
  if (explicit) return explicit;
  return firstExisting([
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
  ]);
}

function detectPowershell() {
  const explicit = envFirst('DSH_AI_VIDEO_POWERSHELL', 'VIDEOGEN_POWERSHELL');
  if (explicit) return explicit;
  const onPath = whichSync(process.platform === 'win32' ? 'powershell' : 'pwsh');
  if (onPath) return onPath;
  return firstExisting(['C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe']);
}

// ---------------------------------------------------------------- config
const DATA_DIR = resolve(
  envFirst('DSH_AI_VIDEO_DATA', 'VIDEOGEN_DATA_ROOT') ||
    join(homedir(), '.dsh', 'data', 'dsh-ai-video'),
);
const CONFIG_PATH = join(DATA_DIR, 'config.json');

/** 读 <dataDir>/config.json（不存在就是空对象）；读坏了只警告，不拦住插件加载。 */
function loadConfig() {
  try {
    const raw = loadConfigText();
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (error) {
    console.warn(`[dsh-ai-video] 配置文件读不了，按默认值启动：${CONFIG_PATH} (${error && error.message})`);
    return {};
  }
}

function loadConfigText() {
  // 同步读：模块初始化阶段就要把路径定下来，异步会拖出一堆竞态。
  try {
    return readFileSync(CONFIG_PATH, 'utf8');
  } catch (error) {
    if (error && error.code === 'ENOENT') return '';
    throw error;
  }
}

const CONFIG = loadConfig();

const VIDEO_DIR = join(DATA_DIR, 'out');
const JOB_DIR = join(DATA_DIR, 'jobs');
const WORK_DIR = join(DATA_DIR, 'work');

const STYLE_ROOT =
  CONFIG.styleDir || envFirst('DSH_AI_VIDEO_STYLE_DIR', 'VIDEOGEN_STYLE_ROOT') || BUILTIN_STYLE_DIR;

const APPS = ENGINE_DIR; // python 的 cwd（脚本自己会从父目录导入 videogen 包）
const PYTHON = CONFIG.python || detectPython();
const ENGINE_PKG = join(ENGINE_DIR, 'videogen');
const BUILD_JOB = join(ENGINE_PKG, 'tools', 'build_job.py');
const DUMP_CATALOG = join(ENGINE_PKG, 'tools', 'dump_catalog.py');
/** 跨平台渲染器（Node + CDP），与 python 引擎同打包（在 engine/node 下）。 */
const NODE_RENDER = join(PLUGIN_DIR, 'engine', 'node', 'render.mjs');
const FFMPEG = CONFIG.ffmpeg || detectFfmpeg('ffmpeg');
const FFPROBE = CONFIG.ffprobe || detectFfmpeg('ffprobe');
const BROWSER = CONFIG.browser || detectBrowser();
const POWERSHELL = CONFIG.powershell || detectPowershell();
const BGM_FILE = (CONFIG.bgm && CONFIG.bgm.file) || envFirst('DSH_AI_VIDEO_BGM', 'VIDEOGEN_BGM');
const STICKER_DIR = CONFIG.stickersDir || envFirst('DSH_AI_VIDEO_STICKERS', 'VIDEOGEN_STICKERS');
const WORKSPACE_HINT = CONFIG.workspaceDir || envFirst('DSH_AI_VIDEO_WORKSPACE');

/** 子进程共用的环境变量：把上面解析出来的路径全部下发给 python 引擎。 */
function childEnv() {
  const llm = CONFIG.llm || {};
  return {
    ...process.env,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
    VIDEOGEN_PY_ROOT: ENGINE_DIR,
    VIDEOGEN_DATA: VIDEO_DIR,
    VIDEOGEN_WORK: WORK_DIR,
    VIDEOGEN_DB: join(DATA_DIR, 'jobs.db'),
    VIDEOGEN_STYLE_ROOT: STYLE_ROOT,
    VIDEOGEN_FFMPEG: FFMPEG,
    VIDEOGEN_FFPROBE: FFPROBE,
    VIDEOGEN_EDGE: BROWSER,
    VIDEOGEN_POWERSHELL: POWERSHELL,
    VIDEOGEN_BGM: BGM_FILE || '',
    VIDEOGEN_STICKERS: STICKER_DIR || '',
    VIDEOGEN_WORKSPACE: WORKSPACE_HINT || '',
    VIDEOGEN_LLM_BASE: llm.baseUrl || envFirst('DSH_AI_VIDEO_LLM_BASE', 'VIDEOGEN_LLM_BASE'),
    VIDEOGEN_LLM_KEY: llm.apiKey || envFirst('DSH_AI_VIDEO_LLM_KEY', 'VIDEOGEN_LLM_KEY'),
    VIDEOGEN_LLM_MODEL: llm.model || envFirst('DSH_AI_VIDEO_LLM_MODEL', 'VIDEOGEN_LLM_MODEL'),
    // 渲染后端：默认用宿主自带的 node 跑 engine/node/render.mjs（三平台通用）；没有就退回 powershell。
    VIDEOGEN_NODE: CONFIG.node || process.env.DSH_AI_VIDEO_NODE || process.execPath,
    VIDEOGEN_RENDERER: CONFIG.renderer || envFirst('DSH_AI_VIDEO_RENDERER', 'VIDEOGEN_RENDERER') || 'auto',
    // 发布版不带 GPT-SoVITS；需要自训练音色时再显式打开。
    VIDEOGEN_ALLOW_GSV: process.env.VIDEOGEN_ALLOW_GSV || '0',
  };
}

/** Absolute pathname the Host registers; the browser addresses it document-relative. */
const PREFIX = '/ai-video';

/** One render at a time: two CDP/Edge runs fight for the same GPU and the same
 *  output directory, and a queue is what a single operator actually wants. */
const jobs = new Map();
let queue = Promise.resolve();

function sendJson(res, status, payload) {
  res.statusCode = status;
  res.setHeader('content-type', 'application/json; charset=utf-8');
  res.setHeader('cache-control', 'no-store');
  res.end(JSON.stringify(payload));
}

function sendMethodNotAllowed(res, allow) {
  res.statusCode = 405;
  res.setHeader('allow', allow);
  res.end();
}

async function readBody(req, limit = 256 * 1024) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.byteLength;
    if (size > limit) {
      req.resume();
      return null;
    }
    chunks.push(chunk);
  }
  return Buffer.concat(chunks, size).toString('utf8');
}

function connectionOf(ctx) {
  return Reflect.get(ctx, 'connection');
}

/** The connection fence (Host/Origin + login cookie) decides who may call. */
function rejected(ctx, req, res) {
  const connection = connectionOf(ctx);
  if (!connection || typeof connection.requestRejection !== 'function') return false;
  const rejection = connection.requestRejection(req);
  if (rejection === undefined) return false;
  res.statusCode = rejection;
  res.end();
  return true;
}

function runPython(script, args, { timeoutMs = 120000 } = {}) {
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(PYTHON, [script, ...args], {
      cwd: APPS,
      windowsHide: true,
      env: childEnv(),
    });
    let out = '';
    let err = '';
    const timer = setTimeout(() => {
      child.kill();
      rejectPromise(new Error(`python 超时（>${Math.round(timeoutMs / 1000)}s）`));
    }, timeoutMs);
    child.stdout.on('data', (chunk) => {
      out += chunk.toString('utf8');
    });
    child.stderr.on('data', (chunk) => {
      err += chunk.toString('utf8');
    });
    child.on('error', (error) => {
      clearTimeout(timer);
      rejectPromise(error);
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code !== 0) rejectPromise(new Error(err.trim() || `python 退出码 ${code}`));
      else resolvePromise(out);
    });
  });
}

// ------------------------------------------------------- context sources
// Two of the three background sources only exist here: the current session's
// transcript (the panel cannot read it) and files the operator points at.
// The workspace outline and the plugin roster are read by python instead,
// because both are plain text under the project root.

// DSH 把会话日志放在 <DSH_DATA_DIR>/sessions；找不到就当作「没有对话上下文」。
function resolveSessionsRoot() {
  const explicit = envFirst('DSH_AI_VIDEO_SESSIONS', 'DSH_SESSIONS_DIR');
  if (explicit) return explicit;
  const dataDir = envFirst('DSH_DATA_DIR');
  const candidates = [
    dataDir ? join(dataDir, 'sessions') : '',
    join(homedir(), '.dsh', 'sessions'),
  ];
  return firstExisting(candidates);
}

const SESSIONS_ROOT = resolveSessionsRoot();
const ZSTD_MAGIC = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);
const TEXT_EXT = new Set([
  '.md', '.txt', '.json', '.jsonl', '.yaml', '.yml', '.py', '.js', '.ts',
  '.ps1', '.toml', '.ini', '.csv', '.html', '.css',
]);

/**
 * Session logs are appended one zstd frame at a time, so a whole-file decode
 * only returns the first frame. Walk the frame magic instead.
 */
function readZstdFrames(buffer) {
  const parts = [];
  let at = buffer.indexOf(ZSTD_MAGIC, 0);
  while (at !== -1) {
    const next = buffer.indexOf(ZSTD_MAGIC, at + 4);
    const end = next === -1 ? buffer.length : next;
    try {
      parts.push(zstdDecompressSync(buffer.subarray(at, end)));
    } catch {
      // A frame can be half-written while the Agent is mid-turn; skip it.
    }
    at = next;
  }
  return Buffer.concat(parts).toString('utf8');
}

/** Newest `session.v4.jsonl.zstd` under the session store — i.e. this session. */
async function newestSessionLog() {
  if (!SESSIONS_ROOT) return null;
  let best = null;
  let workspaces = [];
  try {
    workspaces = await readdir(SESSIONS_ROOT, { withFileTypes: true });
  } catch {
    return null;
  }
  for (const workspace of workspaces) {
    if (!workspace.isDirectory()) continue;
    let sessions = [];
    try {
      sessions = await readdir(join(SESSIONS_ROOT, workspace.name), { withFileTypes: true });
    } catch {
      continue;
    }
    for (const session of sessions) {
      if (!session.isDirectory()) continue;
      const file = join(SESSIONS_ROOT, workspace.name, session.name, 'session.v4.jsonl.zstd');
      try {
        const info = await stat(file);
        if (!best || info.mtimeMs > best.mtimeMs) {
          best = { file, mtimeMs: info.mtimeMs, sessionId: session.name };
        }
      } catch {
        // no log for this session yet
      }
    }
  }
  return best;
}

/** Filler the runtime injects before a turn; it is not what the user said. */
function isInjectedText(text) {
  const head = text.slice(0, 64);
  return head.startsWith('Current runtime context') || head.startsWith('<system-reminder>') || head.startsWith('Time sampled');
}

let transcriptCache = null;
let transcriptAt = 0;
const TRANSCRIPT_TTL = 15 * 1000;

async function sessionTranscript({ maxChars = 12000, maxMessages = 40 } = {}) {
  const now = Date.now();
  if (transcriptCache && now - transcriptAt < TRANSCRIPT_TTL) return transcriptCache;
  const newest = await newestSessionLog();
  if (!newest) return { ok: false, error: '找不到会话日志', text: '', chars: 0, messages: 0 };
  let raw;
  try {
    raw = readZstdFrames(await readFile(newest.file));
  } catch (error) {
    return { ok: false, error: String(error && error.message ? error.message : error), text: '', chars: 0, messages: 0 };
  }
  const turns = [];
  for (const line of raw.split('\n')) {
    if (!line.trim()) continue;
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      continue;
    }
    if (event.type !== 'user/message' && event.type !== 'assistant/message') continue;
    const blocks = Array.isArray(event.data && event.data.content) ? event.data.content : [];
    const text = blocks
      .filter((block) => block && block.type === 'text' && typeof block.text === 'string')
      .map((block) => block.text)
      .join('\n')
      .trim();
    if (!text) continue;
    if (event.type === 'user/message' && isInjectedText(text)) continue;
    turns.push({ role: event.type === 'user/message' ? '用户' : '助手', text });
  }
  const recent = turns.slice(-maxMessages);
  let text = recent.map((turn) => `${turn.role}：${turn.text}`).join('\n\n');
  if (text.length > maxChars) text = `…（前文省略）\n${text.slice(-maxChars)}`;
  transcriptCache = {
    ok: true,
    sessionId: newest.sessionId,
    updatedAt: newest.mtimeMs,
    messages: recent.length,
    chars: text.length,
    text,
    preview: text.slice(0, 240),
  };
  transcriptAt = now;
  return transcriptCache;
}

/** Read operator-picked files/directories as plain text (bounded). */
async function readPathTexts(paths, { maxFiles = 8, maxChars = 30000 } = {}) {
  const picked = Array.isArray(paths)
    ? paths.filter((p) => typeof p === 'string' && p.trim()).slice(0, maxFiles)
    : [];
  const entries = [];
  let budget = maxChars;
  for (const raw of picked) {
    let full;
    try {
      full = await realpath(raw.trim());
    } catch {
      entries.push({ path: raw.trim(), kind: 'missing', chars: 0 });
      continue;
    }
    let info;
    try {
      info = await stat(full);
    } catch {
      entries.push({ path: full, kind: 'missing', chars: 0 });
      continue;
    }
    if (info.isDirectory()) {
      let names = [];
      try {
        names = await readdir(full, { withFileTypes: true });
      } catch {
        entries.push({ path: full, kind: 'dir', chars: 0 });
        continue;
      }
      // 目录里既可能是文本文件，也可能是一层子目录（插件目录就是这样），
      // 所以直系文本文件与子目录里的一层文件都算候选。
      const candidates = [];
      for (const name of names) {
        if (candidates.length >= 6) break;
        if (name.isFile() && TEXT_EXT.has((name.name.match(/\.[^.]+$/) || [''])[0].toLowerCase())) {
          candidates.push(join(full, name.name));
        }
      }
      for (const name of names) {
        if (candidates.length >= 6) break;
        if (!name.isDirectory()) continue;
        let children = [];
        try {
          children = await readdir(join(full, name.name), { withFileTypes: true });
        } catch {
          continue;
        }
        for (const child of children) {
          if (candidates.length >= 6) break;
          const ext = (child.name.match(/\.[^.]+$/) || [''])[0].toLowerCase();
          if (child.isFile() && ['.md', '.txt', '.yaml', '.yml', '.json'].includes(ext)) {
            candidates.push(join(full, name.name, child.name));
          }
        }
      }
      let text = '';
      for (const file of candidates) {
        if (budget <= 0) break;
        try {
          const chunk = await readFile(file, 'utf8');
          const sliced = chunk.slice(0, Math.min(8000, budget));
          budget -= sliced.length;
          text += `\n### ${file.slice(full.length + 1)}\n${sliced}`;
        } catch {
          // unreadable child; keep walking
        }
      }
      if (text) entries.push({ path: full, kind: 'dir', chars: text.length, text });
      else entries.push({ path: full, kind: 'dir', chars: 0 });
      continue;
    }
    if (info.size > 2 * 1024 * 1024) {
      entries.push({ path: full, kind: 'too-large', chars: 0 });
      continue;
    }
    try {
      const chunk = await readFile(full, 'utf8');
      const sliced = chunk.slice(0, Math.min(8000, Math.max(0, budget)));
      if (!sliced) {
        entries.push({ path: full, kind: 'empty', chars: 0 });
        continue;
      }
      budget -= sliced.length;
      entries.push({ path: full, kind: 'file', chars: sliced.length, text: sliced });
    } catch (error) {
      entries.push({ path: full, kind: 'unreadable', chars: 0, error: String(error && error.message ? error.message : error) });
    }
  }
  const text = entries
    .filter((entry) => entry.text)
    .map((entry) => `### ${entry.path}\n${entry.text}`)
    .join('\n\n')
    .slice(0, maxChars);
  return {
    entries: entries.map(({ text: _text, ...rest }) => rest),
    files: entries.filter((entry) => entry.text).length,
    chars: text.length,
    text,
  };
}

async function resolveContextText(body) {
  if (!body || body.mode !== 'auto') return '';
  const wanted = Array.isArray(body.contexts) ? body.contexts.filter((k) => typeof k === 'string').slice(0, 6) : [];
  const pieces = [];
  if (wanted.includes('conversation')) {
    const transcript = await sessionTranscript();
    if (transcript.ok && transcript.text) pieces.push(`## 本次对话（最近 ${transcript.messages} 条）\n${transcript.text}`);
  }
  if (wanted.includes('files')) {
    const files = await readPathTexts(body.paths);
    if (files.text) pieces.push(`## 自选资料（${files.files} 个文件）\n${files.text}`);
  }
  return pieces.join('\n\n').slice(0, 40000);
}

// ---------------------------------------------------------------- catalog

let catalogCache = null;
let catalogAt = 0;
const CATALOG_TTL = 60 * 1000;

async function catalog(force = false) {
  const now = Date.now();
  if (!force && catalogCache && now - catalogAt < CATALOG_TTL) return catalogCache;
  const args = force ? ['--force'] : [];
  const raw = await runPython(DUMP_CATALOG, args, { timeoutMs: 120000 });
  const data = JSON.parse(raw);
  catalogCache = data;
  catalogAt = now;
  return data;
}

const NATIVE_STYLES = [
  {
    key: 'a3',
    name: '大字冲击',
    blurb: '一个词一个词砸进画面，跟着节拍；适合金句、情绪爆发。',
    fields: ['words', 'beat', 'voice', 'bgm', 'sfx'],
  },
  {
    key: 'title',
    name: '标题卡',
    blurb: '渐变背景 + 大标题 + 副标题 + 逐条要点；适合通告、开屏。',
    fields: ['title', 'subtitle', 'words', 'voice', 'bgm', 'sfx'],
  },
  {
    key: 'quote',
    name: '竖排金句',
    blurb: '竖排中文逐字淡入 + 红章印章；适合语录、书摘、台词。',
    fields: ['title', 'subtitle', 'voice', 'bgm', 'sfx'],
  },
  {
    key: 'explain',
    name: '讲解片',
    blurb: '一屏一个知识点：标签 + 大标题 + 说明 + 表情包 + 进度条；适合教程、功能导览。',
    fields: ['words', 'voice', 'bgm', 'sfx'],
  },
];

/** Edge 在线音色（edge-tts 自带，不需要 key，只需要联网）。 */
const BUILTIN_VOICES = [
  { id: 'zh-CN-XiaoxiaoNeural', name: '晓晓（暖女声，默认）' },
  { id: 'zh-CN-XiaoyiNeural', name: '晓伊（甜一点）' },
  { id: 'zh-CN-YunxiNeural', name: '云希（清朗男声）' },
  { id: 'zh-CN-YunyangNeural', name: '云扬（播音男声）' },
  { id: 'zh-CN-YunjianNeural', name: '云健（低沉男声）' },
  { id: 'zh-CN-YunxiaNeural', name: '云夏（少年音）' },
  { id: 'zh-CN-liaoning-XiaobeiNeural', name: '晓北（东北话）' },
  { id: 'zh-CN-shaanxi-XiaoniNeural', name: '晓妮（陕西话）' },
  { id: 'zh-TW-HsiaoChenNeural', name: '晓臻（台湾腔）' },
  { id: 'zh-HK-HiuMaanNeural', name: '曉曼（粤语）' },
];

/** 用户自训练音色：配置里 voices[] 直接写 {id,name}；需要本机有 GPT-SoVITS 才能出声。 */
function configuredVoices() {
  const list = Array.isArray(CONFIG.voices) ? CONFIG.voices : [];
  return list
    .filter((item) => item && typeof item.id === 'string' && item.id.trim())
    .map((item) => ({ id: item.id.trim(), name: String(item.name || item.id), custom: true }));
}

const VOICES = [...BUILTIN_VOICES, ...configuredVoices()];


// ---------------------------------------------------------------- jobs

function publicJob(job) {
  return {
    id: job.id,
    mode: job.mode,
    state: job.state,
    stage: job.stage,
    pct: job.pct,
    title: job.title,
    style: job.style,
    out: job.out,
    error: job.error,
    warnings: job.warnings,
    seconds: job.seconds,
    duration: job.duration,
    size: job.size,
    width: job.width,
    height: job.height,
    hasAudio: job.hasAudio,
    createdAt: job.createdAt,
    finishedAt: job.finishedAt,
    log: job.log.slice(-12),
  };
}

function revealInFolder(target) {
  try {
    if (process.platform === 'win32') {
      spawn('explorer.exe', ['/select,', target], { detached: true, stdio: 'ignore', windowsHide: false }).unref();
    } else if (process.platform === 'darwin') {
      spawn('open', ['-R', target], { detached: true, stdio: 'ignore' }).unref();
    } else {
      spawn('xdg-open', [dirname(target)], { detached: true, stdio: 'ignore' }).unref();
    }
  } catch {
    /* 打不开就算了，前端还有「复制路径」 */
  }
}

function mediaUrl(out) {
  return `ai-video/media?path=${encodeURIComponent(out)}`;
}

function finish(job) {
  job.finishedAt = Date.now();
  if (job.state === 'running') job.state = 'failed';
  if (job.state === 'done') job.mediaUrl = mediaUrl(job.out);
}

function startJob(job, payload) {
  job.state = 'running';
  job.stage = '排队';
  job.pct = 0;
  const file = join(JOB_DIR, `${job.id}.json`);
  return writeFile(file, JSON.stringify(payload), 'utf8').then(
    () =>
      new Promise((resolvePromise) => {
        const child = spawn(PYTHON, [BUILD_JOB, file], {
          cwd: APPS,
          windowsHide: true,
          env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
        });
        job.child = child;
        let buffer = '';
        const handleLine = (line) => {
          const text = line.trim();
          if (!text) return;
          let msg;
          try {
            msg = JSON.parse(text);
          } catch {
            job.log.push(text.slice(0, 400));
            return;
          }
          if (msg.type === 'progress') {
            job.stage = msg.stage || job.stage;
            job.pct = Math.max(job.pct, Number(msg.pct) || 0);
          } else if (msg.type === 'log') {
            job.log.push(String(msg.text || '').slice(0, 400));
          } else if (msg.type === 'done') {
            job.state = 'done';
            job.pct = 1;
            job.stage = '完成';
            job.out = msg.out;
            job.mediaUrl = mediaUrl(msg.out);
            job.duration = msg.duration;
            job.size = msg.size;
            job.width = msg.width;
            job.height = msg.height;
            job.hasAudio = msg.has_audio;
            job.seconds = msg.seconds;
            job.style = msg.style || job.style;
            job.title = msg.title || job.title;
            job.warnings = msg.warnings || [];
          } else if (msg.type === 'error') {
            job.state = 'failed';
            job.error = msg.message || '未知错误';
            job.stage = '失败';
            if (msg.trace) job.log.push(String(msg.trace).slice(-800));
          }
        };
        child.stdout.on('data', (chunk) => {
          buffer += chunk.toString('utf8');
          const parts = buffer.split(/\r?\n/);
          buffer = parts.pop() || '';
          parts.forEach(handleLine);
        });
        child.stderr.on('data', (chunk) => {
          const text = chunk.toString('utf8').trim();
          if (text) job.log.push(text.slice(-600));
        });
        child.on('error', (error) => {
          job.state = 'failed';
          job.error = String(error);
          finish(job);
          resolvePromise();
        });
        child.on('close', () => {
          if (buffer.trim()) handleLine(buffer);
          finish(job);
          resolvePromise();
        });
      }),
  );
}

function enqueue(job, payload) {
  queue = queue.then(() => startJob(job, payload)).catch((error) => {
    job.state = 'failed';
    job.error = String(error);
    finish(job);
  });
  return queue;
}

function nextId() {
  return randomUUID().slice(0, 8);
}

/** Only files the Host itself produced may be streamed back. */
function insideVideoDir(path) {
  const full = resolve(path);
  const base = resolve(VIDEO_DIR);
  return full === base || full.startsWith(base + sep);
}

function insideStyleRoot(path) {
  const full = resolve(path);
  const base = resolve(STYLE_ROOT);
  return full === base || full.startsWith(base + sep);
}

function streamFile(req, res, file, contentType) {
  return stat(file).then((info) => {
    const total = info.size;
    const range = req.headers.range;
    if (range) {
      const match = /bytes=(\d*)-(\d*)/.exec(range);
      const start = match && match[1] ? Number(match[1]) : 0;
      const end = match && match[2] ? Number(match[2]) : total - 1;
      res.statusCode = 206;
      res.setHeader('content-range', `bytes ${start}-${end}/${total}`);
      res.setHeader('accept-ranges', 'bytes');
      res.setHeader('content-length', end - start + 1);
      res.setHeader('content-type', contentType);
      createReadStream(file, { start, end }).pipe(res);
      return;
    }
    res.statusCode = 200;
    res.setHeader('content-length', total);
    res.setHeader('accept-ranges', 'bytes');
    res.setHeader('content-type', contentType);
    res.setHeader('cache-control', 'public, max-age=600');
    createReadStream(file).pipe(res);
  });
}

/** Validate one job payload: anything the panel sends is untrusted. */
function validatePayload(body) {
  if (!body || typeof body !== 'object') throw new Error('请求体必须是 JSON 对象');
  const rawMode = body.mode === 'prompt' ? 'prompt' : body.mode === 'auto' ? 'auto' : 'native';
  const mode = rawMode;
  const num = (value, fallback, min, max) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return fallback;
    return Math.min(max, Math.max(min, n));
  };
  const str = (value, max = 400) => (typeof value === 'string' ? value.slice(0, max).trim() : '');

  const orientation = str(body.orientation, 12) === 'landscape' ? 'landscape' : 'portrait';
  const quality = str(body.quality, 8) === '720' ? 720 : 1080;
  const square = str(body.orientation, 12) === 'square';
  const longSide = quality;
  const [width, height] = square
    ? [longSide, longSide]
    : orientation === 'landscape'
      ? [Math.round((longSide * 16) / 9), longSide]
      : [longSide, Math.round((longSide * 16) / 9)];

  if (mode === 'auto') {
    const brief = str(body.brief, 600);
    if (!brief) throw new Error('没填主题或要求');
    const wanted = Array.isArray(body.contexts)
      ? body.contexts.filter((k) => typeof k === 'string').slice(0, 6)
      : [str(body.context, 12)];
    const hasWorkspace = wanted.includes('workspace');
    const hasPlugins = wanted.includes('plugins');
    const context = hasWorkspace && hasPlugins ? 'all' : hasWorkspace ? 'workspace' : hasPlugins ? 'plugins' : 'none';
    const duration = num(body.duration, 90, 8, 360);
    const styles = Array.isArray(body.styles)
      ? body.styles.filter((k) => typeof k === 'string' && k.trim()).slice(0, 4)
      : [];
    return [{
      mode,
      auto: {
        brief,
        duration,
        context,
        context_text: str(body.contextText, 40000),
        width,
        height,
        fps: 24,
        voice: str(body.voice, 80) || 'zh-CN-XiaoxiaoNeural',
        rate: '+6%',
        no_bgm: !body.bgmEnabled,
        bgm_volume: num(body.bgmVolume, 0.13, 0, 0.6),
        sfx_volume: body.sfxEnabled === false ? 0 : num(body.sfxVolume, 0.14, 0, 0.5),
        no_narration: !body.narrationEnabled,
        no_stickers: body.stickersEnabled === false,
        styles,
        extra: str(body.extra, 300),
      },
      meta: { brief: brief.slice(0, 80), duration, width, height, context },
    }];
  }

  if (mode === 'prompt') {
    const many = Array.isArray(body.styleKeys)
      ? body.styleKeys.filter((k) => typeof k === 'string' && k.trim()).slice(0, 12)
      : [];
    const keys = many.length ? many : [str(body.styleKey, 200)].filter(Boolean);
    if (!keys.length) throw new Error('没选风格提示词');
    const duration = num(body.duration, 8, 3, 60);
    return keys.map((styleKey) => ({
      mode,
      prompt: {
        style_key: styleKey,
        topic: str(body.topic, 300),
        extra: str(body.extra, 300),
        duration,
        width,
        height,
        fps: 24,
        voice: str(body.voice, 80) || 'zh-CN-XiaoxiaoNeural',
        narration: str(body.narration, 400),
        narration_disabled: !body.narrationEnabled,
        no_bgm: !body.bgmEnabled,
        bgm_volume: num(body.bgmVolume, 0.13, 0, 0.6),
      },
      meta: { styleKey, topic: str(body.topic, 80), duration, width, height },
    }));
  }

  const words = str(body.words, 300);
  if (!words) throw new Error('没填台词（用 | 分隔）');
  return [{
    mode,
    native: {
      style: str(body.style, 20) || 'a3',
      words,
      title: str(body.title, 120),
      subtitle: str(body.subtitle, 160),
      beat: num(body.beat, 0.55, 0.3, 1.4),
      width,
      height,
      fps: 24,
      voice: str(body.voice, 80) || 'zh-CN-XiaoxiaoNeural',
      no_audio: !body.voiceEnabled,
      no_bgm: !body.bgmEnabled,
      bgm_volume: num(body.bgmVolume, 0.16, 0, 0.6),
      no_sfx: !body.sfxEnabled,
      sfx_volume: num(body.sfxVolume, 0.14, 0, 0.5),
    },
    meta: { style: str(body.style, 20) || 'a3', words: words.slice(0, 60), width, height },
  }];
}

/** Route handlers. */
function register(ctx) {
  const effect = (label, path, handler) =>
    ctx.effect(() => ctx.webServer.register({ kind: 'exact', path, handler }), label);

  effect(`ai-video: GET ${PREFIX}/catalog`, `${PREFIX}/catalog`, async (req, res) => {
    if (rejected(ctx, req, res)) return;
    if (req.method !== 'GET') return sendMethodNotAllowed(res, 'GET');
    try {
      const url = new URL(String(req.url), 'http://localhost');
      const data = await catalog(url.searchParams.get('refresh') === '1');
      sendJson(res, 200, {
        ok: true,
        native: NATIVE_STYLES,
        voices: VOICES,
        categories: data.categories,
        count: data.count,
        styles: data.styles.map((s) => ({
          key: s.key,
          code: s.code,
          name: s.name,
          category: s.category,
          look: s.look,
          fit: s.fit,
          keywords: s.keywords,
          hasThumb: Boolean(s.hasThumb),
          prompt: s.prompt,
        })),
      });
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error && error.message ? error.message : error) });
    }
  });

  effect(`ai-video: GET ${PREFIX}/transcript`, `${PREFIX}/transcript`, async (req, res) => {
    if (rejected(ctx, req, res)) return;
    if (req.method !== 'GET') return sendMethodNotAllowed(res, 'GET');
    try {
      const transcript = await sessionTranscript();
      if (!transcript.ok) {
        sendJson(res, 200, { ok: false, error: transcript.error || '读不到会话日志' });
        return;
      }
      const url = new URL(String(req.url), 'http://localhost');
      const full = url.searchParams.get('full') === '1';
      sendJson(res, 200, {
        ok: true,
        sessionId: transcript.sessionId,
        messages: transcript.messages,
        chars: transcript.chars,
        preview: transcript.preview,
        updatedAt: transcript.updatedAt,
        // 默认不回全文（12k 字），调试/落盘时用 ?full=1 取完整片段。
        ...(full ? { text: transcript.text } : {}),
      });
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error && error.message ? error.message : error) });
    }
  });

  effect(`ai-video: POST ${PREFIX}/files`, `${PREFIX}/files`, async (req, res) => {
    if (rejected(ctx, req, res)) return;
    if (req.method !== 'POST') return sendMethodNotAllowed(res, 'POST');
    try {
      const text = await readBody(req, 64 * 1024);
      if (text === null) {
        sendJson(res, 413, { ok: false, error: '请求体太大' });
        return;
      }
      let body = {};
      try {
        body = text ? JSON.parse(text) : {};
      } catch {
        sendJson(res, 400, { ok: false, error: '请求体不是合法 JSON' });
        return;
      }
      const result = await readPathTexts(body.paths);
      sendJson(res, 200, { ok: true, entries: result.entries, files: result.files, chars: result.chars });
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error && error.message ? error.message : error) });
    }
  });

  effect(`ai-video: GET ${PREFIX}/thumb`, `${PREFIX}/thumb`, async (req, res) => {
    if (rejected(ctx, req, res)) return;
    if (req.method !== 'GET') return sendMethodNotAllowed(res, 'GET');
    try {
      const url = new URL(String(req.url), 'http://localhost');
      const key = url.searchParams.get('key') || '';
      const data = await catalog();
      const style = data.styles.find((s) => s.key === key);
      if (!style) {
        sendJson(res, 404, { ok: false, error: '没有这条风格' });
        return;
      }
      // dump_catalog 只回 hasThumb（绝对路径不出本机），这里按 key 自己拼路径：
      // key 是相对 风格提示词/ 的路径（如 "A-G_代码样片/A3_大字冲击"），缩略图是同名 .jpg。
      const thumbPath = resolve(STYLE_ROOT, `${style.key}.jpg`);
      if (!insideStyleRoot(thumbPath)) {
        sendJson(res, 403, { ok: false, error: '路径不允许' });
        return;
      }
      let ok = false;
      try {
        ok = (await stat(thumbPath)).isFile();
      } catch {
        ok = false;
      }
      if (!ok) {
        sendJson(res, 404, { ok: false, error: '这条风格没有静帧' });
        return;
      }
      res.setHeader('cache-control', 'public, max-age=86400');
      await streamFile(req, res, thumbPath, 'image/jpeg');
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error) });
    }
  });

  effect(`ai-video: POST ${PREFIX}/jobs`, `${PREFIX}/jobs`, async (req, res) => {
    if (rejected(ctx, req, res)) return;
    if (req.method === 'GET') {
      const list = [...jobs.values()].sort((a, b) => b.createdAt - a.createdAt).slice(0, 20);
      sendJson(res, 200, { ok: true, jobs: list.map(publicJob) });
      return;
    }
    if (req.method !== 'POST') return sendMethodNotAllowed(res, 'GET, POST');
    let text;
    try {
      text = await readBody(req);
    } catch {
      sendJson(res, 400, { ok: false, error: '请求体读不出来' });
      return;
    }
    if (text === null) {
      sendJson(res, 413, { ok: false, error: '请求体太大' });
      return;
    }
    let body;
    try {
      body = JSON.parse(text);
    } catch {
      sendJson(res, 400, { ok: false, error: '请求体不是合法 JSON' });
      return;
    }
    let payloads;
    try {
      // Context sources the browser cannot read are resolved here: the running
      // session's transcript and the files the operator picked.
      body.contextText = await resolveContextText(body);
      payloads = validatePayload(body);
    } catch (error) {
      sendJson(res, 400, { ok: false, error: String(error && error.message ? error.message : error) });
      return;
    }
    const created = [];
    for (const payload of payloads) {
      const styleShort = payload.meta.styleKey ? payload.meta.styleKey.split('/').pop() : '';
      const baseTitle = payload.meta.topic || payload.meta.words || payload.meta.brief || '';
      const job = {
        id: nextId(),
        mode: payload.mode,
        state: 'queued',
        stage: '排队',
        pct: 0,
        title: payload.mode === 'prompt' && styleShort ? `${baseTitle} · ${styleShort}` : baseTitle,
        style: payload.meta.styleKey || payload.meta.style || (payload.mode === 'auto' ? 'AI 自拟' : ''),
        log: [],
        warnings: [],
        createdAt: Date.now() + created.length,
      };
      jobs.set(job.id, job);
      created.push({ job, payload });
    }
    for (const item of created) {
      // 串行队列：一次只渲一条（两条 CDP/Edge 抢 GPU 与输出目录）。
      await enqueue(item.job, item.payload);
    }
    sendJson(res, 200, {
      ok: true,
      job: publicJob(created[0].job),
      jobs: created.map((item) => publicJob(item.job)),
    });
  });

  effect(`ai-video: GET ${PREFIX}/jobs/<id>`, `${PREFIX}/jobs/`, async (req, res) => {
    if (rejected(ctx, req, res)) return;
    if (req.method !== 'GET') return sendMethodNotAllowed(res, 'GET');
    const url = new URL(String(req.url), 'http://localhost');
    const id = url.pathname.slice(`${PREFIX}/jobs/`.length).replace(/^\//, '');
    const job = jobs.get(id);
    if (!job) {
      sendJson(res, 404, { ok: false, error: '没有这个任务' });
      return;
    }
    sendJson(res, 200, { ok: true, job: publicJob(job) });
  });

  effect(`ai-video: GET ${PREFIX}/media`, `${PREFIX}/media`, async (req, res) => {
    if (rejected(ctx, req, res)) return;
    if (req.method !== 'GET' && req.method !== 'HEAD') return sendMethodNotAllowed(res, 'GET');
    const url = new URL(String(req.url), 'http://localhost');
    const path = url.searchParams.get('path') || '';
    if (!path || !insideVideoDir(path)) {
      sendJson(res, 403, { ok: false, error: '只能读产出的视频目录' });
      return;
    }
    try {
      await streamFile(req, res, path, 'video/mp4');
    } catch {
      sendJson(res, 404, { ok: false, error: '文件不在了' });
    }
  });

  effect(`ai-video: POST ${PREFIX}/reveal`, `${PREFIX}/reveal`, async (req, res) => {
    if (rejected(ctx, req, res)) return;
    if (req.method !== 'POST') return sendMethodNotAllowed(res, 'POST');
    let body;
    try {
      const text = await readBody(req, 8192);
      body = text ? JSON.parse(text) : {};
    } catch {
      sendJson(res, 400, { ok: false, error: '请求体不是合法 JSON' });
      return;
    }
    const path = typeof body.path === 'string' ? body.path : '';
    if (!path || !insideVideoDir(path)) {
      sendJson(res, 403, { ok: false, error: '只能打开产出目录里的文件' });
      return;
    }
    try {
      await stat(path);
    } catch {
      sendJson(res, 404, { ok: false, error: '文件不在了' });
      return;
    }
    revealInFolder(normalize(path));
    sendJson(res, 200, { ok: true });
  });

  effect(`ai-video: GET ${PREFIX}/health`, `${PREFIX}/health`, async (req, res) => {
    if (rejected(ctx, req, res)) return;
    if (req.method !== 'GET') return sendMethodNotAllowed(res, 'GET');
    let pythonOk = false;
    try {
      await stat(PYTHON);
      await stat(BUILD_JOB);
      pythonOk = true;
    } catch {
      pythonOk = false;
    }
    const llm = CONFIG.llm || {};
    sendJson(res, 200, {
      ok: true,
      dataDir: DATA_DIR,
      python: PYTHON,
      pythonOk,
      buildJob: BUILD_JOB,
      videoDir: VIDEO_DIR,
      styleRoot: STYLE_ROOT,
      ffmpeg: FFMPEG,
      ffmpegOk: Boolean(FFMPEG),
      browser: BROWSER,
      browserOk: Boolean(BROWSER),
      powershell: POWERSHELL,
    renderer: NODE_RENDER && existsSync(NODE_RENDER) && process.execPath ? 'node' : 'powershell',
    node: process.execPath,
    nodeRenderer: NODE_RENDER,
      llmReady: Boolean((llm.baseUrl || process.env.VIDEOGEN_LLM_BASE) && (llm.apiKey || process.env.VIDEOGEN_LLM_KEY)),
      stickersDir: STICKER_DIR,
      bgm: BGM_FILE,
      jobs: jobs.size,
      queueBusy: [...jobs.values()].some((j) => j.state === 'running' || j.state === 'queued'),
    });
  });
}

export const name = 'dsh-ai-video';
export const inject = ['webServer', 'connection'];

export function apply(ctx) {
  for (const dir of [VIDEO_DIR, JOB_DIR, WORK_DIR]) {
    mkdir(dir, { recursive: true }).catch(() => {});
  }
  register(ctx);
}
