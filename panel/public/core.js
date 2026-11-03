'use strict';
// Healer 文案面板 v2：共用小工具（API、提示、複製、確認對話框）。掛在 window.HCP。
(() => {
  const API = String((window.PANEL_CONFIG && window.PANEL_CONFIG.api) || '').replace(/\/+$/, '');
  const $ = (id) => document.getElementById(id);
  const okToken = (t) => (t && /^[A-Za-z0-9_-]{16,128}$/.test(t) ? t : null);
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const SKU = /^[A-Za-z0-9._\-\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}ー々]+$/u;
  const STATUS_LABEL = { draft: '草稿', submitted: '已送出', generating: '撰寫中', ready: '可挑句', finalized: '已定稿' };
  const MISSING_LABEL = { sku: '商品編號', name: '商品名稱', category: '類別', colors: '色名', material: '材質', photos: '照片', status: '狀態' };

  function el(tag, props, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else if (k === 'text') n.textContent = v;
      else if (k in n && !k.includes('-')) n[k] = v;
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids.flat()) if (c != null && c !== false) n.append(c);
    return n;
  }
  const store = {
    get(key) { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } },
    set(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* 私密模式等，忽略 */ } },
    del(key) { try { localStorage.removeItem(key); } catch { /* ignore */ } },
  };

  // 網址：/p/<token>（?item=<商品 id>）；舊版 /s/<token>
  const path = location.pathname;
  const pm = path.match(/^\/p\/([^/]+)\/?$/);
  const TOKEN = okToken(pm && pm[1]) || (path === '/' || path === '/index.html' ? okToken(new URLSearchParams(location.search).get('p')) : null);
  const LEGACY = !TOKEN && (/^\/s\/[^/]+\/?$/.test(path) || new URLSearchParams(location.search).has('s'));

  class HttpError extends Error {
    constructor(status, body) {
      super((body && body.message) || `伺服器錯誤（${status}）`);
      this.status = status;
      this.body = body || {};
    }
    get closed() { return this.status === 409 && this.body.error === 'project_closed'; }
  }
  const errorText = (e) => (e instanceof HttpError ? e.message : '網路連不上，請檢查網路');

  const hooks = { onClosed: null };
  async function api(method, sub, body, opts) {
    const init = { method, cache: 'no-store', headers: {} };
    if (opts && opts.keepalive) init.keepalive = true;
    if (body !== undefined) {
      init.headers['content-type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    const res = await fetch(`${API}/p/${TOKEN}${sub}`, init);
    let data = null;
    try { data = await res.json(); } catch { /* 非 JSON */ }
    if (!res.ok) {
      const err = new HttpError(res.status, data);
      if (err.closed && hooks.onClosed) hooks.onClosed();
      throw err;
    }
    return data;
  }

  // ---------- 提示 ----------
  let bannerRetry = null;
  function showBanner(text, retry) {
    $('banner-text').textContent = text;
    bannerRetry = retry || null;
    $('banner-retry').hidden = !retry;
    $('banner').hidden = false;
  }
  function hideBanner() { $('banner').hidden = true; bannerRetry = null; }
  $('banner-retry').addEventListener('click', () => { const fn = bannerRetry; hideBanner(); if (fn) fn(); });

  let toastTimer = null;
  function toast(text) {
    const t = $('toast');
    t.textContent = text;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 2600);
  }
  const setSaveStatus = (text) => { $('save-status').textContent = text; };

  async function copyText(text) {
    if (!text) return toast('沒有可以複製的文字');
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = el('textarea', { value: text, readOnly: true, style: 'position:fixed;top:-1000px' });
      document.body.append(ta);
      ta.select();
      const ok = document.execCommand && document.execCommand('copy');
      ta.remove();
      if (!ok) return toast('無法自動複製，請長按文字手動複製');
    }
    toast('已複製');
  }

  // 共用確認對話框：回傳 Promise<boolean>
  let askResolve = null;
  function ask({ title, body, extra, yes, danger }) {
    const d = $('confirm');
    $('confirm-title').textContent = title;
    $('confirm-body').textContent = body || '';
    $('confirm-extra').textContent = extra || '';
    $('confirm-extra').hidden = !extra;
    $('confirm-yes').textContent = yes || '確定';
    $('confirm-yes').className = `btn ${danger ? 'danger solid' : 'primary'}`;
    if (askResolve) askResolve(false);
    d.showModal();
    return new Promise((resolve) => { askResolve = resolve; });
  }
  function settle(v) {
    const r = askResolve;
    askResolve = null;
    if ($('confirm').open) $('confirm').close();
    if (r) r(v);
  }
  $('confirm-no').addEventListener('click', () => settle(false));
  $('confirm-yes').addEventListener('click', () => settle(true));
  $('confirm').addEventListener('close', () => settle(false));

  function showView(name) {
    for (const v of document.querySelectorAll('.view')) v.hidden = v.id !== `view-${name}`;
  }
  function missingText(list) {
    return (Array.isArray(list) ? list : []).map((m) => MISSING_LABEL[m] || String(m)).join('、');
  }

  window.HCP = {
    API, TOKEN, LEGACY, UUID, SKU, STATUS_LABEL, $, el, store, HttpError, errorText, api, hooks,
    showBanner, hideBanner, toast, setSaveStatus, copyText, ask, showView, missingText,
  };
})();
