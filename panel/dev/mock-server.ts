// 面板後端 v2 的記憶體版假伺服器（只供本機開發與測試，不要部署）。
// 照 spec2 實作 panel-api 的全部使用者端（/p/…）與 Bot 端（/bot/…）路由與 CORS，另外：
//   * /functions/v1/panel-api/*   假 API（跟正式網址同一個路徑結構）
//   * /storage/upload/<path>       假的簽名上傳網址（PUT 原始檔案內容）
//   * /storage/object/<path>       假的簽名下載網址（GET）
//   * /dev/webhook                 假 webhook 接收端；GET /dev/webhooks 看收到的通知
//   * /config.js                   改寫成指向本機 API
//   * 其他路徑                      panel/public/ 靜態檔；/p/:token、/s/:token 回 index.html（同 vercel.json rewrite），
//                                  並套用 vercel.json 的安全標頭（含 CSP），讓本機測試也會抓到 CSP 違規
// 啟動：deno run -A panel/dev/mock-server.ts   （環境變數 PORT 預設 8787、MOCK_BOT_KEY 預設 dev-bot-key）
// MOCK_DELAY_MS（預設 0）：使用者端 API（/p/…）每個請求先等這麼久才處理，模擬正式 Edge Function 1–3 秒的延遲，
// 用來重現前端「樂觀更新 + 請求還在飛」的競態。Bot 端與靜態檔不延遲。

const PORT = Number(Deno.env.get("PORT") ?? 8787);
const BOT_KEY = Deno.env.get("MOCK_BOT_KEY") ?? "dev-bot-key";
const DELAY_MS = Math.max(0, Number(Deno.env.get("MOCK_DELAY_MS") ?? 0) || 0);
const PUBLIC_DIR = new URL("../public/", import.meta.url);
const VERCEL_JSON = new URL("../vercel.json", import.meta.url);
const API_PREFIX = "/functions/v1/panel-api";
const MAX_PHOTO_BYTES = 8 * 1024 * 1024;
const MAX_PHOTOS = 20;
const MAX_PRODUCTS = 30;
const MAX_EDITED = 5000;
const PHOTO_TYPES: Record<string, string> = { "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp" };
const FIELD_KEYS = ["name", "category", "colors", "material", "sizes", "price", "background", "feelings"];
const STATE_LIMIT = 64 * 1024;
const ROUTES = ["A", "B", "C"];
const STATUSES = ["draft", "submitted", "generating", "ready", "finalized"];
const KEEP_DAYS = 180;
const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SKU_RE = /^[A-Za-z0-9._\-\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}ー々]{1,40}$/u;

type Photo = { path: string; name: string; size: number; order: number };
type Project = {
  token: string; title: string; status: "open" | "closed"; options: Record<string, unknown>;
  created_at: string; updated_at: string; closed_at: string | null; close_notify_status: string | null; expires_at: string | null;
};
type Product = {
  id: string; project_token: string; sku: string; position: number; status: string;
  fields: Record<string, unknown>; photos: Photo[]; result: Record<string, unknown> | null;
  state: Record<string, unknown>; state_rev: number; edited_final: string | null; edited_at: string | null;
  notify_status: string | null; submitted_at: string | null; claimed_at: string | null; synced_at: string | null;
  created_at: string; updated_at: string;
};

const projects = new Map<string, Project>();
const products = new Map<string, Product>();
const storage = new Map<string, { bytes: Uint8Array<ArrayBuffer>; type: string }>();
const uploadTokens = new Map<string, string>(); // upload token -> path
const config = new Map<string, string>();
const webhookLog: unknown[] = [];

const enc = new TextEncoder();
async function sha256hex(text: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", enc.encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
config.set("bot_key_sha256", await sha256hex(BOT_KEY));

function randomToken(bytes = 32): string {
  const b = crypto.getRandomValues(new Uint8Array(bytes));
  return btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
const now = () => new Date().toISOString();

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "content-type, x-bot-key, authorization",
  "access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
};
function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...CORS, "content-type": "application/json; charset=utf-8" } });
}
class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public extra: Record<string, unknown> = {}) {
    super(message);
  }
}
const fail = (status: number, code: string, message: string, extra = {}): never => {
  throw new ApiError(status, code, message, extra);
};

async function readJson(req: Request): Promise<Record<string, unknown>> {
  const text = await req.text();
  if (!text) return {};
  try {
    const v = JSON.parse(text);
    if (v && typeof v === "object" && !Array.isArray(v)) return v;
  } catch { /* fall through */ }
  return fail(400, "bad_json", "請求內容不是正確的 JSON");
}

// ---------- 資料存取 ----------
function getProject(token: string, opts: { allowExpired?: boolean } = {}): Project {
  if (!TOKEN_RE.test(token)) fail(404, "not_found", "找不到這個專案連結，請回聊天室跟小編要新連結");
  const p = projects.get(token);
  if (!p) return fail(404, "not_found", "找不到這個專案連結，請回聊天室跟小編要新連結");
  if (!opts.allowExpired && p.expires_at && Date.parse(p.expires_at) < Date.now()) {
    fail(404, "not_found", "這個專案連結已過期，請回聊天室跟小編要新連結");
  }
  return p;
}
function productsOf(token: string): Product[] {
  return [...products.values()].filter((x) => x.project_token === token).sort((a, b) => a.position - b.position);
}
function getProduct(p: Project, pid: string): Product {
  if (!UUID_RE.test(pid)) fail(404, "not_found", "找不到這個商品");
  const x = products.get(pid.toLowerCase());
  if (!x || x.project_token !== p.token) return fail(404, "not_found", "找不到這個商品");
  return x;
}
function requireOpen(p: Project) {
  if (p.status !== "open") fail(409, "project_closed", "這期專案已經結束，不能再修改");
}
function requireDraft(x: Product) {
  if (x.status !== "draft") fail(409, "not_draft", "這個商品已經送出，不能再修改");
}
function touch(x: Product | Project) {
  x.updated_at = now();
}
function checkSku(raw: unknown): string {
  const sku = typeof raw === "string" ? raw.trim() : "";
  if (!sku) fail(400, "bad_sku", "請填商品編號");
  if (sku.length > 40) fail(400, "bad_sku", "商品編號最多 40 字");
  if (!SKU_RE.test(sku)) fail(400, "bad_sku", "商品編號只能用英文、數字、中文或 - _ .，不能有空白或斜線");
  return sku;
}
function categoriesOf(p: Project): string[] {
  const c = p.options.categories;
  return Array.isArray(c) ? c.filter((x): x is string => typeof x === "string") : [];
}
const nameOf = (x: Product) => String(x.fields.name ?? "");

function signed(path: string, origin: string, hours: number) {
  return `${origin}/storage/object/${path}?exp=${Date.now() + hours * 3600_000}`;
}
function productView(p: Project, x: Product, origin: string, hours: number, bot: boolean) {
  const photos = [...x.photos].sort((a, b) => a.order - b.order).map((ph) => ({ ...ph, url: signed(ph.path, origin, hours) }));
  const body: Record<string, unknown> = {
    id: x.id, sku: x.sku, status: x.status, fields: x.fields, photos, result: x.result, state: x.state,
    state_rev: x.state_rev, edited_final: x.edited_final, edited_at: x.edited_at, updated_at: x.updated_at,
    server_time: now(), project_status: p.status,
  };
  if (bot) Object.assign(body, { notify_status: x.notify_status, synced_at: x.synced_at, claimed_at: x.claimed_at, submitted_at: x.submitted_at });
  return body;
}
const finalText = (x: Product) => {
  const f = x.state.final as { text?: unknown } | null | undefined;
  return f && typeof f.text === "string" ? f.text : null;
};

function cleanFields(p: Project, input: unknown): Record<string, unknown> {
  if (!input || typeof input !== "object" || Array.isArray(input)) fail(400, "bad_fields", "欄位格式不正確");
  const src = input as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const key of FIELD_KEYS) {
    const v = src[key];
    if (key === "colors") {
      if (v === undefined || v === null) { out.colors = []; continue; }
      if (!Array.isArray(v) || v.some((c) => typeof c !== "string")) fail(400, "bad_fields", "色名格式不正確");
      const colors = (v as string[]).map((c) => c.trim()).filter(Boolean);
      if (colors.join("、").length > 1000) fail(400, "too_long", "色名太長了");
      out.colors = colors;
      continue;
    }
    if (v === undefined || v === null) { out[key] = ""; continue; }
    if (typeof v !== "string") fail(400, "bad_fields", `欄位 ${key} 必須是文字`);
    const limit = key === "name" ? 120 : 1000;
    if ((v as string).length > limit) fail(400, "too_long", `欄位 ${key} 太長了（上限 ${limit} 字）`);
    out[key] = v;
  }
  if (out.category && !categoriesOf(p).includes(out.category as string)) fail(400, "bad_category", "類別不在可選的清單裡");
  return out;
}

function missingOf(x: Product): string[] {
  const f = x.fields;
  const miss: string[] = [];
  if (!SKU_RE.test(x.sku)) miss.push("sku");
  if (!String(f.name ?? "").trim()) miss.push("name");
  if (!String(f.category ?? "").trim()) miss.push("category");
  if (!Array.isArray(f.colors) || !f.colors.length) miss.push("colors");
  if (!String(f.material ?? "").trim()) miss.push("material");
  if (!x.photos.length) miss.push("photos");
  return miss;
}

function validateState(input: unknown): Record<string, unknown> {
  if (!input || typeof input !== "object" || Array.isArray(input)) fail(400, "bad_state", "挑選狀態格式不正確");
  const st = input as Record<string, unknown>;
  if (enc.encode(JSON.stringify(st)).length > STATE_LIMIT) fail(413, "too_large", "挑選內容太大了");
  const strArr = (v: unknown) => Array.isArray(v) && v.every((x) => typeof x === "string");
  const draft = st.draft ?? [];
  if (
    !Array.isArray(draft) ||
    !draft.every((d) => d && typeof d === "object" && typeof d.id === "string" && typeof d.text === "string" && (d.origin === undefined || typeof d.origin === "string"))
  ) {
    fail(400, "bad_state", "草稿格式不正確");
  }
  for (const k of ["hearts", "dislikes", "banned_words"]) if (st[k] !== undefined && !strArr(st[k])) fail(400, "bad_state", `${k} 格式不正確`);
  const final = st.final ?? null;
  if (final !== null && !(typeof final === "object" && typeof (final as Record<string, unknown>).text === "string")) fail(400, "bad_state", "定稿格式不正確");
  return { draft, hearts: st.hearts ?? [], dislikes: st.dislikes ?? [], banned_words: st.banned_words ?? [], final };
}

// ---------- webhook ----------
async function sendWebhook(body: Record<string, unknown>): Promise<string> {
  const url = config.get("webhook_url") ?? Deno.env.get("PANEL_WEBHOOK_URL");
  const key = config.get("webhook_key") ?? Deno.env.get("PANEL_WEBHOOK_KEY") ?? "";
  if (!url) return "unset";
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (key) headers.authorization = `Bearer ${key}`;
  try {
    const res = await fetch(url, { method: "POST", headers, body: JSON.stringify(body), signal: AbortSignal.timeout(5000) });
    await res.body?.cancel();
    return res.ok ? "ok" : `failed:${res.status}`;
  } catch (e) {
    return (e as Error).name === "TimeoutError" ? "failed:timeout" : "failed:network";
  }
}
function webhookLists(list: Product[]) {
  return { product_ids: list.map((x) => x.id), product_names: list.map((x) => nameOf(x).slice(0, 60)), skus: list.map((x) => x.sku) };
}

async function closeProject(p: Project): Promise<Record<string, unknown>> {
  if (p.status !== "closed") {
    const t = now();
    p.status = "closed";
    p.closed_at = t;
    p.expires_at = new Date(Date.now() + KEEP_DAYS * 86400_000).toISOString();
    p.updated_at = t;
    const list = productsOf(p.token).filter((x) => x.status !== "draft"); // 草稿不計入
    p.close_notify_status = await sendWebhook({ event: "panel.project.closed", project_token: p.token, ...webhookLists(list), submitted_at: t, closed_at: t });
  }
  return { status: p.status, closed_at: p.closed_at, expires_at: p.expires_at, close_notify_status: p.close_notify_status };
}

function removeFiles(prefix: string) {
  for (const k of [...storage.keys()]) if (k.startsWith(prefix)) storage.delete(k);
}

// ---------- 使用者端 ----------
async function userRoute(req: Request, token: string, rest: string, origin: string): Promise<Response> {
  const p = getProject(token);
  const m = req.method;
  if (rest === "" && m === "GET") {
    const closed = p.status === "closed";
    return json({
      title: p.title, status: p.status, closed_at: p.closed_at, options: p.options, server_time: now(),
      products: productsOf(token).map((x) => ({
        id: x.id, sku: x.sku, name: nameOf(x), status: x.status, position: x.position, photo_count: x.photos.length,
        updated_at: x.updated_at, has_edited_final: !!x.edited_final,
        ...(closed ? { final_text: finalText(x), edited_final: x.edited_final } : {}),
      })),
    });
  }
  if (rest === "/products" && m === "POST") {
    requireOpen(p);
    const b = await readJson(req);
    const sku = checkSku(b.sku);
    const list = productsOf(token);
    if (list.length >= MAX_PRODUCTS) fail(409, "too_many", `每期最多 ${MAX_PRODUCTS} 個商品`);
    if (list.some((x) => x.sku === sku)) fail(409, "duplicate_sku", "這個商品編號在這期專案裡已經有了");
    const t = now();
    const x: Product = {
      id: crypto.randomUUID(), project_token: token, sku, position: list.reduce((mx, y) => Math.max(mx, y.position), 0) + 1,
      status: "draft", fields: {}, photos: [], result: null, state: {}, state_rev: 0, edited_final: null, edited_at: null,
      notify_status: null, submitted_at: null, claimed_at: null, synced_at: null, created_at: t, updated_at: t,
    };
    products.set(x.id, x);
    touch(p);
    return json({ id: x.id, sku: x.sku, status: x.status, position: x.position }, 201);
  }
  if (rest === "/close" && m === "POST") {
    const b = await readJson(req);
    if (b.confirm !== true) fail(400, "confirm_required", "要結束專案請帶 confirm:true");
    return json(await closeProject(p));
  }
  if (rest === "/submit" && m === "POST") {
    requireOpen(p);
    const b = await readJson(req);
    const ids = b.product_ids;
    if (!Array.isArray(ids) || ids.length < 1 || ids.length > MAX_PRODUCTS || ids.some((i) => typeof i !== "string")) {
      fail(400, "bad_request", `product_ids 要是 1–${MAX_PRODUCTS} 個商品 id`);
    }
    const list = [...new Set(ids as string[])].map((id) => {
      if (!UUID_RE.test(id)) fail(400, "bad_product", "有商品不屬於這期專案");
      const x = products.get(id.toLowerCase());
      if (!x || x.project_token !== token) fail(400, "bad_product", "有商品不屬於這期專案");
      return x!;
    });
    if (b.fields !== undefined && list.length === 1 && list[0].status === "draft") {
      list[0].fields = cleanFields(p, b.fields);
      touch(list[0]);
    }
    const done = list.filter((x) => x.status === "ready" || x.status === "finalized");
    if (done.length) fail(409, "bad_status", `「${done.map((x) => x.sku).join("、")}」的文案已經寫好了，不能重新送出`);
    const already = list.filter((x) => x.status === "submitted" || x.status === "generating");
    const fresh = list.filter((x) => x.status === "draft");
    const items = fresh.map((x) => ({ id: x.id, sku: x.sku, missing: missingOf(x) })).filter((it) => it.missing.length);
    if (items.length) fail(400, "incomplete", "有商品還沒填完，全部都還沒送出", { items });
    const t = now();
    for (const x of fresh) { x.status = "submitted"; x.submitted_at = t; x.updated_at = t; }
    let notify: string | null = null;
    if (fresh.length) {
      notify = await sendWebhook({ event: "panel.products.submitted", project_token: token, ...webhookLists(fresh), submitted_at: t });
      for (const x of fresh) x.notify_status = notify;
    }
    return json({ submitted: fresh.map((x) => x.id), already: already.map((x) => x.id), notify_status: notify });
  }

  const mm = rest.match(/^\/products\/([^/]+)(\/[a-z-]+)?$/);
  if (!mm) return fail(404, "no_route", "沒有這個功能");
  const x = getProduct(p, mm[1]);
  const action = mm[2] ?? "";
  if (action === "" && m === "GET") return json(productView(p, x, origin, 1, false));
  requireOpen(p);
  if (action === "" && m === "PATCH") {
    requireDraft(x);
    const b = await readJson(req);
    const sku = checkSku(b.sku);
    if (productsOf(token).some((y) => y.id !== x.id && y.sku === sku)) fail(409, "duplicate_sku", "這個商品編號在這期專案裡已經有了");
    x.sku = sku;
    touch(x);
    return json({ id: x.id, sku: x.sku, status: x.status, position: x.position });
  }
  if (action === "" && m === "DELETE") {
    requireDraft(x);
    removeFiles(`${token}/${x.id}/`);
    products.delete(x.id);
    return json({ deleted: x.id });
  }
  if (action === "/upload-url" && m === "POST") {
    requireDraft(x);
    const b = await readJson(req);
    const ext = PHOTO_TYPES[String(b.type)];
    if (!ext) fail(400, "bad_type", "只接受 JPG、PNG、WebP 照片");
    if (typeof b.size !== "number" || b.size <= 0) fail(400, "bad_size", "檔案大小不正確");
    if ((b.size as number) > MAX_PHOTO_BYTES) fail(413, "too_large", "照片太大了（上限 8MB）");
    if (x.photos.length >= MAX_PHOTOS) fail(409, "too_many", `每個商品最多 ${MAX_PHOTOS} 張照片`);
    const path = `${token}/${x.id}/${Date.now().toString(36)}-${randomToken(6)}.${ext}`;
    const upTok = randomToken(16);
    uploadTokens.set(upTok, path);
    return json({ path, signed_url: `${origin}/storage/upload/${path}?token=${upTok}`, token: upTok });
  }
  if (action === "/photos" && m === "POST") {
    requireDraft(x);
    const b = await readJson(req);
    const path = String(b.path ?? "");
    const prefix = `${token}/${x.id}/`;
    if (!path.startsWith(prefix) || path.includes("..") || path.slice(prefix.length).includes("/")) fail(400, "bad_path", "照片路徑不正確");
    if (!storage.has(path)) fail(400, "not_uploaded", "找不到剛上傳的照片，請再試一次");
    if (!x.photos.some((ph) => ph.path === path)) {
      if (x.photos.length >= MAX_PHOTOS) fail(409, "too_many", `每個商品最多 ${MAX_PHOTOS} 張照片`);
      const order = x.photos.reduce((mx, ph) => Math.max(mx, ph.order), 0) + 1;
      x.photos.push({ path, name: String(b.name ?? "").slice(0, 200), size: storage.get(path)!.bytes.length, order });
      touch(x);
    }
    return json({ photos: x.photos });
  }
  if (action === "/photos" && m === "DELETE") {
    requireDraft(x);
    const b = await readJson(req);
    const path = String(b.path ?? "");
    if (!path.startsWith(`${token}/${x.id}/`)) fail(400, "bad_path", "照片路徑不正確");
    x.photos = x.photos.filter((ph) => ph.path !== path);
    storage.delete(path);
    touch(x);
    return json({ photos: x.photos });
  }
  if (action === "/fields" && m === "PUT") {
    requireDraft(x);
    const b = await readJson(req);
    x.fields = cleanFields(p, b.fields);
    touch(x);
    return json({ fields: x.fields });
  }
  if (action === "/state" && m === "PUT") {
    if (x.status !== "ready" && x.status !== "finalized") fail(409, "not_ready", "文案還沒寫好");
    const b = await readJson(req);
    if (b.rev !== x.state_rev) fail(409, "stale", "其他裝置已經更新過，已載入最新版本", { state: x.state, state_rev: x.state_rev });
    const state = validateState(b.state);
    x.state = state;
    x.state_rev += 1;
    x.status = state.final ? "finalized" : "ready";
    touch(x);
    return json({ state_rev: x.state_rev, status: x.status });
  }
  if (action === "/edited-final" && m === "PUT") {
    if (x.status !== "finalized") fail(409, "not_finalized", "定稿後才能貼修改後的文案");
    const b = await readJson(req);
    if (typeof b.text !== "string") fail(400, "bad_text", "text 必須是文字");
    if ((b.text as string).length > MAX_EDITED) fail(400, "too_long", `修改後的文案最多 ${MAX_EDITED} 字`);
    x.edited_final = b.text ? b.text as string : null;
    x.edited_at = x.edited_final === null ? null : now();
    touch(x);
    return json({ edited_at: x.edited_at });
  }
  return fail(404, "no_route", "沒有這個功能");
}

// ---------- Bot 端 ----------
function counts(token: string) {
  const c: Record<string, number> = Object.fromEntries(STATUSES.map((s) => [s, 0]));
  for (const x of productsOf(token)) c[x.status] += 1;
  return c;
}

async function botRoute(req: Request, rest: string, url: URL): Promise<Response> {
  const given = req.headers.get("x-bot-key") ?? "";
  if (!constantTimeEqual(await sha256hex(given), config.get("bot_key_sha256") ?? "")) fail(401, "unauthorized", "Bot 金鑰不正確");
  const m = req.method;
  const origin = url.origin;
  if (rest === "/config" && m === "GET") return json({ webhook_url: config.get("webhook_url") ?? null, webhook_key_set: config.has("webhook_key") });
  if (rest === "/config" && m === "PUT") {
    const b = await readJson(req);
    if (b.clear_webhook === true) { config.delete("webhook_url"); config.delete("webhook_key"); }
    if (typeof b.webhook_url === "string") {
      if (!/^https?:\/\//.test(b.webhook_url)) fail(400, "bad_url", "webhook 網址必須是 http(s)");
      config.set("webhook_url", b.webhook_url);
    }
    if (typeof b.webhook_key === "string") config.set("webhook_key", b.webhook_key);
    return json({ webhook_url: config.get("webhook_url") ?? null, webhook_key_set: config.has("webhook_key") });
  }
  if (rest === "/projects" && m === "POST") {
    const b = await readJson(req);
    const cats = Array.isArray(b.categories) ? [...new Set(b.categories.filter((c) => typeof c === "string"))] : [];
    const token = randomToken(32);
    const t = now();
    projects.set(token, {
      token, title: typeof b.title === "string" ? b.title.slice(0, 200) : "", status: "open", options: { categories: cats },
      created_at: t, updated_at: t, closed_at: null, close_notify_status: null, expires_at: null,
    });
    return json({ token }, 201);
  }
  if (rest === "/projects" && m === "GET") {
    const status = url.searchParams.get("status");
    if (status && status !== "open" && status !== "closed") fail(400, "bad_status", "status 只能是 open 或 closed");
    return json([...projects.values()].filter((p) => !status || p.status === status)
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .map((p) => ({ token: p.token, title: p.title, status: p.status, created_at: p.created_at, closed_at: p.closed_at, counts: counts(p.token) })));
  }
  if (rest === "/queue" && m === "GET") {
    const statuses = (url.searchParams.get("status") || "submitted").split(",").map((s) => s.trim()).filter(Boolean);
    if (statuses.some((s) => !STATUSES.includes(s))) fail(400, "bad_status", "status 不正確");
    const only = url.searchParams.get("project");
    if (only) getProject(only, { allowExpired: true });
    const rows = [...products.values()].filter((x) => statuses.includes(x.status) && (!only || x.project_token === only));
    rows.sort((a, b) => (a.submitted_at ?? "").localeCompare(b.submitted_at ?? "") || a.position - b.position);
    return json(rows.map((x) => {
      const p = projects.get(x.project_token)!;
      return {
        project_token: p.token, project_title: p.title, project_status: p.status, product_id: x.id, sku: x.sku, name: nameOf(x),
        status: x.status, submitted_at: x.submitted_at, claimed_at: x.claimed_at, updated_at: x.updated_at,
      };
    }));
  }
  const mm = rest.match(/^\/projects\/([^/]+)(?:\/products\/([^/]+))?(\/[a-z]+)?$/);
  if (!mm) return fail(404, "no_route", "沒有這個功能");
  const [, token, pid, action = ""] = mm;
  const p = getProject(token, { allowExpired: true });

  if (!pid) {
    if (action === "" && m === "GET") {
      return json({
        token: p.token, title: p.title, status: p.status, closed_at: p.closed_at, close_notify_status: p.close_notify_status,
        options: p.options, created_at: p.created_at,
        products: productsOf(token).map((x) => ({
          id: x.id, sku: x.sku, name: nameOf(x), status: x.status, position: x.position, photo_count: x.photos.length,
          submitted_at: x.submitted_at, claimed_at: x.claimed_at, synced_at: x.synced_at, notify_status: x.notify_status,
          has_edited_final: !!x.edited_final, updated_at: x.updated_at,
        })),
      });
    }
    if (action === "" && m === "DELETE") {
      const list = productsOf(token);
      for (const x of list) products.delete(x.id);
      removeFiles(`${token}/`);
      projects.delete(token);
      return json({ deleted: token, products_removed: list.length });
    }
    if (action === "/close" && m === "POST") return json(await closeProject(p));
    if (action === "/reopen" && m === "POST") {
      p.status = "open";
      p.closed_at = null;
      p.expires_at = null;
      touch(p);
      return json({ status: p.status });
    }
    if (action === "/export" && m === "GET") {
      return json({
        token: p.token, title: p.title, status: p.status, closed_at: p.closed_at, exported_at: now(),
        products: productsOf(token).map((x) => {
          const st = x.state as Record<string, unknown>;
          const res = x.result as { routes?: Record<string, unknown>; product_summary?: string } | null;
          return {
            id: x.id, sku: x.sku, position: x.position, status: x.status, fields: x.fields,
            product_summary: res?.product_summary ?? null, routes: res?.routes ?? null,
            state: { draft: st.draft ?? [], hearts: st.hearts ?? [], dislikes: st.dislikes ?? [], banned_words: st.banned_words ?? [], final: st.final ?? null },
            edited_final: x.edited_final, edited_at: x.edited_at,
          };
        }),
      });
    }
    return fail(404, "no_route", "沒有這個功能");
  }

  const x = getProduct(p, pid);
  if (action === "" && m === "GET") return json(productView(p, x, origin, 2, true));
  if (action === "/claim" && m === "POST") {
    const b = await readJson(req);
    if (p.status === "closed") fail(409, "project_closed", "這期專案已經結束");
    // 原子：檢查與更新之間沒有 await
    if (x.status === "submitted" || (x.status === "generating" && b.force === true)) {
      x.status = "generating";
      x.claimed_at = now();
      touch(x);
      return json({ claimed: true, status: x.status, claimed_at: x.claimed_at });
    }
    if (x.status === "generating") return json({ claimed: false, status: x.status, claimed_at: x.claimed_at });
    return json({ claimed: false, status: x.status });
  }
  if (action === "/result" && m === "POST") {
    if (p.status === "closed") fail(409, "project_closed", "這期專案已經結束");
    if (!["submitted", "generating", "ready"].includes(x.status)) fail(409, "bad_status", `目前狀態是 ${x.status}，不能上傳句子`);
    const b = await readJson(req);
    const routes = b.routes as Record<string, unknown>;
    if (!routes || typeof routes !== "object") fail(400, "bad_result", "routes 格式不正確");
    const out: Record<string, unknown[]> = {};
    for (const r of ROUTES) {
      const list = routes[r] ?? [];
      if (!Array.isArray(list)) fail(400, "bad_result", `路線 ${r} 格式不正確`);
      out[r] = (list as Record<string, unknown>[]).map((s, i) => {
        if (!s || typeof s.text !== "string" || !s.text.trim()) fail(400, "bad_result", `路線 ${r} 第 ${i + 1} 句沒有文字`);
        return { id: `${r}${i + 1}`, text: s.text, check: s.check === true };
      });
    }
    x.result = { routes: out, product_summary: typeof b.product_summary === "string" ? b.product_summary : "", generated_at: now() };
    x.state = {};
    x.state_rev += 1;
    x.edited_final = null;
    x.edited_at = null;
    x.status = "ready";
    touch(x);
    return json({ status: x.status, counts: Object.fromEntries(ROUTES.map((r) => [r, out[r].length])) });
  }
  if (action === "/synced" && m === "POST") {
    x.synced_at = now();
    return json({ synced_at: x.synced_at });
  }
  return fail(404, "no_route", "沒有這個功能");
}

// ---------- 靜態檔（套用 vercel.json 的標頭） ----------
const MIME: Record<string, string> = {
  html: "text/html; charset=utf-8", js: "text/javascript; charset=utf-8", css: "text/css; charset=utf-8",
  json: "application/json", svg: "image/svg+xml", png: "image/png", ico: "image/x-icon",
};
const SECURITY_HEADERS: Record<string, string> = {};
try {
  const vj = JSON.parse(await Deno.readTextFile(VERCEL_JSON));
  for (const h of vj.headers?.find((x: { source: string }) => x.source === "/(.*)")?.headers ?? []) {
    if (h.key !== "Cache-Control" && h.key !== "Strict-Transport-Security") SECURITY_HEADERS[h.key] = h.value;
  }
} catch { /* 沒有 vercel.json 就不加 */ }

async function staticFile(pathname: string, origin: string): Promise<Response> {
  const headers = { ...SECURITY_HEADERS, "cache-control": "no-store" };
  if (pathname === "/config.js") {
    return new Response(`window.PANEL_CONFIG = { api: ${JSON.stringify(origin + API_PREFIX)} };\n`, { headers: { ...headers, "content-type": MIME.js } });
  }
  let rel = pathname === "/" || /^\/[ps]\/[^/]+\/?$/.test(pathname) ? "index.html" : pathname.slice(1);
  if (rel.includes("..")) rel = "index.html";
  try {
    const data = await Deno.readFile(new URL(rel, PUBLIC_DIR));
    return new Response(data, { headers: { ...headers, "content-type": MIME[rel.split(".").pop()!] ?? "application/octet-stream" } });
  } catch {
    return new Response("Not found", { status: 404 });
  }
}

export async function handler(req: Request): Promise<Response> {
  const url = new URL(req.url);
  const origin = url.origin;
  const p = decodeURIComponent(url.pathname);
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  try {
    if (p.startsWith(API_PREFIX + "/")) {
      const rest = p.slice(API_PREFIX.length);
      const user = rest.match(/^\/p\/([^/]+)(\/.*)?$/);
      if (user) {
        if (DELAY_MS) await new Promise((r) => setTimeout(r, DELAY_MS));
        return await userRoute(req, user[1], user[2] ?? "", origin);
      }
      if (rest.startsWith("/bot/")) return await botRoute(req, rest.slice(4), url);
      return fail(404, "no_route", "沒有這個功能");
    }
    if (p.startsWith("/storage/upload/") && req.method === "PUT") {
      const path = p.slice("/storage/upload/".length);
      if (uploadTokens.get(url.searchParams.get("token") ?? "") !== path) return json({ error: "bad_token" }, 403);
      const bytes = new Uint8Array(await req.arrayBuffer());
      if (bytes.length > MAX_PHOTO_BYTES) return json({ error: "too_large" }, 413);
      storage.set(path, { bytes, type: req.headers.get("content-type") ?? "application/octet-stream" });
      uploadTokens.delete(url.searchParams.get("token")!);
      return json({ Key: `healer-panel/${path}` });
    }
    if (p.startsWith("/storage/object/") && req.method === "GET") {
      const obj = storage.get(p.slice("/storage/object/".length));
      if (!obj || Number(url.searchParams.get("exp")) < Date.now()) return new Response("Not found", { status: 404, headers: CORS });
      return new Response(obj.bytes, { headers: { ...CORS, "content-type": obj.type } });
    }
    if (p === "/dev/webhook" && req.method === "POST") {
      webhookLog.push({ authorization: req.headers.get("authorization"), body: await req.json() });
      return json({ ok: true });
    }
    if (p === "/dev/webhooks") return json(webhookLog);
    if (p === "/dev/storage") return json([...storage.keys()]);
    return await staticFile(p, origin);
  } catch (e) {
    if (e instanceof ApiError) return json({ error: e.code, message: e.message, ...e.extra }, e.status);
    console.error(e);
    return json({ error: "internal", message: "伺服器發生錯誤" }, 500);
  }
}

if (import.meta.main) {
  Deno.serve({
    port: PORT,
    onListen: ({ port }) => console.log(`mock panel-api v2 on http://localhost:${port}  (bot key: ${BOT_KEY === "dev-bot-key" ? "dev-bot-key" : "(from MOCK_BOT_KEY)"}${DELAY_MS ? `, delay ${DELAY_MS}ms` : ""})`),
  }, handler);
}
