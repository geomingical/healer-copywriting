'use strict';
// Healer 文案面板 v2：專案首頁、唯讀總覽、路由（/p/<token>、?item=<商品 id>、舊版 /s/）。
(() => {
  const { API, TOKEN, LEGACY, UUID, SKU, STATUS_LABEL, $, el, HttpError, errorText, api, hooks, showBanner, hideBanner, toast, setSaveStatus, copyText, ask, showView, missingText, product } = window.HCP;
  const POLL_HOME = 5000;
  const POLL_HOME_HIDDEN = 20000;
  const MAX_PRODUCTS = 30;

  let PROJ = null;
  let view = 'loading';
  let pollTimer = null;
  let lastJson = '';
  const selected = new Set();

  function setView(name) { view = name; showView(name); }

  function missing(title, text) {
    stopPolling();
    $('missing-title').textContent = title;
    $('missing-text').textContent = text;
    setView('missing');
  }

  // ---------- 路由 ----------
  const itemFromUrl = () => {
    const id = new URLSearchParams(location.search).get('item');
    return id && UUID.test(id) ? id : null;
  };
  const projectUrl = () => (location.pathname.startsWith('/p/') ? location.pathname : `${location.pathname}?p=${TOKEN}`);
  const itemUrl = (id) => (location.pathname.startsWith('/p/') ? `${location.pathname}?item=${id}` : `${location.pathname}?p=${TOKEN}&item=${id}`);

  async function routeFromUrl() {
    if (!PROJ) return;
    if (PROJ.status === 'closed') {
      await product.leave(true);
      if (itemFromUrl()) history.replaceState(null, '', projectUrl());
      renderClosed();
      return;
    }
    const item = itemFromUrl();
    if (item) {
      stopPolling();
      setView('product');
      if (product.current !== item) await product.open(item, PROJ);
    } else {
      await product.leave(true);
      if (view !== 'home') $('home-missing').replaceChildren();
      renderHome();
      schedulePoll();
    }
  }
  const nav = {
    home(replace) {
      if (replace) history.replaceState(null, '', projectUrl());
      else if (itemFromUrl()) history.pushState(null, '', projectUrl());
      loadProject().then((ok) => { if (ok || view !== 'missing') routeFromUrl(); });
    },
    product(id) {
      history.pushState(null, '', itemUrl(id));
      routeFromUrl();
    },
  };
  product.setNav(nav);
  window.addEventListener('popstate', () => { routeFromUrl(); });

  // ---------- 載入專案 ----------
  async function loadProject(quiet) {
    try {
      const p = await api('GET', '');
      PROJ = p;
      product.setProject(p);
      if (view === 'home' || view === 'loading') hideBanner();
      return true;
    } catch (e) {
      if (e instanceof HttpError && e.status === 404) { missing('找不到這個專案連結', '連結可能打錯或已經過期。請回到聊天室跟小編要新的專案連結。'); return false; }
      if (!quiet) showBanner(`讀取失敗：${errorText(e)}`, start);
      return false;
    }
  }

  hooks.onClosed = async () => {
    if (await loadProject(true)) routeFromUrl();
  };

  // ---------- 專案首頁 ----------
  const products = () => [...((PROJ && PROJ.products) || [])].sort((a, b) => a.position - b.position);

  function renderHome() {
    setView('home');
    $('project-title').textContent = PROJ.title || '';
    $('project-title').hidden = !PROJ.title;
    const list = products();
    for (const id of [...selected]) {
      const p = list.find((x) => x.id === id);
      if (!p || p.status !== 'draft') selected.delete(id);
    }
    const sig = JSON.stringify(list) + [...selected].join();
    if (sig !== lastJson || !$('product-list').children.length) {
      lastJson = sig;
      $('product-list').replaceChildren(...list.map(productCard));
    }
    $('product-empty').hidden = list.length > 0;
    $('add-product').disabled = list.length >= MAX_PRODUCTS;
    updateSelected();
  }

  function productCard(p) {
    const draft = p.status === 'draft';
    const hot = p.status === 'ready';
    const box = draft
      ? el('label', { class: 'selbox' }, el('input', {
        type: 'checkbox', checked: selected.has(p.id), 'aria-label': `選取 ${p.sku}`,
        onchange: (e) => { if (e.target.checked) selected.add(p.id); else selected.delete(p.id); lastJson = ''; updateSelected(); },
      }))
      : el('span', { class: 'selbox off', 'aria-hidden': 'true' });
    return el('li', { class: `pcard${hot ? ' hot' : ''}`, 'data-id': p.id, 'data-sku': p.sku }, box,
      el('button', { type: 'button', class: 'pbody', onclick: () => nav.product(p.id) },
        el('span', { class: 'line1' }, el('strong', { class: 'sku', text: p.sku }),
          el('span', { class: `st st-${p.status}`, text: hot ? '✨ 可挑句' : STATUS_LABEL[p.status] || p.status })),
        el('span', { class: 'line2' }, el('span', { class: 'pname', text: p.name || '（還沒填名稱）' }),
          el('span', { class: 'pcount', text: `照片 ${p.photo_count || 0} 張${p.has_edited_final ? '・已貼修改版' : ''}` }))));
  }

  function updateSelected() {
    if (selected.size && !$('home-missing').querySelector('.miss-item')) $('home-missing').replaceChildren();
    $('submit-selected').textContent = `送出選取的商品（${selected.size}）`;
  }

  $('submit-selected').addEventListener('click', submitSelected);
  async function submitSelected() {
    const box = $('home-missing');
    box.replaceChildren();
    if (!selected.size) { box.textContent = '先勾選要送出的草稿商品（至少 1 個）'; return; }
    const ids = [...selected];
    const btn = $('submit-selected');
    btn.disabled = true;
    btn.textContent = '送出中…';
    try {
      const r = await api('POST', '/submit', { product_ids: ids });
      const n = ((r && r.submitted) || []).length + ((r && r.already) || []).length;
      for (const id of ids) selected.delete(id);
      hideBanner();
      toast(`已送出 ${n || ids.length} 個商品，小編會開始寫文案`);
    } catch (e) {
      if (e.closed) return;
      if (e instanceof HttpError && e.status === 400 && e.body.error === 'incomplete') {
        const sku = (id) => ((products().find((p) => p.id === id) || {}).sku);
        box.replaceChildren(el('p', { text: '這些商品還沒填完，全部都還沒送出：' }),
          ...(e.body.items || []).map((it) => el('p', { class: 'miss-item' },
            el('span', { text: `「${it.sku || sku(it.id) || '?'}」還缺：${missingText(it.missing)}` }),
            el('button', { type: 'button', class: 'btn small', text: '去補', 'aria-label': `去補 ${it.sku || ''}`, onclick: () => nav.product(it.id) }))));
      } else if (e instanceof HttpError && (e.status === 409 || e.status === 400)) {
        box.textContent = e.message;
      } else showBanner(`送出失敗：${errorText(e)}。勾選的商品還在，按重試再送一次。`, submitSelected);
    } finally {
      btn.disabled = false;
      await loadProject(true);
      if (view === 'home') renderHome();
    }
  }

  // 新增商品
  $('add-product').addEventListener('click', () => {
    $('add-sku').value = '';
    $('add-error').textContent = '';
    $('add-dialog').showModal();
    $('add-sku').focus();
  });
  $('add-cancel').addEventListener('click', () => $('add-dialog').close());
  $('add-sku').addEventListener('input', () => { $('add-error').textContent = ''; });
  $('add-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const sku = $('add-sku').value.trim();
    const err = $('add-error');
    if (!sku) { err.textContent = '請輸入商品編號'; return; }
    if (sku.length > 40) { err.textContent = '商品編號最多 40 字'; return; }
    if (!SKU.test(sku)) { err.textContent = '只能用英文、數字、中文或 - _ .，不能有空白或斜線'; return; }
    if (products().some((p) => p.sku === sku)) { err.textContent = '這個商品編號在這期專案裡已經有了'; return; }
    $('add-ok').disabled = true;
    try {
      const r = await api('POST', '/products', { sku });
      $('add-dialog').close();
      toast(`已新增「${r.sku || sku}」`);
      await loadProject(true);
      nav.product(r.id);
    } catch (ex) {
      if (ex.closed) { $('add-dialog').close(); return; }
      if (ex instanceof HttpError && ex.body.error === 'duplicate_sku') err.textContent = '這個商品編號在這期專案裡已經有了';
      else err.textContent = ex instanceof HttpError ? ex.message : `新增失敗：${errorText(ex)}`;
    } finally {
      $('add-ok').disabled = false;
    }
  });

  // 結束這期專案
  $('close-project').addEventListener('click', async () => {
    const list = products();
    const notFinal = list.filter((p) => p.status !== 'finalized');
    const drafts = notFinal.filter((p) => p.status === 'draft').length;
    const extra = notFinal.length
      ? `提醒：還有 ${notFinal.length} 個商品尚未定稿${drafts ? `（其中 ${drafts} 個是草稿）` : ''}。`
      : `${list.length} 個商品都已定稿。`;
    const ok = await ask({
      title: '結束這期專案？',
      body: '結束後這個網址會變成唯讀，不能再新增或修改。確定要結束嗎？',
      extra, yes: '結束專案', danger: true,
    });
    if (!ok) return;
    try {
      await api('POST', '/close', { confirm: true });
      toast('這期專案已結束');
    } catch (e) {
      if (!e.closed) { showBanner(`結束失敗：${errorText(e)}`); return; }
    }
    if (await loadProject()) routeFromUrl();
  });

  function schedulePoll() {
    stopPolling();
    pollTimer = setTimeout(async () => {
      if (view !== 'home') return;
      if (await loadProject(true) && view === 'home') {
        if (PROJ.status === 'closed') renderClosed(); else renderHome();
      }
      if (view === 'home') schedulePoll();
    }, document.hidden ? POLL_HOME_HIDDEN : POLL_HOME);
  }
  function stopPolling() { clearTimeout(pollTimer); }
  document.addEventListener('visibilitychange', () => { if (view === 'home') schedulePoll(); });

  // ---------- 唯讀總覽 ----------
  function renderClosed() {
    stopPolling();
    setSaveStatus('');
    setView('closed');
    const when = PROJ.closed_at ? new Date(PROJ.closed_at) : null;
    $('closed-meta').textContent = [PROJ.title, when && !Number.isNaN(when.getTime()) ? `結束於 ${when.toLocaleDateString('zh-TW')}` : ''].filter(Boolean).join('・');
    const list = products();
    $('closed-list').replaceChildren(...(list.length ? list.map((p) => {
      const edited = typeof p.edited_final === 'string' && p.edited_final.trim() ? p.edited_final : null;
      const text = edited || p.final_text || '';
      const source = edited ? '我修改後的最終文案' : p.final_text ? '定稿全文（沒有修改後的版本）' : `沒有定稿（${STATUS_LABEL[p.status] || p.status}）`;
      return el('li', { class: 'card ccard', 'data-sku': p.sku },
        el('p', { class: 'line1' }, el('strong', { class: 'sku', text: p.sku }), el('span', { class: 'pname', text: p.name ? `　${p.name}` : '' })),
        el('p', { class: `source${edited ? ' mine' : ''}`, text: source }),
        text ? el('pre', { class: 'final-text', text }) : null,
        text ? el('button', { type: 'button', class: 'btn primary wide', text: '複製', 'aria-label': `複製 ${p.sku} 的文案`, onclick: () => copyText(text) }) : null);
    }) : [el('li', { class: 'muted center', text: '這期專案沒有商品。' })]));
  }

  // ---------- 開始 ----------
  async function start() {
    hideBanner();
    if (LEGACY) return missing('這是舊版連結', '這是舊版連結，請回聊天室跟小編要新的專案連結。');
    if (!TOKEN) return missing('找不到這個專案連結', '網址裡沒有專案代碼。請回到聊天室跟小編要專案連結。');
    if (!API) { setView('loading'); showBanner('面板設定有誤（config.js 沒有 API 網址），請通知小編'); return; }
    setView('loading');
    if (await loadProject()) routeFromUrl();
  }
  start();
})();
