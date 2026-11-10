/**
 * Browser half of the video workshop: one full panel reached from the left
 * sidebar icon, plus the icon itself.
 *
 * The panel talks to the Host half over the routes it registers under
 * `ai-video/` (relative to the document, so the app works under a sub-path).
 * No Harness client package is imported: React comes from the module table and
 * every color is a `--dsw-alias-*` token.
 */
window.__ModuleLoader__.load({
  id: 'dsh-ai-video',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const { useState, useEffect, useRef, useMemo, useCallback } = React;

    // 面板可能挂在任意路由下（/ 或 /s/<id>），所以接口地址按 origin 拼绝对路径；
    // file:// 下 origin 是 "null"，退回相对路径（冒烟测试用）。
    const API = (function () {
      try {
        const origin = globalThis.location && globalThis.location.origin;
        if (origin && origin !== 'null' && /^https?:/i.test(origin)) return origin + '/ai-video';
      } catch (err) {}
      return 'ai-video';
    })();
    const PANEL_KEY = 'ai-video';

    const CSS = `
.vg-root { height: 100%; display: flex; flex-direction: column; min-height: 0;
  --vg-surface: color-mix(in srgb, var(--dsw-alias-bg-base) 90%, transparent);
  --vg-surface-1: color-mix(in srgb, var(--dsw-alias-bg-layer-1) 86%, transparent);
  --vg-surface-2: color-mix(in srgb, var(--dsw-alias-bg-layer-2) 88%, transparent);
  --vg-text-2: color-mix(in srgb, var(--dsw-alias-label-primary) 68%, transparent);
  color: var(--dsw-alias-label-primary); background: var(--vg-surface);
  backdrop-filter: blur(22px) saturate(1.08); -webkit-backdrop-filter: blur(22px) saturate(1.08);
  isolation: isolate; }
.vg-head { display: flex; align-items: flex-end; gap: 12px; padding: 18px 22px 12px; }
.vg-title { font-size: 17px; font-weight: 650; letter-spacing: .2px; }
.vg-sub { font-size: 12px; color: var(--vg-text-2); padding-bottom: 2px; }
.vg-head-right { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.vg-topbtn { display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 10px;
  border: 1px solid var(--dsw-alias-border-l1); border-radius: 999px; background: var(--dsw-alias-bg-l1);
  color: var(--vg-text-2); font-size: 12px; cursor: pointer; }
.vg-topbtn:hover { color: var(--dsw-alias-label-primary); border-color: var(--dsw-alias-brand-primary); }
.vg-topbtn-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--dsw-alias-brand-primary); }
.vg-tabs { display: flex; gap: 4px; padding: 0 22px; border-bottom: 1px solid var(--dsw-alias-border-l1);
  background: var(--vg-surface-1); }
.vg-tab { appearance: none; border: 0; background: transparent; cursor: pointer; padding: 8px 12px 9px;
  font-size: 13px; color: var(--vg-text-2); border-bottom: 2px solid transparent; }
.vg-tab[data-on="1"] { color: var(--dsw-alias-label-primary); border-bottom-color: var(--dsw-alias-brand-primary); }
.vg-body { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 0; }
@media (max-width: 980px) { .vg-body { grid-template-columns: minmax(0, 1fr); } .vg-side { border-left: 0 !important; } }
.vg-main { min-width: 0; overflow: auto; padding: 16px 22px 28px; }
.vg-side { min-width: 0; border-left: 1px solid var(--dsw-alias-border-l1);
  background: color-mix(in srgb, var(--dsw-alias-bg-layer-1) 82%, transparent);
  overflow: auto; padding: 16px 18px 24px; }
.vg-sec { margin-bottom: 18px; }
.vg-sec-title { font-size: 12px; font-weight: 600; color: var(--vg-text-2);
  text-transform: none; letter-spacing: .3px; margin: 0 0 8px; }
.vg-row { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 10px; }
.vg-field { display: flex; flex-direction: column; gap: 5px; min-width: 132px; flex: 1 1 132px; }
.vg-field > label { font-size: 12px; color: var(--vg-text-2); }
.vg-input, .vg-select, .vg-textarea {
  font: inherit; font-size: 13px; color: var(--dsw-alias-label-primary);
  background: var(--vg-surface-2); border: 1px solid var(--dsw-alias-border-l1);
  border-radius: 8px; padding: 7px 9px; outline: none; width: 100%; box-sizing: border-box; }
.vg-input:focus, .vg-select:focus, .vg-textarea:focus { border-color: var(--dsw-alias-brand-primary); }
.vg-textarea { min-height: 64px; resize: vertical; line-height: 1.5; font-family: inherit; }
.vg-hint { font-size: 11.5px; color: var(--vg-text-2); line-height: 1.6; }
.vg-range { width: 100%; accent-color: var(--dsw-alias-brand-primary); }
.vg-switch { display: inline-flex; align-items: center; gap: 7px; font-size: 12.5px;
  color: var(--dsw-alias-label-primary); cursor: pointer; user-select: none; }
.vg-switch input { accent-color: var(--dsw-alias-brand-primary); }
.vg-btn { font: inherit; font-size: 13px; cursor: pointer; border-radius: 8px; padding: 7px 13px;
  border: 1px solid var(--dsw-alias-border-l2); background: var(--vg-surface-2);
  color: var(--dsw-alias-label-primary); }
.vg-btn:hover { border-color: var(--dsw-alias-brand-primary); }
.vg-btn[data-kind="primary"] { background: var(--dsw-alias-brand-primary); border-color: var(--dsw-alias-brand-primary);
  color: var(--dsw-alias-bg-base); font-weight: 600; }
.vg-btn[data-kind="primary"]:disabled { opacity: .55; cursor: default; }
.vg-btn[data-kind="ghost"] { background: transparent; border-color: transparent;
  color: var(--vg-text-2); padding: 6px 8px; }
.vg-btn[data-kind="ghost"]:hover { color: var(--dsw-alias-label-primary); }
.vg-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.vg-chip { font-size: 12px; cursor: pointer; padding: 4px 10px; border-radius: 999px;
  border: 1px solid var(--dsw-alias-border-l1); color: var(--vg-text-2);
  background: transparent; }
.vg-chip[data-on="1"] { color: var(--dsw-alias-label-primary); border-color: var(--dsw-alias-brand-primary);
  background: var(--vg-surface-2); }
.vg-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(148px, 1fr)); gap: 10px; }
.vg-card { border: 1px solid var(--dsw-alias-border-l1); border-radius: 10px; overflow: hidden;
  cursor: pointer; background: var(--vg-surface-1); display: flex; flex-direction: column; }
.vg-card[data-on="1"] { border-color: var(--dsw-alias-brand-primary); box-shadow: 0 0 0 1px var(--dsw-alias-brand-primary) inset; }
.vg-card-thumb { width: 100%; aspect-ratio: 4 / 3; background: var(--vg-surface-2);
  display: flex; align-items: center; justify-content: center; overflow: hidden; position: relative; }
.vg-card-check { position: absolute; top: 6px; right: 6px; width: 20px; height: 20px; border-radius: 50%;
  background: var(--dsw-alias-brand-primary); color: var(--dsw-alias-bg-base); font-size: 12px;
  font-style: normal; font-weight: 700; display: flex; align-items: center; justify-content: center;
  box-shadow: 0 1px 4px rgba(0, 0, 0, .35); }
.vg-card[data-on="1"] { background: color-mix(in srgb, var(--dsw-alias-brand-primary) 8%, var(--vg-surface-1)); }
.vg-picked-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.vg-batch { margin-top: 10px; display: flex; flex-direction: column; gap: 5px; }
.vg-batch-row { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 8px;
  border: 1px solid var(--dsw-alias-border-l1); background: var(--vg-surface-1); cursor: pointer;
  text-align: left; color: inherit; font: inherit; }
.vg-batch-row[data-on="1"] { border-color: var(--dsw-alias-brand-primary); }
.vg-batch-row[data-s="failed"] { border-color: var(--dsw-alias-state-error-primary); }
.vg-batch-name { font-size: 12px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.vg-batch-st { font-size: 11px; color: var(--vg-text-2); flex: none; }
.vg-pill[data-on="1"] { color: var(--dsw-alias-label-primary); border-color: var(--dsw-alias-brand-primary); }
.vg-card-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.vg-card-thumb span { font-size: 20px; font-weight: 700; color: var(--vg-text-2); opacity: .5; }
.vg-card-body { padding: 7px 9px 9px; display: flex; flex-direction: column; gap: 2px; }
.vg-card-name { font-size: 12.5px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.vg-card-meta { font-size: 11px; color: var(--vg-text-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.vg-picked { border: 1px solid var(--dsw-alias-border-l1); border-radius: 10px; padding: 10px 12px;
  background: var(--vg-surface-1); margin-bottom: 12px; }
.vg-picked-name { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
.vg-bar { height: 6px; border-radius: 999px; background: var(--vg-surface-2); overflow: hidden; }
.vg-bar > i { display: block; height: 100%; background: var(--dsw-alias-brand-primary); transition: width .3s ease; }
.vg-kv { display: flex; justify-content: space-between; font-size: 12px; color: var(--vg-text-2);
  padding: 3px 0; gap: 10px; }
.vg-kv b { font-weight: 500; color: var(--dsw-alias-label-primary); text-align: right;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.vg-video { width: 100%; border-radius: 10px; background: #000; display: block; max-height: 460px; }
.vg-log { font-family: ui-monospace, Cascadia Mono, Consolas, monospace; font-size: 11px; line-height: 1.5;
  color: var(--vg-text-2); white-space: pre-wrap; word-break: break-word;
  max-height: 120px; overflow: auto; background: var(--vg-surface-2); border-radius: 8px; padding: 8px; }
.vg-err { color: var(--dsw-alias-state-error-primary); font-size: 12px; line-height: 1.55; white-space: pre-wrap; }
.vg-empty { font-size: 12px; color: var(--vg-text-2); padding: 14px 0; text-align: center; }
.vg-hist { display: flex; flex-direction: column; gap: 6px; }
.vg-hist-row { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 8px;
  border: 1px solid var(--dsw-alias-border-l1); cursor: pointer; background: transparent; text-align: left; }
.vg-hist-row:hover { background: var(--vg-surface-2); }
.vg-hist-name { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.vg-hist-dot { width: 7px; height: 7px; border-radius: 50%; flex: none; background: var(--dsw-alias-state-idle-primary); }
.vg-hist-dot[data-s="done"] { background: var(--dsw-alias-state-success-primary); }
.vg-hist-dot[data-s="failed"] { background: var(--dsw-alias-state-error-primary); }
.vg-hist-dot[data-s="running"] { background: var(--dsw-alias-brand-primary); }
.vg-pill { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--dsw-alias-border-l1);
  color: var(--vg-text-2); }
.vg-split { height: 1px; background: var(--dsw-alias-border-l1); margin: 14px 0; }
.vg-ctx-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.vg-ctx-files { margin-top: 8px; display: grid; gap: 6px; }
.vg-side-btn { width: 100%; display: flex; align-items: center; justify-content: center; height: 100%; }
.vg-side-btn svg { display: block; }
`;

    // ------------------------------------------------------------ helpers

    async function api(path, options) {
      const res = await fetch(`${API}/${path}`, {
        headers: { 'content-type': 'application/json' },
        credentials: 'same-origin',
        ...(options || {}),
      });
      let data = null;
      try {
        data = await res.json();
      } catch {
        data = null;
      }
      if (!res.ok || !data || data.ok === false) {
        const message = (data && data.error) || `请求失败（HTTP ${res.status}）`;
        throw new Error(message);
      }
      return data;
    }

    function fmtSize(bytes) {
      if (!bytes) return '—';
      if (bytes > 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
      return `${Math.round(bytes / 1024)} KB`;
    }

    function fmtTime(ts) {
      if (!ts) return '';
      const d = new Date(ts);
      const p = (n) => String(n).padStart(2, '0');
      return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
    }

    const SFX_LEVELS = [
      { id: 'off', name: '关', volume: 0 },
      { id: 'light', name: '轻', volume: 0.08 },
      { id: 'mid', name: '中', volume: 0.14 },
      { id: 'heavy', name: '重', volume: 0.2 },
    ];

    const ORIENTATIONS = [
      { id: 'portrait', name: '竖屏 9:16' },
      { id: 'landscape', name: '横屏 16:9' },
      { id: 'square', name: '方形 1:1' },
    ];

    // ------------------------------------------------------------ icons

    function PanelIcon() {
      return h(
        'svg',
        {
          viewBox: '0 0 24 24',
          width: 18,
          height: 18,
          fill: 'none',
          stroke: 'currentColor',
          strokeWidth: 1.7,
          strokeLinecap: 'round',
          strokeLinejoin: 'round',
          'aria-hidden': true,
        },
        h('rect', { x: 2.5, y: 5.5, width: 19, height: 13, rx: 2.4 }),
        h('path', { d: 'M2.8 9.6h18.4' }),
        h('path', { d: 'M7 5.6 9.6 9.4M12 5.6 14.6 9.4M17 5.6 19.4 9.2' }),
        h('path', { d: 'M10.4 12.9v3.4l2.9-1.7z', fill: 'currentColor', stroke: 'none' }),
      );
    }

    /** Sidebar list entry: the icon the user clicks to open the panel. */
    function SidebarButton(props) {
      const active = Boolean(props && props.active);
      return h(
        'div',
        {
          className: 'vg-side-btn',
          style: { opacity: active ? 1 : 0.72, color: 'var(--dsw-alias-label-primary)' },
          title: 'AI速成视频',
        },
        h(PanelIcon),
      );
    }

    // 入口只保留会话视图里的「AI速成视频」页签。
    // 组件与样式先留着，将来想恢复侧栏入口只需把下面的注册加回来。
    void SidebarButton;
    void PanelIcon;

    // ------------------------------------------------------------ bits

    function Field(props) {
      return h(
        'div',
        { className: 'vg-field', style: props.style },
        h('label', null, props.label),
        props.children,
      );
    }

    function Switch(props) {
      return h(
        'label',
        { className: 'vg-switch' },
        h('input', { type: 'checkbox', checked: Boolean(props.checked), onChange: (e) => props.onChange(e.target.checked) }),
        props.label,
      );
    }

    function Progress(props) {
      const job = props.job;
      if (!job) return null;
      const pct = Math.round((job.pct || 0) * 100);
      const stateText =
        job.state === 'done'
          ? '已完成'
          : job.state === 'failed'
            ? '失败'
            : job.state === 'queued'
              ? '排队中'
              : job.stage || '渲染中';
      return h(
        'div',
        { className: 'vg-sec' },
        h('div', { className: 'vg-kv' }, h('span', null, stateText), h('b', null, `${pct}%`)),
        h('div', { className: 'vg-bar' }, h('i', { style: { width: `${pct}%` } })),
        job.error ? h('div', { className: 'vg-err', style: { marginTop: 8 } }, job.error) : null,
        job.warnings && job.warnings.length
          ? h('div', { className: 'vg-hint', style: { marginTop: 8 } }, job.warnings.join('；'))
          : null,
      );
    }

    // ------------------------------------------------------------ panel

    function Panel() {
      const [tab, setTab] = useState('prompt');
      const [lib, setLib] = useState(null);
      const [loadError, setLoadError] = useState('');
      const [query, setQuery] = useState('');
      const [category, setCategory] = useState('全部');
      const [visible, setVisible] = useState(120);
      const [styleKeys, setStyleKeys] = useState([]);
      const [autoStyleKeys, setAutoStyleKeys] = useState([]);
      const [autoStyleQuery, setAutoStyleQuery] = useState('');
      const [autoCategory, setAutoCategory] = useState('全部');
      const [autoVisible, setAutoVisible] = useState(60);
      const [stickersEnabled, setStickersEnabled] = useState(true);
      const [batch, setBatch] = useState([]);
      const [nativeStyle, setNativeStyle] = useState('a3');

      const [topic, setTopic] = useState('加班的意义');
      const [extra, setExtra] = useState('');
      const [duration, setDuration] = useState(8);
      const [narration, setNarration] = useState('');
      const [narrationEnabled, setNarrationEnabled] = useState(true);

      const [words, setWords] = useState('你|懂什么|听话');
      const [title, setTitle] = useState('');
      const [subtitle, setSubtitle] = useState('');
      const [beat, setBeat] = useState(0.55);
      const [voiceEnabled, setVoiceEnabled] = useState(true);

      const [orientation, setOrientation] = useState('portrait');
      const [quality, setQuality] = useState('1080');
      const [voice, setVoice] = useState('zh-CN-XiaoxiaoNeural');
      const [bgmEnabled, setBgmEnabled] = useState(true);
      const [bgmVolume, setBgmVolume] = useState(0.13);
      const [sfx, setSfx] = useState('mid');

      const [job, setJob] = useState(null);
      const [error, setError] = useState('');
      const [submitting, setSubmitting] = useState(false);
      const [history, setHistory] = useState([]);
      const [brief, setBrief] = useState('总结这个工作区的工作流程');
      // 参考材料是多选：工作区 / 插件清单交给 python 抓，另外两项由 Host 现取。
      const [contexts, setContexts] = useState(['workspace']);
      const [transcriptInfo, setTranscriptInfo] = useState(null);
      const [pathsText, setPathsText] = useState('');
      const [filesInfo, setFilesInfo] = useState(null);
      const [readingFiles, setReadingFiles] = useState(false);
      const [autoDuration, setAutoDuration] = useState(90);
      const [failedThumbs, setFailedThumbs] = useState({});
      const pollRef = useRef(null);

      useEffect(() => {
        let alive = true;
        api('catalog')
          .then((data) => {
            if (!alive) return;
            setLib(data);
            if (!styleKeys.length && data.styles && data.styles.length) {
              const first = data.styles[0];
              setStyleKeys([first.key]);
            }
          })
          .catch((e) => alive && setLoadError(String(e.message || e)));
        return () => {
          alive = false;
        };
      }, []);

      const refreshHistory = useCallback(() => {
        api('jobs')
          .then((data) => setHistory(data.jobs || []))
          .catch(() => {});
      }, []);

      useEffect(() => {
        refreshHistory();
      }, [refreshHistory]);

      // 勾了「本次对话」才去读：Host 那边是要解压 5MB 会话日志的。
      useEffect(() => {
        if (!contexts.includes('conversation')) {
          setTranscriptInfo(null);
          return undefined;
        }
        let alive = true;
        api('transcript')
          .then((data) => {
            if (!alive) return;
            setTranscriptInfo(data.ok ? data : { ok: false, error: data.error || '读不到会话日志' });
          })
          .catch((e) => alive && setTranscriptInfo({ ok: false, error: String(e.message || e) }));
        return () => {
          alive = false;
        };
      }, [contexts]);

      // 一批任务：任意一条还在排队/渲染就继续轮询，全部结束再刷历史。
      useEffect(() => {
        const live = batch.filter((j) => j.state === 'queued' || j.state === 'running');
        if (!live.length) return undefined;
        const timer = setInterval(() => {
          Promise.all(
            live.map((j) =>
              api(`jobs/${j.id}`)
                .then((data) => data.job)
                .catch(() => null),
            ),
          ).then((fresh) => {
            const map = {};
            fresh.forEach((j) => {
              if (j && j.id) map[j.id] = j;
            });
            if (!Object.keys(map).length) return;
            setBatch((prev) => prev.map((j) => map[j.id] || j));
            const first = fresh[0];
            if (first) setJob(first);
            const still = fresh.some((j) => j && (j.state === 'queued' || j.state === 'running'));
            if (!still) refreshHistory();
          });
        }, 1500);
        return () => clearInterval(timer);
      }, [batch, refreshHistory]);

      useEffect(() => {
        if (!job || !job.id) return undefined;
        if (batch.length > 1) return undefined; // 批量时有专门的轮询
        if (job.state === 'done' || job.state === 'failed') return undefined;
        const timer = setInterval(() => {
          api(`jobs/${job.id}`)
            .then((data) => {
              setJob(data.job);
              if (data.job.state === 'done' || data.job.state === 'failed') {
                refreshHistory();
              }
            })
            .catch(() => {});
        }, 1000);
        pollRef.current = timer;
        return () => clearInterval(timer);
      }, [job, batch.length, refreshHistory]);

      const styles = (lib && lib.styles) || [];
      const categories = useMemo(() => ['全部'].concat((lib && lib.categories) || []), [lib]);
      const shown = useMemo(() => {
        const q = query.trim().toLowerCase();
        return styles.filter((s) => {
          if (category !== '全部' && s.category !== category) return false;
          if (!q) return true;
          return (
            (s.name || '').toLowerCase().includes(q) ||
            (s.code || '').toLowerCase().includes(q) ||
            (s.keywords || '').toLowerCase().includes(q) ||
            (s.look || '').toLowerCase().includes(q)
          );
        });
      }, [styles, category, query]);
      const picked = styles.find((s) => s.key === styleKeys[0]) || null;
      const autoShown = useMemo(() => {
        const q = autoStyleQuery.trim().toLowerCase();
        const pool = styles.filter((s) => {
          if (autoCategory !== '全部' && s.category !== autoCategory) return false;
          if (!q) return true;
          return [s.name, s.code, s.category, s.look, s.keywords]
            .filter(Boolean)
            .some((v) => String(v).toLowerCase().includes(q));
        });
        const pickedFirst = autoStyleKeys
          .map((key) => styles.find((s) => s.key === key))
          .filter((s) => s && pool.includes(s));
        return pickedFirst.concat(pool.filter((s) => !autoStyleKeys.includes(s.key)));
      }, [styles, autoStyleQuery, autoCategory, autoStyleKeys]);

      // A new filter starts the list over instead of keeping a stale "show more".
      useEffect(() => {
        setVisible(120);
      }, [query, category]);
      useEffect(() => {
        setAutoVisible(60);
      }, [autoStyleQuery, autoCategory]);

      function toggleStyle(key) {
        setStyleKeys((prev) =>
          prev.includes(key) ? prev.filter((k) => k !== key) : prev.concat([key]),
        );
        if (lib && lib.styles) {
          const s = lib.styles.find((x) => x.key === key);
          if (s && !topic) setTopic(s.name);
        }
      }

      function toggleAutoStyle(key) {
        setAutoStyleKeys((prev) =>
          prev.includes(key) ? prev.filter((k) => k !== key) : prev.concat([key]).slice(-4),
        );
      }

      function toggleContext(key) {
        setContexts((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : prev.concat([key])));
      }

      /** 「读取并预览」：只拿条目数和字数，真正的正文由 Host 在建任务时拼进去。 */
      async function readFiles() {
        setReadingFiles(true);
        setFilesInfo(null);
        try {
          const paths = pathsText
            .split('\n')
            .map((line) => line.trim())
            .filter(Boolean);
          const data = await api('files', { method: 'POST', body: JSON.stringify({ paths }) });
          setFilesInfo(data);
        } catch (e) {
          setFilesInfo({ ok: false, error: String(e.message || e) });
        } finally {
          setReadingFiles(false);
        }
      }

      /**
       * 风格选择网格：短视频（片段）页签和「AI 自拟」的风格参考共用一套外观
       * （搜索框 + 分类 chips + 缩略图卡片 + 再显示），只是选中集合和过滤状态各自独立。
       */
      function renderStyleGrid({ list, selected, onToggle, keyPrefix, placeholder, compact }) {
        const q = keyPrefix === 'auto' ? autoStyleQuery : query;
        const setQ = keyPrefix === 'auto' ? setAutoStyleQuery : setQuery;
        const cat = keyPrefix === 'auto' ? autoCategory : category;
        const setCat = keyPrefix === 'auto' ? setAutoCategory : setCategory;
        const vis = keyPrefix === 'auto' ? autoVisible : visible;
        const setVis = keyPrefix === 'auto' ? setAutoVisible : setVisible;
        return h(
          'div',
          {
            className: 'vg-gridwrap',
            style: compact ? { maxHeight: 430, overflowY: 'auto', paddingRight: 4 } : null,
          },
          h(
            'div',
            { className: 'vg-row', style: { marginBottom: 8 } },
            h('input', {
              className: 'vg-input',
              placeholder,
              value: q,
              onChange: (e) => setQ(e.target.value),
              style: { flex: '1 1 220px' },
            }),
          ),
          h(
            'div',
            { className: 'vg-chips', style: { marginBottom: 10 } },
            categories.map((c) =>
              h(
                'button',
                {
                  key: `${keyPrefix}-${c}`,
                  className: 'vg-chip',
                  'data-on': cat === c ? '1' : '0',
                  onClick: () => setCat(c),
                },
                c,
              ),
            ),
          ),
          loadError
            ? h('div', { className: 'vg-err' }, loadError)
            : !lib
              ? h('div', { className: 'vg-empty' }, '正在读风格库…')
              : h(
                  'div',
                  { className: 'vg-gridbox' },
                  h(
                    'div',
                    { className: 'vg-grid' },
                    list.slice(0, vis).map((s) =>
                      h(
                        'div',
                        {
                          key: s.key,
                          className: 'vg-card',
                          'data-on': selected.includes(s.key) ? '1' : '0',
                          onClick: () => onToggle(s.key),
                          title: s.look || s.name,
                        },
                        h(
                          'div',
                          { className: 'vg-card-thumb' },
                          failedThumbs[s.key] || s.hasThumb === false
                            ? h('span', null, s.code)
                            : h('img', {
                                loading: 'lazy',
                                alt: s.name,
                                src: `${API}/thumb?key=${encodeURIComponent(s.key)}`,
                                onError: () =>
                                  setFailedThumbs((prev) => (prev[s.key] ? prev : { ...prev, [s.key]: 1 })),
                              }),
                          selected.includes(s.key)
                            ? h('i', { className: 'vg-card-check' }, '✓')
                            : null,
                        ),
                        h(
                          'div',
                          { className: 'vg-card-body' },
                          h('div', { className: 'vg-card-name' }, `${s.code} ${s.name}`),
                          h('div', { className: 'vg-card-meta' }, s.category),
                        ),
                      ),
                    ),
                  ),
                  list.length > vis
                    ? h(
                        'div',
                        { style: { display: 'flex', justifyContent: 'center', marginTop: 12 } },
                        h(
                          'button',
                          { className: 'vg-btn', onClick: () => setVis((n) => n + 120) },
                          `再显示 120 条（还有 ${list.length - vis} 条）`,
                        ),
                      )
                    : null,
                ),
        );
      }

      async function submit() {
        setError('');
        setSubmitting(true);
        try {
          const sfxLevel = SFX_LEVELS.find((s) => s.id === sfx) || SFX_LEVELS[2];
          // 第三个分支（mode: 'native'）是历史遗留：面板已去掉「内置模板」页签，
          // 只有直接调 API 的老客户端还会走到这里，Host 侧仍然支持 native 模式。
          const body =
            tab === 'auto'
              ? {
                  mode: 'auto',
                  brief,
                  contexts,
                  context:
                    contexts.includes('workspace') && contexts.includes('plugins')
                      ? 'all'
                      : contexts.includes('plugins')
                      ? 'plugins'
                      : contexts.includes('workspace')
                      ? 'workspace'
                      : 'none',
                  paths: pathsText
                    .split('\n')
                    .map((line) => line.trim())
                    .filter(Boolean),
                  duration: autoDuration,
                  narrationEnabled,
                  stickersEnabled,
                  styles: autoStyleKeys,
                  orientation,
                  quality,
                  voice,
                  bgmEnabled,
                  bgmVolume,
                  sfxEnabled: sfxLevel.volume > 0,
                  sfxVolume: sfxLevel.volume,
                  extra,
                }
              : tab === 'prompt'
              ? {
                  mode: 'prompt',
                  styleKeys,
                  // 兼容旧 Host：第一条仍然放在 styleKey 上。
                  styleKey: styleKeys[0] || '',
                  topic,
                  extra,
                  duration,
                  narration: narration || topic,
                  narrationEnabled,
                  orientation,
                  quality,
                  voice,
                  bgmEnabled,
                  bgmVolume,
                  sfxEnabled: sfxLevel.volume > 0,
                  sfxVolume: sfxLevel.volume,
                }
              : {
                  mode: 'native',
                  style: nativeStyle,
                  words,
                  title,
                  subtitle,
                  beat,
                  voiceEnabled,
                  orientation,
                  quality,
                  voice,
                  bgmEnabled,
                  bgmVolume,
                  sfxEnabled: sfxLevel.volume > 0,
                  sfxVolume: sfxLevel.volume,
                };
          const data = await api('jobs', { method: 'POST', body: JSON.stringify(body) });
          const created = (data.jobs && data.jobs.length ? data.jobs : [data.job]).filter(Boolean);
          setBatch(created);
          setJob(created[0] || null);
          refreshHistory();
        } catch (e) {
          setError(String((e && e.message) || e));
        } finally {
          setSubmitting(false);
        }
      }

      function openFolder() {
        if (!job || !job.out) return;
        api('reveal', { method: 'POST', body: JSON.stringify({ path: job.out }) }).catch((e) =>
          setError(String((e && e.message) || e)),
        );
      }

      function copyPath() {
        if (!job || !job.out) return;
        if (navigator.clipboard) navigator.clipboard.writeText(job.out).catch(() => {});
      }

      const busy =
        batch.some((item) => item.state === 'queued' || item.state === 'running') ||
        Boolean(job && (job.state === 'queued' || job.state === 'running'));

      const header = h(
        'div',
        { className: 'vg-head' },
        h('div', { className: 'vg-title' }, 'AI速成视频'),
        h('div', { className: 'vg-sub' }, lib ? `${lib.count} 条短视频风格` : '正在读风格库…'),
        h(
          'div',
          { className: 'vg-head-right' },
          h('span', { className: 'vg-pill' }, orientation === 'landscape' ? '横屏' : orientation === 'square' ? '方形' : '竖屏'),
          h('span', { className: 'vg-pill' }, `${quality}p`),
        ),
      );

      const tabs = h(
        'div',
        { className: 'vg-tabs' },
        h(
          'button',
          { className: 'vg-tab', 'data-on': tab === 'prompt' ? '1' : '0', onClick: () => setTab('prompt') },
          '短视频（片段）',
        ),
        h(
          'button',
          { className: 'vg-tab', 'data-on': tab === 'auto' ? '1' : '0', onClick: () => setTab('auto') },
          'AI 自拟（长片）',
        ),
      );

      const commonFields = h(
        'div',
        { className: 'vg-sec' },
        h('div', { className: 'vg-sec-title' }, '画幅与声音'),
        h(
          'div',
          { className: 'vg-row' },
          h(
            Field,
            { label: '画幅' },
            h(
              'select',
              { className: 'vg-select', value: orientation, onChange: (e) => setOrientation(e.target.value) },
              ORIENTATIONS.map((o) => h('option', { key: o.id, value: o.id }, o.name)),
            ),
          ),
          h(
            Field,
            { label: '清晰度' },
            h(
              'select',
              { className: 'vg-select', value: quality, onChange: (e) => setQuality(e.target.value) },
              h('option', { value: '1080' }, '1080p'),
              h('option', { value: '720' }, '720p（更快）'),
            ),
          ),
          h(
            Field,
            { label: '音色' },
            h(
              'select',
              { className: 'vg-select', value: voice, onChange: (e) => setVoice(e.target.value) },
              ((lib && lib.voices) || []).map((v) => h('option', { key: v.id, value: v.id }, v.name)),
            ),
          ),
        ),
        h(
          'div',
          { className: 'vg-row', style: { alignItems: 'center' } },
          h(Switch, { label: '配乐', checked: bgmEnabled, onChange: setBgmEnabled }),
          h(
            Field,
            { label: `配乐音量 ${Math.round(bgmVolume * 100)}%`, style: { minWidth: 180 } },
            h('input', {
              className: 'vg-range',
              type: 'range',
              min: 0,
              max: 0.4,
              step: 0.02,
              value: bgmVolume,
              onChange: (e) => setBgmVolume(Number(e.target.value)),
            }),
          ),
          h(
            Field,
            { label: '音效', style: { minWidth: 120 } },
            h(
              'select',
              { className: 'vg-select', value: sfx, onChange: (e) => setSfx(e.target.value) },
              SFX_LEVELS.map((s) => h('option', { key: s.id, value: s.id }, s.name)),
            ),
          ),
        ),
      );

      const promptPane = h(
        'div',
        null,
        h(
          'div',
          { className: 'vg-sec' },
          h(
            'div',
            { className: 'vg-sec-title', style: { display: 'flex', alignItems: 'center', gap: 8 } },
            h('span', null, `风格库（${shown.length} / ${styles.length}）`),
            h(
              'span',
              { className: 'vg-pill', 'data-on': styleKeys.length ? '1' : '0' },
              styleKeys.length ? `已选 ${styleKeys.length} 条` : '可多选',
            ),
            styleKeys.length
              ? h(
                  'button',
                  {
                    className: 'vg-btn',
                    'data-kind': 'ghost',
                    onClick: () => setStyleKeys([]),
                    style: { marginLeft: 'auto' },
                  },
                  '清空',
                )
              : null,
          ),
          renderStyleGrid({
            list: shown,
            selected: styleKeys,
            onToggle: toggleStyle,
            keyPrefix: 'prompt',
            placeholder: '搜风格：大字、漫画、中国风…',
          }),
        ),
        styleKeys.length
          ? h(
              'div',
              { className: 'vg-picked' },
              h(
                'div',
                { className: 'vg-picked-name' },
                `已选 ${styleKeys.length} 条风格：一次生成 ${styleKeys.length} 条视频（串行排队）`,
              ),
              h(
                'div',
                { className: 'vg-picked-list' },
                styleKeys.map((key) => {
                  const s = styles.find((x) => x.key === key);
                  return h(
                    'button',
                    {
                      key,
                      className: 'vg-chip',
                      'data-on': '1',
                      title: '点一下取消这条',
                      onClick: () => toggleStyle(key),
                    },
                    `${s ? `${s.code} ${s.name}` : key} ×`,
                  );
                }),
              ),
              picked
                ? h('div', { className: 'vg-hint', style: { marginTop: 8 } }, picked.look || picked.prompt || '')
                : null,
            )
          : h('div', { className: 'vg-picked' }, h('div', { className: 'vg-hint' }, '在下面的网格里点选风格，可以同时选多条，一次提交批量出片。')),
        h(
          'div',
          { className: 'vg-sec' },
          h('div', { className: 'vg-sec-title' }, '内容'),
          h(
            'div',
            { className: 'vg-row' },
            h(
              Field,
              { label: '主题 / 一句话（画面文案由 AI 按风格重写）', style: { flex: '1 1 100%' } },
              h('input', {
                className: 'vg-input',
                value: topic,
                onChange: (e) => setTopic(e.target.value),
                placeholder: '例如：周末别找我',
              }),
            ),
          ),
          h(
            'div',
            { className: 'vg-row' },
            h(
              Field,
              { label: '旁白（留空就用主题）', style: { flex: '1 1 100%' } },
              h('textarea', {
                className: 'vg-textarea',
                value: narration,
                onChange: (e) => setNarration(e.target.value),
                placeholder: '念出来的那句话',
              }),
            ),
          ),
          h(
            'div',
            { className: 'vg-row' },
            h(
              Field,
              { label: `时长 ${duration}s（风格模式建议 4–30s）`, style: { flex: '1 1 100%' } },
              h('input', {
                className: 'vg-range',
                type: 'range',
                min: 3,
                max: 60,
                step: 1,
                value: duration,
                onChange: (e) => setDuration(Number(e.target.value)),
              }),
            ),
          ),
          h(
            'div',
            { className: 'vg-row' },
            h(
              Field,
              { label: '额外要求（可选）', style: { flex: '1 1 100%' } },
              h('input', {
                className: 'vg-input',
                value: extra,
                onChange: (e) => setExtra(e.target.value),
                placeholder: '例如：结尾留一秒黑屏 / 字幕放底部',
              }),
            ),
          ),
          h(
            'div',
            { className: 'vg-row', style: { alignItems: 'center', marginTop: 4 } },
            h(Switch, {
              label: '要旁白配音',
              checked: narrationEnabled,
              onChange: setNarrationEnabled,
            }),
            h('span', { className: 'vg-hint' }, '短视频（片段）会先让 AI 按这条风格写一个单文件动画页面，再逐帧截图渲染。'),
          ),
        ),
        commonFields,
      );

      const nativePane = h(
        'div',
        null,
        h(
          'div',
          { className: 'vg-sec' },
          h('div', { className: 'vg-sec-title' }, '内置模板'),
          h(
            'div',
            { className: 'vg-chips', style: { marginBottom: 10 } },
            ((lib && lib.native) || []).map((s) =>
              h(
                'button',
                {
                  key: s.key,
                  className: 'vg-chip',
                  'data-on': nativeStyle === s.key ? '1' : '0',
                  onClick: () => setNativeStyle(s.key),
                },
                s.name,
              ),
            ),
          ),
          h(
            'div',
            { className: 'vg-hint' },
            (((lib && lib.native) || []).find((s) => s.key === nativeStyle) || {}).blurb || '',
          ),
        ),
        h(
          'div',
          { className: 'vg-sec' },
          h('div', { className: 'vg-sec-title' }, '内容'),
          h(
            'div',
            { className: 'vg-row' },
            h(
              Field,
              { label: '台词（用 | 分词）', style: { flex: '1 1 100%' } },
              h('textarea', {
                className: 'vg-textarea',
                value: words,
                onChange: (e) => setWords(e.target.value),
                placeholder: '你|懂什么|听话',
              }),
            ),
          ),
          nativeStyle === 'title' || nativeStyle === 'quote'
            ? h(
                'div',
                { className: 'vg-row' },
                h(Field, { label: '标题' }, h('input', {
                  className: 'vg-input',
                  value: title,
                  onChange: (e) => setTitle(e.target.value),
                })),
                h(Field, { label: '副标题' }, h('input', {
                  className: 'vg-input',
                  value: subtitle,
                  onChange: (e) => setSubtitle(e.target.value),
                })),
              )
            : null,
          nativeStyle === 'a3'
            ? h(
                'div',
                { className: 'vg-row' },
                h(
                  Field,
                  { label: `节拍 ${beat.toFixed(2)}s / 词`, style: { flex: '1 1 100%' } },
                  h('input', {
                    className: 'vg-range',
                    type: 'range',
                    min: 0.3,
                    max: 1.2,
                    step: 0.05,
                    value: beat,
                    onChange: (e) => setBeat(Number(e.target.value)),
                  }),
                ),
              )
            : null,
          h(
            'div',
            { className: 'vg-row', style: { alignItems: 'center', marginTop: 4 } },
            h(Switch, { label: '要配音', checked: voiceEnabled, onChange: setVoiceEnabled }),
            h(
              'span',
              { className: 'vg-hint' },
              nativeStyle === 'a3'
                ? `预计时长 ${(words.split('|').filter(Boolean).length * beat + 1.4).toFixed(1)}s`
                : '时长由台词和音效自动推算',
            ),
          ),
        ),
        commonFields,
      );

      const autoPane = h(
        'div',
        null,
        h(
          'div',
          { className: 'vg-sec' },
          h('div', { className: 'vg-sec-title' }, '你想看什么'),
          h(
            'div',
            { className: 'vg-row' },
            h(
              Field,
              { label: '只说一句要求，分镜、文案、配图、配音都交给 AI', style: { flex: '1 1 100%' } },
              h('textarea', {
                className: 'vg-textarea',
                value: brief,
                onChange: (e) => setBrief(e.target.value),
                placeholder: '例如：总结这个工作区的工作流程；或者：讲清楚我的 QQ 机器人有哪些插件功能',
                style: { minHeight: 92 },
              }),
            ),
          ),
          h(
            'div',
            { className: 'vg-row' },
            h(
              Field,
              { label: '给 AI 的参考材料（可多选）' },
              h(
                'div',
                { className: 'vg-ctx-chips' },
                [
                  { key: 'workspace', label: '工作区', hint: 'README + 目录树' },
                  { key: 'plugins', label: '插件清单', hint: '工作区里的插件 / 工具' },
                  { key: 'conversation', label: '本次对话', hint: '当前会话的问答记录' },
                  { key: 'files', label: '自选资料', hint: '指定文件或文件夹' },
                ].map((item) =>
                  h(
                    'button',
                    {
                      key: item.key,
                      className: 'vg-chip',
                      'data-on': contexts.includes(item.key) ? '1' : null,
                      title: item.hint,
                      onClick: () => toggleContext(item.key),
                    },
                    contexts.includes(item.key) ? `✓ ${item.label}` : item.label,
                  ),
                ),
              ),
              contexts.includes('conversation')
                ? h(
                    'div',
                    { className: 'vg-hint', style: { marginTop: 6 } },
                    transcriptInfo === null
                      ? '正在读取本次对话…'
                      : transcriptInfo.ok
                      ? `已取最近 ${transcriptInfo.messages} 条消息 / ${transcriptInfo.chars} 字`
                      : `读不到：${transcriptInfo.error}`,
                  )
                : null,
              contexts.includes('files')
                ? h(
                    'div',
                    { className: 'vg-ctx-files' },
                    h('textarea', {
                      className: 'vg-textarea',
                      value: pathsText,
                      onChange: (e) => setPathsText(e.target.value),
                      placeholder: '每行一个绝对路径，例如：\nD:\\docs\\requirements.md\nD:\\docs\\design',
                      style: { minHeight: 64 },
                    }),
                    h(
                      'div',
                      { className: 'vg-row', style: { alignItems: 'center', gap: 10, marginTop: 6 } },
                      h(
                        'button',
                        {
                          className: 'vg-btn vg-btn-ghost',
                          disabled: readingFiles || !pathsText.trim(),
                          onClick: readFiles,
                        },
                        readingFiles ? '读取中…' : '读取并预览',
                      ),
                      filesInfo
                        ? h(
                            'span',
                            { className: 'vg-hint' },
                            filesInfo.ok
                              ? `已读 ${filesInfo.files} 个文件 / ${filesInfo.chars} 字${
                                  filesInfo.entries && filesInfo.entries.length
                                    ? `（${filesInfo.entries
                                        .filter((e) => e.kind !== 'file' && e.kind !== 'dir')
                                        .map((e) => e.kind)
                                        .join(' / ')}）`
                                    : ''
                                }`
                              : `读不到：${filesInfo.error}`,
                          )
                        : h('span', { className: 'vg-hint' }, '目录会自动挑里面的 md/txt/json 等文本文件'),
                    ),
                  )
                : null,
            ),
            h(
              Field,
              { label: `时长 ${autoDuration}s ≈ ${Math.max(3, Math.round(autoDuration / 7.6))} 屏` },
              h('input', {
                className: 'vg-range',
                type: 'range',
                min: 15,
                max: 360,
                step: 5,
                value: autoDuration,
                onChange: (e) => setAutoDuration(Number(e.target.value)),
              }),
            ),
          ),
          h(
            'div',
            { className: 'vg-row', style: { alignItems: 'center', marginTop: 4 } },
            h(Switch, { label: '要旁白配音', checked: narrationEnabled, onChange: setNarrationEnabled }),
            h('span', { className: 'vg-hint' }, '按「一屏一个知识点」排：每屏讲一句话，自动配表情包、进度条和音效。'),
          ),
        ),
        h(
          'div',
          { className: 'vg-sec' },
          h('div', { className: 'vg-sec-title' }, '画面风格'),
          h(
            'div',
            { className: 'vg-hint' },
            'AI 自拟走内置「讲解片」版式（深色卡片 + 标签 + 大标题 + 表情包 + 进度条），不是 341 条短视频风格里的单页动画；想用那 341 条请切到「短视频（片段）」。',
          ),
          h(
            'div',
            { className: 'vg-row', style: { alignItems: 'center', marginTop: 10 } },
            h(Switch, { label: '加表情包', checked: stickersEnabled, onChange: setStickersEnabled }),
            h(
              'span',
              { className: 'vg-hint' },
              stickersEnabled ? '每屏由 AI 从 14 个猫娘表情里挑一个' : '纯文字版式，画面更干净',
            ),
          ),
        ),
        h(
          'div',
          { className: 'vg-sec' },
          h(
            'div',
            { className: 'vg-sec-title', style: { display: 'flex', alignItems: 'center', gap: 8 } },
            h('span', null, '风格参考（可选，最多 4 条）'),
            autoStyleKeys.length
              ? h('span', { className: 'vg-pill', 'data-on': '1' }, `已选 ${autoStyleKeys.length}`)
              : h('span', { className: 'vg-pill' }, '不选 = 全交给 AI'),
          ),
          h(
            'div',
            { className: 'vg-hint' },
            '选中后每一屏都会按这些风格现写页面再渲成画面（多选按屏轮换），画面由风格决定，代价是每屏多花 10–30 秒；不选则用内置讲解片模板，画面由模板决定。',
          ),
          autoStyleKeys.length
            ? h(
                'div',
                { className: 'vg-picked-list', style: { marginTop: 8 } },
                autoStyleKeys.map((key) => {
                  const s = styles.find((x) => x.key === key);
                  return h(
                    'button',
                    {
                      key,
                      className: 'vg-chip',
                      'data-on': '1',
                      title: '点一下取消这条',
                      onClick: () => toggleAutoStyle(key),
                    },
                    `${s ? `${s.code} ${s.name}` : key} ×`,
                  );
                }),
              )
            : null,
          renderStyleGrid({
            list: autoShown,
            selected: autoStyleKeys,
            onToggle: toggleAutoStyle,
            keyPrefix: 'auto',
            placeholder: '搜风格：中国风、大字、水彩…',
            compact: true,
          }),
        ),
        h(
          'div',
          { className: 'vg-sec' },
          h(
            'div',
            { className: 'vg-row', style: { marginTop: 8 } },
            h(
              Field,
              { label: '额外要求（可选）', style: { flex: '1 1 100%' } },
              h('input', {
                className: 'vg-input',
                value: extra,
                onChange: (e) => setExtra(e.target.value),
                placeholder: '例如：多讲代码里的那个任务队列 / 结尾给一句行动号召',
              }),
            ),
          ),
        ),
        commonFields,
      );

      const main = h(
        'div',
        { className: 'vg-root' },
        // Component-local stylesheet: unmounting the panel removes it, so the
        // plugin leaves nothing behind in the host document.
        h('style', { key: 'vg-css' }, CSS),
        header,
        tabs,
        h(
          'div',
          { className: 'vg-body' },
          h(
            'div',
            { className: 'vg-main' },
            tab === 'auto' ? autoPane : promptPane,
          ),
          h(
            'div',
            { className: 'vg-side' },
            h(
              'div',
              { className: 'vg-sec' },
              h('div', { className: 'vg-sec-title' }, '出片'),
              h(
                'div',
                { className: 'vg-row', style: { marginBottom: 10 } },
                h(
                  'button',
                  {
                    className: 'vg-btn',
                    'data-kind': 'primary',
                    disabled:
                      submitting ||
                      busy ||
                      (tab === 'prompt' && !styleKeys.length) ||
                      (tab === 'auto' && !brief.trim()),
                    onClick: submit,
                    style: { flex: '1 1 auto' },
                  },
                  busy ? '正在渲染…' : submitting ? '提交中…' : '开始生成',
                ),
                busy
                  ? h(
                      'button',
                      { className: 'vg-btn', 'data-kind': 'ghost', onClick: () => setJob(null) },
                      '收起',
                    )
                  : null,
              ),
              error ? h('div', { className: 'vg-err' }, error) : null,
              job ? h(Progress, { job }) : h('div', { className: 'vg-hint' }, '还没开始。选风格 → 填主题 → 点开始生成。'),
              batch.length > 1
                ? h(
                    'div',
                    { className: 'vg-batch' },
                    h(
                      'div',
                      { className: 'vg-sec-title', style: { marginTop: 10 } },
                      `本批 ${batch.length} 条（串行渲染）`,
                    ),
                    batch.map((item) => {
                      const pct = Math.round((item.pct || 0) * 100);
                      const st =
                        item.state === 'done'
                          ? '✓'
                          : item.state === 'failed'
                            ? '✕'
                            : item.state === 'queued'
                              ? '排队'
                              : `${pct}%`;
                      return h(
                        'button',
                        {
                          key: item.id,
                          className: 'vg-batch-row',
                          'data-s': item.state,
                          'data-on': job && job.id === item.id ? '1' : '0',
                          onClick: () => setJob(item),
                          title: item.error || item.out || '',
                        },
                        h('i', { className: 'vg-hist-dot', 'data-s': item.state }),
                        h('span', { className: 'vg-batch-name' }, item.title || item.id),
                        h('span', { className: 'vg-batch-st' }, st),
                      );
                    }),
                  )
                : null,
            ),
            job && job.state === 'done' && job.mediaUrl
              ? h(
                  'div',
                  { className: 'vg-sec' },
                  h('video', { className: 'vg-video', controls: true, src: job.mediaUrl, preload: 'metadata' }),
                  h(
                    'div',
                    { className: 'vg-kv', style: { marginTop: 8 } },
                    h('span', null, '规格'),
                    h('b', null, `${job.width}×${job.height} · ${job.duration}s · ${fmtSize(job.size)}`),
                  ),
                  h(
                    'div',
                    { className: 'vg-kv' },
                    h('span', null, '耗时'),
                    h('b', null, job.seconds ? `${job.seconds}s` : '—'),
                  ),
                  h(
                    'div',
                    { className: 'vg-row', style: { marginTop: 8 } },
                    h('button', { className: 'vg-btn', onClick: openFolder, style: { flex: '1 1 45%' } }, '打开文件夹'),
                    h('button', { className: 'vg-btn', onClick: copyPath, style: { flex: '1 1 45%' } }, '复制路径'),
                  ),
                )
              : null,
            job && job.log && job.log.length
              ? h(
                  'div',
                  { className: 'vg-sec' },
                  h('div', { className: 'vg-sec-title' }, '日志'),
                  h('div', { className: 'vg-log' }, job.log.join('\n')),
                )
              : null,
            h('div', { className: 'vg-split' }),
            h(
              'div',
              { className: 'vg-sec' },
              h(
                'div',
                { className: 'vg-sec-title', style: { display: 'flex', justifyContent: 'space-between' } },
                h('span', null, '本次会话的产出'),
                h(
                  'button',
                  { className: 'vg-btn', 'data-kind': 'ghost', onClick: refreshHistory },
                  '刷新',
                ),
              ),
              history.length
                ? h(
                    'div',
                    { className: 'vg-hist' },
                    history.slice(0, 8).map((row) =>
                      h(
                        'button',
                        {
                          key: row.id,
                          className: 'vg-hist-row',
                          onClick: () => setJob(row),
                          title: row.out || row.error || '',
                        },
                        h('i', { className: 'vg-hist-dot', 'data-s': row.state }),
                        h('span', { className: 'vg-hist-name' }, row.title || row.style || row.id),
                        h('span', { className: 'vg-hint' }, fmtTime(row.createdAt)),
                      ),
                    ),
                  )
                : h('div', { className: 'vg-empty' }, '还没有产出'),
            ),
          ),
        ),
      );
      return main;
    }

    return {
      inject: ['slots'],
      apply(ctx) {
        // 唯一入口：会话视图页签（和「对话 / 上下文 / 画廊 / 编队 / 统计」并排）。
        // 侧栏图标 + main 整页面板已下线，不再注册。
        ctx.slots.inject('conversation.view', () =>
          ctx.slots.register(
            { name: 'conversation.view', id: PANEL_KEY, order: 45, label: 'AI速成视频' },
            Panel,
          ),
        );
      },
    };
  },
});
