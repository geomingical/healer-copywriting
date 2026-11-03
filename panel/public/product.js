'use strict';
// Healer 文案面板 v2：單一商品畫面（填寫 → 等待 → 挑句子 → 定稿＋我修改後的最終文案）。
(() => {
  const { $, el, store, HttpError, errorText, api, showBanner, hideBanner, toast, setSaveStatus, copyText, ask, showView, missingText, STATUS_LABEL, SKU } = window.HCP;
  const ROUTE_NAMES = { A: 'A 心動開場', B: 'B 賣點直擊', C: 'C 穿搭情境' };
  const LONG_SIDE = 1600;
  const JPEG_QUALITY = 0.82;
  const MAX_PHOTOS = 20;
  const FIELDS_DEBOUNCE = 800;
  const SKU_DEBOUNCE = 800;
  const EDITED_DEBOUNCE = 800;
  const EDITED_MAX = 5000;
  const STATE_DEBOUNCE = 600;
  const POLL_FAST = 3000;
  const POLL_SLOW = 15000;
  const FIELD_NAMES = ['name', 'category', 'material', 'sizes', 'price', 'background', 'feelings'];

  let PROJ = null; // 專案（取 options.categories）
  let P = null; // 目前商品的所有本機狀態；換商品就整個換掉
  let nav = { home() {} };

  function newCtx(pid) {
    return {
      pid, alive: true, S: null, pane: null,
      F: null, items: [], formReady: false, triedSubmit: false, fieldsTimer: null, skuTimer: null, skuSent: null,
      ST: null, rev: 0, tab: 'A', stateDirty: false, stateTimer: null, stateQueued: null, pickReadyRev: null,
      editedTimer: null, editedDirty: false, editedValue: '', editedQueued: null, pollTimer: null, waitTicker: null,
      writes: Promise.resolve(), // 這個商品的寫入佇列：所有寫入請求依序執行，前一個結束才送下一個
    };
  }
  const papi = (c, method, sub, body, opts) => api(method, `/products/${c.pid}${sub}`, body, opts);
  // 排進這個商品的寫入佇列；回傳 job 的結果（job 失敗不會卡住後面的寫入）
  function enqueue(c, job) {
    const run = c.writes.then(job);
    c.writes = run.then(() => {}, () => {});
    return run;
  }
  const settled = (c) => !c.stateDirty && !c.stateTimer && !c.editedDirty && !c.editedTimer && !c.fieldsTimer && !c.skuTimer;
  const KEY = (c) => ({ state: `hcp-state-${c.pid}`, material: `hcp-material-${c.pid}`, submitted: `hcp-submitted-${c.pid}` });

  function showPane(name) {
    for (const p of document.querySelectorAll('#view-product .pane')) p.hidden = p.id !== `pane-${name}`;
    P.pane = name;
  }
  function renderHead() {
    const S = P.S;
    const name = String((P.F && P.F.name) || (S.fields && S.fields.name) || '').trim();
    $('product-head').replaceChildren(
      el('strong', { text: S.sku }), name ? el('span', { text: name }) : null,
      el('span', { class: `st st-${S.status}`, text: STATUS_LABEL[S.status] || S.status }));
  }

  // ---------- 載入與分流 ----------
  async function open(pid, proj) {
    if (P) await leave(true);
    PROJ = proj;
    P = newCtx(pid);
    setSaveStatus('');
    showView('product');
    for (const p of document.querySelectorAll('#view-product .pane')) p.hidden = true;
    $('product-head').textContent = '載入中…';
    window.scrollTo(0, 0);
    await load();
  }
  function setProject(proj) { PROJ = proj; }

  async function load(quiet) {
    const c = P;
    try {
      const S = await papi(c, 'GET', '');
      if (!c.alive) return;
      c.S = S;
      hideBanner();
      if (S.project_status === 'closed' && window.HCP.hooks.onClosed) { window.HCP.hooks.onClosed(); return; }
      route();
    } catch (e) {
      if (!c.alive || e.closed) return;
      if (e instanceof HttpError && (e.status === 404 || e.status === 400)) { toast('找不到這個商品'); nav.home(true); return; }
      if (!quiet) showBanner(`讀取失敗：${errorText(e)}`, () => load());
      if (c.pane === 'wait' || !c.S) schedulePoll();
    }
  }

  function route() {
    const st = P.S.status;
    renderHead();
    if (st === 'draft') { stopPolling(); renderForm(); }
    else if (st === 'submitted' || st === 'generating' || !P.S.result) { renderWait(); schedulePoll(); }
    else { stopPolling(); renderPick(); }
  }

  // ---------- 1. 填寫 ----------
  function renderForm() {
    showPane('form');
    if (P.formReady) return;
    P.formReady = true;
    const S = P.S;
    const f = S.fields || {};
    P.F = { colors: Array.isArray(f.colors) ? f.colors.slice() : [] };
    for (const k of FIELD_NAMES) P.F[k] = typeof f[k] === 'string' ? f[k] : '';
    const form = $('fields-form');
    const select = form.elements.category;
    select.replaceChildren(el('option', { value: '', text: '請選擇' }));
    for (const cat of (PROJ && PROJ.options && PROJ.options.categories) || []) select.append(el('option', { value: cat, text: cat }));
    if (P.F.category && ![...select.options].some((o) => o.value === P.F.category)) P.F.category = '';
    for (const k of FIELD_NAMES) form.elements[k].value = P.F[k];
    $('sku-input').value = S.sku;
    $('sku-error').textContent = '';
    $('color-input').value = '';
    $('form-missing').textContent = '';
    P.items = [...(S.photos || [])].sort((a, b) => a.order - b.order)
      .map((p) => ({ key: p.path, path: p.path, name: p.name, url: p.url, status: 'done', progress: 1 }));
    renderColors();
    renderPhotos();
  }

  $('fields-form').addEventListener('submit', (e) => e.preventDefault());
  $('fields-form').addEventListener('input', (e) => {
    const name = e.target.name;
    if (!FIELD_NAMES.includes(name) || !P || !P.F) return;
    P.F[name] = e.target.value;
    if (name === 'name') renderHead();
    scheduleFieldsSave();
  });

  function scheduleFieldsSave() {
    setSaveStatus('尚未儲存');
    clearTimeout(P.fieldsTimer);
    const c = P;
    P.fieldsTimer = setTimeout(() => saveFields(c), FIELDS_DEBOUNCE);
    if (P.triedSubmit) updateMissing();
  }
  function saveFields(c = P) {
    clearTimeout(c.fieldsTimer);
    c.fieldsTimer = null;
    return enqueue(c, () => putFields(c));
  }
  async function putFields(c) {
    const sent = JSON.stringify(c.F);
    if (c.alive) setSaveStatus('儲存中…');
    try {
      await papi(c, 'PUT', '/fields', { fields: c.F });
      if (c.alive && JSON.stringify(c.F) === sent && !c.fieldsTimer) setSaveStatus('已自動儲存');
      hideBanner();
      return true;
    } catch (e) {
      if (e.closed) return false;
      if (c.alive) setSaveStatus('尚未儲存');
      if (e instanceof HttpError && e.status === 409) { if (c.alive) load(); return false; }
      if (e instanceof HttpError && e.status === 400) { toast(`欄位沒存到：${e.message}`); return false; }
      showBanner(`欄位沒存到：${errorText(e)}。填的內容還在，按重試再存一次。`, () => saveFields(c));
      return false;
    }
  }

  // 商品編號（只有草稿可改）
  function skuProblem(v) {
    if (!v) return '請填商品編號';
    if (v.length > 40) return '商品編號最多 40 字';
    if (!SKU.test(v)) return '商品編號只能用英文、數字、中文或 - _ .，不能有空白或斜線';
    return '';
  }
  $('sku-input').addEventListener('input', () => {
    if (!P) return;
    clearTimeout(P.skuTimer);
    const v = $('sku-input').value.trim();
    const bad = skuProblem(v);
    $('sku-error').textContent = bad;
    if (bad || v === P.S.sku) { P.skuTimer = null; return; }
    setSaveStatus('尚未儲存');
    const c = P;
    P.skuTimer = setTimeout(() => saveSku(c), SKU_DEBOUNCE);
  });
  function saveSku(c = P) {
    clearTimeout(c.skuTimer);
    c.skuTimer = null;
    return enqueue(c, () => putSku(c));
  }
  async function putSku(c) {
    const v = $('sku-input').value.trim();
    if (!c.alive || skuProblem(v) || v === c.S.sku) return !skuProblem(v);
    setSaveStatus('儲存中…');
    try {
      await papi(c, 'PATCH', '', { sku: v });
      c.S.sku = v;
      if (c.alive) { setSaveStatus('已自動儲存'); $('sku-error').textContent = ''; renderHead(); }
      return true;
    } catch (e) {
      if (e.closed) return false;
      setSaveStatus('尚未儲存');
      if (e instanceof HttpError && e.status === 409 && e.body.error === 'duplicate_sku') $('sku-error').textContent = '這個商品編號在這期專案裡已經有了，換一個';
      else if (e instanceof HttpError && e.status === 409) load();
      else if (e instanceof HttpError) $('sku-error').textContent = e.message;
      else showBanner(`商品編號沒存到：${errorText(e)}`, () => saveSku(c));
      return false;
    }
  }

  // 色名標籤
  function renderColors() {
    $('color-tags').replaceChildren(...P.F.colors.map((col, i) => el('span', { class: 'tag' }, col,
      el('button', { type: 'button', 'aria-label': `移除色名 ${col}`, text: '×', onclick: () => { P.F.colors.splice(i, 1); renderColors(); scheduleFieldsSave(); } }))));
  }
  function addColor() {
    const input = $('color-input');
    const parts = input.value.split(/[、,，\n]/).map((s) => s.trim()).filter(Boolean);
    let added = false;
    for (const col of parts) if (!P.F.colors.includes(col)) { P.F.colors.push(col); added = true; }
    input.value = '';
    if (added) { renderColors(); scheduleFieldsSave(); }
  }
  $('color-add').addEventListener('click', addColor);
  $('color-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); addColor(); }
  });

  // 照片
  function renderPhotos() {
    $('photo-list').replaceChildren(...P.items.map((it) => {
      const label = { processing: '處理中', uploading: `上傳 ${Math.round(it.progress * 100)}%`, failed: '失敗' }[it.status];
      return el('li', { class: `photo ${it.status}`, 'data-key': it.key },
        it.url ? el('img', { src: it.url, alt: it.name || '商品照片', loading: 'lazy' }) : null,
        label ? el('span', { class: 'state', text: label }) : null,
        it.status === 'uploading' || it.status === 'processing'
          ? el('div', { class: 'bar' }, el('i', { style: `width:${Math.round(it.progress * 100)}%` })) : null,
        it.status === 'failed' ? el('button', { type: 'button', class: 'btn small retry', text: '重試', title: it.error, onclick: () => queue(P, [it]) }) : null,
        it.status === 'done' || it.status === 'failed'
          ? el('button', { type: 'button', class: 'del', 'aria-label': `刪除照片 ${it.name || ''}`, text: '✕', onclick: () => removePhoto(it) }) : null);
    }));
    if (P.triedSubmit) updateMissing();
  }
  function updateBar(c, it) {
    if (c !== P) return;
    const li = $('photo-list').querySelector(`[data-key="${CSS.escape(it.key)}"]`);
    if (!li) return;
    const bar = li.querySelector('.bar i');
    if (bar) bar.style.width = `${Math.round(it.progress * 100)}%`;
    const st = li.querySelector('.state');
    if (st && it.status === 'uploading') st.textContent = `上傳 ${Math.round(it.progress * 100)}%`;
  }

  $('file-input').addEventListener('change', (e) => {
    const files = [...e.target.files];
    e.target.value = '';
    if (!P) return;
    const room = MAX_PHOTOS - P.items.length;
    if (files.length > room) toast(`最多 ${MAX_PHOTOS} 張，多的沒有加入`);
    const added = files.slice(0, Math.max(0, room)).map((file) => ({
      key: `local-${Date.now()}-${Math.random().toString(36).slice(2)}`, name: file.name, file, status: 'processing', progress: 0,
    }));
    P.items.push(...added);
    renderPhotos();
    queue(P, added);
  });

  let uploadChain = Promise.resolve();
  function queue(c, list) {
    for (const it of list) {
      it.status = 'processing';
      it.progress = 0;
      uploadChain = uploadChain.then(() => upload(c, it));
    }
    if (c === P) renderPhotos();
    return uploadChain;
  }

  async function upload(c, it) {
    if (!c.items.includes(it)) return;
    try {
      if (!it.blob) {
        it.blob = await shrink(it.file);
        it.url = URL.createObjectURL(it.blob);
      }
      it.status = 'uploading';
      if (c === P) renderPhotos();
      const up = await enqueue(c, () => papi(c, 'POST', '/upload-url', { name: it.name, type: 'image/jpeg', size: it.blob.size }));
      await putFile(up.signed_url, it.blob, (p) => { it.progress = p; updateBar(c, it); });
      const data = await enqueue(c, () => papi(c, 'POST', '/photos', { path: up.path, name: it.name, size: it.blob.size }));
      it.path = up.path;
      it.status = 'done';
      it.progress = 1;
      it.file = null;
      if (data && Array.isArray(data.photos)) c.S.photos = data.photos;
    } catch (e) {
      it.status = 'failed';
      it.error = errorText(e);
      if (e.closed) return;
      if (e instanceof HttpError && e.status === 409) { if (c === P) load(); return; }
      if (c === P) toast(`照片上傳失敗：${it.error}`);
    }
    if (c === P) renderPhotos();
  }

  // 先套用 EXIF 方向，再縮到長邊 1600px、JPEG 0.82
  async function shrink(file) {
    let src;
    try {
      src = await createImageBitmap(file, { imageOrientation: 'from-image' });
    } catch {
      src = await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error('這個檔案不是可讀的照片'));
        img.src = URL.createObjectURL(file);
      });
    }
    const w = src.width || src.naturalWidth;
    const h = src.height || src.naturalHeight;
    const scale = Math.min(1, LONG_SIDE / Math.max(w, h));
    const canvas = el('canvas', { width: Math.max(1, Math.round(w * scale)), height: Math.max(1, Math.round(h * scale)) });
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(src, 0, 0, canvas.width, canvas.height);
    if (src.close) src.close();
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', JPEG_QUALITY));
    if (!blob) throw new Error('照片處理失敗');
    return blob;
  }

  function putFile(url, blob, onProgress) {
    return new Promise((resolve, reject) => {
      const x = new XMLHttpRequest();
      x.open('PUT', url);
      x.setRequestHeader('content-type', blob.type || 'image/jpeg');
      x.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
      x.onload = () => (x.status >= 200 && x.status < 300 ? resolve() : reject(new HttpError(x.status, null)));
      x.onerror = () => reject(new TypeError('network'));
      x.send(blob);
    });
  }

  async function removePhoto(it) {
    const c = P;
    if (it.path) {
      try {
        const data = await enqueue(c, () => papi(c, 'DELETE', '/photos', { path: it.path }));
        if (data && Array.isArray(data.photos)) c.S.photos = data.photos;
      } catch (e) {
        if (!e.closed) toast(`刪除失敗：${errorText(e)}`);
        return;
      }
    }
    c.items = c.items.filter((x) => x !== it);
    if (it.url && it.url.startsWith('blob:')) URL.revokeObjectURL(it.url);
    if (c === P) renderPhotos();
  }

  // 送出這個商品（內部呼叫 submit，product_ids 只有這一個）
  function missingFields() {
    const miss = [];
    if (skuProblem($('sku-input').value.trim())) miss.push('商品編號');
    if (!P.items.some((i) => i.status === 'done')) miss.push('照片');
    if (!P.F.name.trim()) miss.push('商品名稱');
    if (!P.F.category) miss.push('類別');
    if (!P.F.colors.length) miss.push('色名');
    if (!P.F.material.trim()) miss.push('材質');
    return miss;
  }
  function updateMissing() {
    const miss = missingFields();
    $('form-missing').textContent = miss.length ? `還缺：${miss.join('、')}` : '';
    return miss;
  }

  $('submit-btn').addEventListener('click', submit);
  async function submit() {
    const c = P;
    c.triedSubmit = true;
    if ($('color-input').value.trim()) addColor();
    if (updateMissing().length) return;
    if (c.items.some((i) => i.status === 'processing' || i.status === 'uploading')) {
      $('form-missing').textContent = '照片還在上傳，等一下再按';
      return;
    }
    const btn = $('submit-btn');
    btn.disabled = true;
    btn.textContent = '送出中…';
    try {
      if (c.skuTimer && !(await saveSku(c))) return;
      if (!(await saveFields(c))) return;
      await enqueue(c, () => api('POST', '/submit', { product_ids: [c.pid] }));
      setSaveStatus('');
      store.set(KEY(c).submitted, new Date().toISOString());
      await load();
    } catch (e) {
      if (e.closed) return;
      if (e instanceof HttpError && e.status === 400 && e.body.error === 'incomplete') {
        const mine = (e.body.items || []).find((x) => x.id === c.pid) || (e.body.items || [])[0];
        $('form-missing').textContent = `還缺：${missingText(mine && mine.missing) || e.message}`;
      } else if (e instanceof HttpError && e.status === 400) $('form-missing').textContent = e.message;
      else if (e instanceof HttpError && e.status === 409) await load();
      else showBanner(`送出失敗：${errorText(e)}。填的內容都還在，按重試再送一次。`, submit);
    } finally {
      btn.disabled = false;
      btn.textContent = '送出這個商品';
    }
  }

  $('delete-product').addEventListener('click', async () => {
    const c = P;
    const ok = await ask({ title: '刪除這個草稿商品？', body: `「${c.S.sku}」的照片和填好的資料都會刪掉，不能復原。`, yes: '刪除', danger: true });
    if (!ok || c !== P) return;
    try {
      clearTimeout(c.fieldsTimer);
      clearTimeout(c.skuTimer);
      c.fieldsTimer = c.skuTimer = null;
      await enqueue(c, () => papi(c, 'DELETE', ''));
      toast(`已刪除「${c.S.sku}」`);
      nav.home();
    } catch (e) {
      if (!e.closed) toast(`刪除失敗：${errorText(e)}`);
    }
  });

  // ---------- 2. 等待 ----------
  function renderWait() {
    showPane('wait');
    const S = P.S;
    $('wait-title').textContent = S.status === 'submitted' ? '已送出，等小編接手' : '小編正在寫文案';
    const offset = S.server_time ? Date.parse(S.server_time) - Date.now() : 0;
    const start = Date.parse(store.get(KEY(P).submitted) || S.updated_at || '') || Date.now() + offset;
    const tick = () => {
      const sec = Math.max(0, Math.floor((Date.now() + offset - start) / 1000));
      $('wait-time').textContent = sec >= 60 ? `${Math.floor(sec / 60)} 分 ${sec % 60} 秒` : `${sec} 秒`;
    };
    tick();
    clearInterval(P.waitTicker);
    P.waitTicker = setInterval(tick, 1000);
  }
  function schedulePoll() {
    clearTimeout(P.pollTimer);
    const c = P;
    P.pollTimer = setTimeout(() => { if (c === P && c.alive) load(true); }, document.hidden ? POLL_SLOW : POLL_FAST);
  }
  function stopPolling() {
    if (!P) return;
    clearTimeout(P.pollTimer);
    clearInterval(P.waitTicker);
  }
  document.addEventListener('visibilitychange', () => {
    if (!P || !P.alive) return;
    if (P.pane === 'wait') { if (document.hidden) schedulePoll(); else load(true); }
    if (document.hidden && (P.stateTimer || (P.stateDirty && !P.stateQueued))) saveState();
    if (document.hidden && P.editedTimer) saveEdited();
  });

  // ---------- 3. 挑句子 / 4. 定稿 ----------
  function normState(s) {
    s = s && typeof s === 'object' ? s : {};
    const strs = (v) => (Array.isArray(v) ? [...new Set(v.filter((x) => typeof x === 'string'))] : []);
    return {
      draft: (Array.isArray(s.draft) ? s.draft : []).filter((d) => d && typeof d.text === 'string')
        .map((d) => ({ id: String(d.id || d.origin || ''), text: d.text, origin: String(d.origin || d.id || '') })),
      hearts: strs(s.hearts),
      dislikes: strs(s.dislikes),
      banned_words: strs(s.banned_words),
      final: s.final && typeof s.final.text === 'string' ? { text: s.final.text, saved_at: String(s.final.saved_at || '') } : null,
    };
  }
  const sentences = () => ['A', 'B', 'C'].flatMap((r) => ((P.S.result.routes || {})[r] || []).map((x) => ({ ...x, route: r })));
  const sentenceById = (id) => sentences().find((x) => x.id === id);
  const bannedIn = (text) => P.ST.banned_words.find((w) => w && text.includes(w));
  const material = () => String((P.S.fields && P.S.fields.material) || '').trim();
  const useMaterial = () => store.get(KEY(P).material) !== false;

  function renderPick() {
    const c = P;
    // 本機還有沒存到的挑選（或正在存）時，不拿伺服器版本蓋掉；真的衝突時 PUT 會回 stale 再處理
    if (!c.ST || (c.pickReadyRev !== c.S.state_rev && !c.stateDirty && !c.stateTimer)) {
      c.ST = normState(c.S.state);
      c.rev = c.S.state_rev;
      const backup = store.get(KEY(c).state);
      if (backup && backup.rev === c.rev && JSON.stringify(normState(backup.state)) !== JSON.stringify(c.ST)) {
        c.ST = normState(backup.state); // 上次沒存到伺服器的挑選
        scheduleSave();
        toast('已還原上次還沒儲存的挑選');
      } else if (backup) store.del(KEY(c).state);
      c.pickReadyRev = c.rev;
    }
    showPane('pick');
    const ST = c.ST;
    const finalized = !!ST.final;
    $('final-box').hidden = !finalized;
    $('pick-area').hidden = finalized;
    $('fab').hidden = finalized;
    if (finalized) {
      $('final-text').textContent = ST.final.text;
      if (!c.editedDirty && !c.editedTimer) $('edited-text').value = c.editedValue = c.S.edited_final || '';
      else $('edited-text').value = c.editedValue;
      if (!c.editedQueued) $('edited-status').textContent = c.editedDirty || c.editedTimer ? '尚未儲存' : c.S.edited_at ? '已儲存' : '';
      closeSheet();
      return;
    }
    $('summary').textContent = c.S.result.product_summary || '';
    const all = sentences();
    const visible = (x) => !ST.dislikes.includes(x.id) && !bannedIn(x.text);
    for (const t of document.querySelectorAll('.tab')) {
      const r = t.dataset.route;
      t.setAttribute('aria-selected', String(r === c.tab));
      t.tabIndex = r === c.tab ? 0 : -1;
      t.querySelector('.count').textContent = `${all.filter((x) => x.route === r && visible(x)).length} 句`;
    }
    const list = all.filter((x) => x.route === c.tab && visible(x));
    $('cards').setAttribute('aria-label', ROUTE_NAMES[c.tab]);
    $('cards').replaceChildren(...(list.length ? list.map(card) : [el('li', { class: 'empty', text: '這個版本沒有可以挑的句子了' })]));
    const hidden = all.filter((x) => !visible(x));
    $('hidden-box').hidden = !hidden.length;
    $('hidden-summary').textContent = `已隱藏 ${hidden.length} 句（可復原）`;
    $('hidden-list').replaceChildren(...hidden.map((x) => {
      const word = bannedIn(x.text);
      return el('li', {}, el('span', { text: `${x.id}　${x.text}` }),
        ST.dislikes.includes(x.id)
          ? el('button', { type: 'button', class: 'btn small', text: '復原', 'aria-label': `復原 ${x.id}`, onclick: () => undislike(x.id) })
          : el('em', { text: `含禁用詞「${word}」` }));
    }));
    $('fab').textContent = `我的文案 (${ST.draft.length})`;
    if (!$('sheet').hidden) renderSheet();
  }

  function card(x) {
    const hearted = P.ST.hearts.includes(x.id);
    return el('li', { class: `cardx${hearted ? ' hearted' : ''}`, 'data-id': x.id },
      el('div', { class: 'text' },
        x.check ? el('span', { class: 'badge', text: '⚠ 需確認' }) : null,
        x.check ? el('br') : null,
        x.text),
      el('button', { type: 'button', class: 'act heart', 'aria-pressed': String(hearted), 'aria-label': `${hearted ? '取消' : '加入'}我的文案：${x.id}`, text: hearted ? '♥' : '♡', onclick: () => toggleHeart(x.id) }),
      el('button', { type: 'button', class: 'act dislike', 'aria-label': `不喜歡：${x.id}`, text: '✕', onclick: (e) => dislike(x.id, e.currentTarget.closest('li')) }));
  }

  for (const t of document.querySelectorAll('.tab')) {
    t.addEventListener('click', () => { P.tab = t.dataset.route; renderPick(); });
    t.addEventListener('keydown', (e) => {
      const order = ['A', 'B', 'C'];
      const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
      if (!d) return;
      P.tab = order[(order.indexOf(P.tab) + d + 3) % 3];
      renderPick();
      document.querySelector(`.tab[data-route="${P.tab}"]`).focus();
    });
  }

  function dropFromDraft(id) {
    P.ST.hearts = P.ST.hearts.filter((h) => h !== id);
    P.ST.draft = P.ST.draft.filter((d) => d.origin !== id);
  }
  function toggleHeart(id) {
    if (P.ST.hearts.includes(id)) dropFromDraft(id);
    else {
      const x = sentenceById(id);
      P.ST.hearts.push(id);
      if (!P.ST.draft.some((d) => d.origin === id)) P.ST.draft.push({ id, text: x.text, origin: id });
    }
    renderPick();
    scheduleSave();
  }
  function dislike(id, li) {
    const c = P;
    const done = () => {
      if (c !== P) return;
      if (!c.ST.dislikes.includes(id)) c.ST.dislikes.push(id);
      dropFromDraft(id);
      renderPick();
      scheduleSave();
      toast('已隱藏，可在下方「已隱藏」復原');
    };
    const quiet = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (li && !quiet) { li.classList.add('leaving'); setTimeout(done, 250); } else done();
  }
  function undislike(id) {
    P.ST.dislikes = P.ST.dislikes.filter((d) => d !== id);
    renderPick();
    scheduleSave();
  }

  // 我的文案面板
  let lastFocus = null;
  function openSheet() {
    lastFocus = document.activeElement;
    $('sheet').hidden = false;
    $('sheet-backdrop').hidden = false;
    renderSheet();
    $('sheet-close').focus();
  }
  function closeSheet() {
    if ($('sheet').hidden) return;
    $('sheet').hidden = true;
    $('sheet-backdrop').hidden = true;
    if (lastFocus && lastFocus.isConnected) lastFocus.focus();
  }
  $('fab').addEventListener('click', openSheet);
  $('sheet-close').addEventListener('click', closeSheet);
  $('sheet-backdrop').addEventListener('click', closeSheet);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !document.querySelector('dialog[open]')) closeSheet(); });

  const grow = (ta) => { ta.style.height = 'auto'; ta.style.height = `${ta.scrollHeight + 2}px`; };

  function renderSheet() {
    const ST = P.ST;
    $('draft-empty').hidden = ST.draft.length > 0;
    $('draft-list').replaceChildren(...ST.draft.map((d, i) => {
      const word = bannedIn(d.text);
      const src = sentenceById(d.origin);
      const ta = el('textarea', { rows: 1, value: d.text, 'aria-label': `第 ${i + 1} 句，可直接修改` });
      ta.addEventListener('input', () => {
        d.text = ta.value;
        grow(ta);
        scheduleSave();
      });
      return el('li', { class: word ? 'warn' : '' },
        word ? el('p', { class: 'why', text: `含禁用詞「${word}」` }) : null,
        ta,
        el('div', { class: 'tools' },
          el('span', { class: 'origin', text: `${d.origin}${src && src.text !== d.text ? '・已修改' : ''}` }),
          el('button', { type: 'button', class: 'btn', text: '↑', 'aria-label': `第 ${i + 1} 句往上移`, disabled: i === 0, onclick: () => move(i, -1) }),
          el('button', { type: 'button', class: 'btn', text: '↓', 'aria-label': `第 ${i + 1} 句往下移`, disabled: i === ST.draft.length - 1, onclick: () => move(i, 1) }),
          el('button', { type: 'button', class: 'btn', text: '移除', 'aria-label': `移除第 ${i + 1} 句`, onclick: () => { dropFromDraft(d.origin); P.ST.draft = P.ST.draft.filter((x) => x !== d); renderPick(); scheduleSave(); } })));
    }));
    for (const ta of $('draft-list').querySelectorAll('textarea')) grow(ta);
    const mat = material();
    $('material-toggle').closest('label').hidden = !mat;
    $('material-toggle').checked = useMaterial();
    $('material-line').hidden = !mat || !useMaterial();
    $('material-line').textContent = mat;
    $('banned-tags').replaceChildren(...ST.banned_words.map((w) => el('span', { class: 'tag' }, w,
      el('button', { type: 'button', 'aria-label': `移除禁用詞 ${w}`, text: '×', onclick: () => { P.ST.banned_words = P.ST.banned_words.filter((x) => x !== w); renderPick(); scheduleSave(); } }))));
  }
  function move(i, d) {
    const j = i + d;
    const draft = P.ST.draft;
    if (j < 0 || j >= draft.length) return;
    [draft[i], draft[j]] = [draft[j], draft[i]];
    renderSheet();
    scheduleSave();
    const btn = $('draft-list').children[j].querySelectorAll('.tools .btn')[d < 0 ? 0 : 1];
    (btn && !btn.disabled ? btn : $('draft-list').children[j].querySelector('textarea')).focus();
  }
  $('material-toggle').addEventListener('change', (e) => { store.set(KEY(P).material, e.target.checked); renderSheet(); });
  function addBanned() {
    const input = $('banned-input');
    const words = input.value.split(/[、,，\s]+/).map((s) => s.trim()).filter(Boolean);
    input.value = '';
    const fresh = words.filter((w) => !P.ST.banned_words.includes(w));
    if (!fresh.length) return;
    P.ST.banned_words.push(...fresh);
    renderPick();
    scheduleSave();
    toast(`已加入禁用詞：${fresh.join('、')}`);
  }
  $('banned-add').addEventListener('click', addBanned);
  $('banned-input').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); addBanned(); } });

  function composeText() {
    const lines = P.ST.draft.map((d) => d.text.trim()).filter(Boolean);
    if (useMaterial() && material()) lines.push(material());
    return lines.join('\n');
  }
  $('copy-btn').addEventListener('click', () => (P.ST.draft.length ? copyText(composeText()) : toast('還沒有句子')));
  $('final-copy').addEventListener('click', () => copyText(P.ST.final ? P.ST.final.text : ''));
  $('finalize-btn').addEventListener('click', async () => {
    const c = P;
    if (!c.ST.draft.some((d) => d.text.trim())) return toast('先按 ♡ 加入句子再定稿');
    const warn = c.ST.draft.find((d) => bannedIn(d.text));
    if (warn) return toast(`有句子含禁用詞「${bannedIn(warn.text)}」，請先修改或移除`);
    const ok = await ask({ title: '確定要定稿嗎？', body: '定稿後會記錄成團隊的喜好。之後還是可以按「回去修改」。', yes: '定稿' });
    if (!ok || c !== P) return;
    c.ST.final = { text: composeText(), saved_at: new Date().toISOString() };
    markState(c);
    closeSheet();
    renderPick();
    saveState(c);
    window.scrollTo(0, 0);
  });
  $('final-edit').addEventListener('click', async () => {
    const c = P;
    // 先把修改後文案存掉（佇列保證它排在「取消定稿」的 state 之前送出）
    if ((c.editedTimer || c.editedDirty) && !(await saveEdited(c))) return;
    if (c !== P || !c.ST.final) return;
    c.ST.final = null;
    markState(c);
    renderPick();
    saveState(c);
  });

  // 自動儲存挑選狀態（帶 rev；409 stale 就採用伺服器版本）。
  // 所有 state PUT 都排進商品寫入佇列，所以 ♡／✕／草稿／定稿／回去修改 會依序送出，rev 永遠是上一個回應的值。
  function markState(c) {
    c.stateDirty = true;
    store.set(KEY(c).state, { rev: c.rev, state: c.ST });
    if (c.alive) setSaveStatus('尚未儲存');
  }
  function scheduleSave() {
    const c = P;
    markState(c);
    clearTimeout(c.stateTimer);
    c.stateTimer = setTimeout(() => saveState(c), STATE_DEBOUNCE);
  }
  // 回傳 Promise<boolean>：這次（含排隊中）的 state 是否已存到伺服器
  function saveState(c = P) {
    clearTimeout(c.stateTimer);
    c.stateTimer = null;
    if (!c.stateQueued) c.stateQueued = enqueue(c, () => { c.stateQueued = null; return putState(c); });
    return c.stateQueued;
  }
  async function putState(c) {
    if (!c.stateDirty) return true;
    if (c.alive) setSaveStatus('儲存中…');
    for (;;) {
      const sent = JSON.stringify(c.ST);
      try {
        const r = await papi(c, 'PUT', '/state', { state: c.ST, rev: c.rev });
        c.rev = r.state_rev;
        c.S.state_rev = c.rev;
        c.pickReadyRev = c.rev;
        c.S.status = r.status || (JSON.parse(sent).final ? 'finalized' : 'ready');
        hideBanner();
        if (JSON.stringify(c.ST) !== sent) { store.set(KEY(c).state, { rev: c.rev, state: c.ST }); continue; } // 送出期間又改了：接著送新版
        c.stateDirty = false;
        store.del(KEY(c).state);
        if (c.alive) { if (settled(c)) setSaveStatus('已自動儲存'); renderHead(); }
        return true;
      } catch (e) {
        if (e.closed) { /* 專案已結束，交給 app 切到唯讀 */ } else if (e instanceof HttpError && e.status === 409 && e.body.error === 'stale') {
          c.ST = normState(e.body.state);
          c.rev = e.body.state_rev;
          c.S.state_rev = c.rev;
          c.pickReadyRev = c.rev;
          c.S.status = c.ST.final ? 'finalized' : 'ready';
          c.stateDirty = false;
          store.del(KEY(c).state);
          if (c.alive) {
            setSaveStatus('已載入最新版本');
            toast('其他裝置剛更新過，已載入最新版本');
            renderPick();
          }
        } else if (e instanceof HttpError && e.status === 409) {
          if (c.alive) { setSaveStatus('尚未儲存'); load(); }
        } else {
          if (c.alive) setSaveStatus('尚未儲存');
          showBanner(`挑選沒存到：${errorText(e)}。你的挑選還在，按重試再存一次。`, () => saveState(c));
        }
        return false;
      }
    }
  }

  // 我修改後的最終文案（只在定稿後；autosave）
  $('edited-text').addEventListener('input', () => {
    if (!P) return;
    scheduleEdited();
  });
  function setEditedStatus(c, text) {
    if (c.alive) $('edited-status').textContent = text;
  }
  function scheduleEdited() {
    const c = P;
    c.editedValue = $('edited-text').value;
    c.editedDirty = true;
    setEditedStatus(c, '尚未儲存');
    setSaveStatus('尚未儲存');
    clearTimeout(c.editedTimer);
    c.editedTimer = setTimeout(() => saveEdited(c), EDITED_DEBOUNCE);
  }
  // 排進商品寫入佇列，所以一定在前面（含「定稿」）的 state PUT 完成後才送出。
  // 若仍回 409 not_finalized（例如定稿的 state 沒存到），先重新存 state 再重試一次；
  // 任何失敗都保留 dirty 與文字，狀態維持「尚未儲存」並給重試按鈕。回傳 Promise<boolean>。
  function saveEdited(c = P) {
    clearTimeout(c.editedTimer);
    c.editedTimer = null;
    if (!c.editedQueued) c.editedQueued = editedFlow(c);
    return c.editedQueued;
  }
  async function editedFlow(c) {
    if (c.editedDirty) { setEditedStatus(c, '儲存中…'); if (c.alive) setSaveStatus('儲存中…'); }
    let r = await enqueue(c, () => { c.editedQueued = null; return putEdited(c); });
    if (r === 'not_finalized') {
      await saveState(c); // 定稿的 state 還沒存到（或失敗了）：等它存好
      r = await enqueue(c, () => putEdited(c));
    }
    if (r === true) return true;
    if (c.editedDirty) {
      setEditedStatus(c, r === 'too_long' ? `太長了（上限 ${EDITED_MAX} 字），還沒儲存` : '尚未儲存');
      if (c.alive) setSaveStatus('尚未儲存');
    }
    if (r === 'not_finalized') showBanner('修改後的文案還沒存到：定稿還沒存好。文字還在，按重試再存一次。', () => saveEdited(c));
    return false;
  }
  async function putEdited(c) {
    if (!c.editedDirty) return true;
    const text = c.editedValue;
    if (text.length > EDITED_MAX) return 'too_long';
    setEditedStatus(c, '儲存中…');
    try {
      const r = await papi(c, 'PUT', '/edited-final', { text });
      c.S.edited_final = text || null;
      c.S.edited_at = r && r.edited_at;
      hideBanner();
      if (c.editedValue === text) {
        c.editedDirty = false;
        setEditedStatus(c, '已儲存');
        if (c.alive && settled(c)) setSaveStatus('已自動儲存');
      } else setEditedStatus(c, '尚未儲存'); // 送出期間又改了，debounce 計時器會再存
      return true;
    } catch (e) {
      if (e instanceof HttpError && e.status === 409 && e.body.error === 'not_finalized') return 'not_finalized';
      if (e.closed) return false;
      if (e instanceof HttpError && e.status === 400) { toast(`修改後的文案沒存到：${e.message}`); return false; }
      showBanner(`修改後的文案沒存到：${errorText(e)}。文字還在，按重試再存一次。`, () => saveEdited(c));
      return false;
    }
  }
  $('edited-seed').addEventListener('click', async () => {
    const c = P;
    const finalText = c.ST && c.ST.final ? c.ST.final.text : '';
    const cur = $('edited-text').value;
    if (cur.trim() && cur !== finalText) {
      const ok = await ask({ title: '用定稿全文取代？', body: '目前框裡的文字會被定稿全文取代。', yes: '取代' });
      if (!ok || c !== P) return;
    }
    $('edited-text').value = finalText;
    scheduleEdited();
    $('edited-text').focus();
  });
  $('edited-copy').addEventListener('click', () => copyText($('edited-text').value));

  // ---------- 離開商品畫面 ----------
  // 把還沒存的東西立刻存掉；回傳是否全部成功。
  async function flush(c = P) {
    if (!c) return true;
    const jobs = [];
    if (c.skuTimer) jobs.push(saveSku(c));
    if (c.fieldsTimer) jobs.push(saveFields(c));
    if (c.stateTimer || c.stateDirty) jobs.push(saveState(c));
    if (c.editedTimer || c.editedDirty) jobs.push(saveEdited(c));
    const res = await Promise.all(jobs);
    await c.writes; // 其他已排隊的寫入也等它送完
    return res.every(Boolean);
  }
  async function leave(force) {
    const c = P;
    if (!c) return true;
    const ok = await flush(c);
    if (!ok && !force) return false;
    c.alive = false;
    stopPolling();
    clearInterval(c.waitTicker);
    closeSheet();
    if (P === c) P = null;
    setSaveStatus('');
    return true;
  }
  $('back-home').addEventListener('click', async () => {
    if (await leave(false)) nav.home();
  });

  window.addEventListener('pagehide', () => {
    const c = P;
    if (!c) return;
    if (c.fieldsTimer) papi(c, 'PUT', '/fields', { fields: c.F }, { keepalive: true }).catch(() => {});
    if (c.editedDirty || c.editedTimer) papi(c, 'PUT', '/edited-final', { text: c.editedValue }, { keepalive: true }).catch(() => {});
  });
  // 還有沒存到伺服器的東西（含正在送、排隊中、失敗待重試）就提醒
  window.addEventListener('beforeunload', (e) => {
    const c = P;
    if (!c) return;
    if (!settled(c) || c.items.some((i) => i.status === 'uploading' || i.status === 'processing')) {
      e.preventDefault();
      e.returnValue = '';
    }
  });

  window.HCP.product = {
    open, leave, setProject, setNav(n) { nav = n; },
    get current() { return P ? P.pid : null; },
  };
})();
