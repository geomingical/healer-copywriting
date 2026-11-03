// panel-api v2 的所有路由與驗證（專案＋多商品）。不直接依賴 Supabase：資料庫、儲存空間、fetch、時間、亂數
// 都由外部注入，讓 handler_test.ts 可以用記憶體假物件測試。

export type ProjectStatus = "open" | "closed";
export type ProductStatus = "draft" | "submitted" | "generating" | "ready" | "finalized";
export const PRODUCT_STATUSES: ProductStatus[] = ["draft", "submitted", "generating", "ready", "finalized"];

export interface Photo {
  path: string;
  name: string;
  size: number;
  order: number;
}

export interface ProjectRow {
  token: string;
  title: string;
  status: ProjectStatus;
  options: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  close_notify_status: string | null;
  expires_at: string | null;
}

export interface ProductRow {
  id: string;
  project_token: string;
  sku: string;
  position: number;
  status: ProductStatus;
  fields: Record<string, unknown>;
  photos: Photo[];
  result: Record<string, unknown> | null;
  state: Record<string, unknown>;
  state_rev: number;
  edited_final: string | null;
  edited_at: string | null;
  notify_status: string | null;
  submitted_at: string | null;
  claimed_at: string | null;
  synced_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProductWhere {
  status?: ProductStatus[];
  state_rev?: number;
}

/** 待處理清單的一列：商品欄位＋所屬專案的標題與狀態。 */
export type QueueRow =
  & Pick<
    ProductRow,
    "id" | "project_token" | "sku" | "position" | "status" | "fields" | "submitted_at" | "claimed_at" | "updated_at"
  >
  & { project_title: string; project_status: ProjectStatus };

export type PhotoOpResult =
  | { photos: Photo[] }
  | { error: "not_found" | "not_draft" | "project_closed" | "too_many" };

export interface Db {
  getConfig(): Promise<Record<string, string>>;
  setConfig(entries: Record<string, string>): Promise<void>;
  deleteConfig(keys: string[]): Promise<void>;

  getProject(token: string): Promise<ProjectRow | null>;
  insertProject(row: { token: string; title: string; options: Record<string, unknown> }): Promise<void>;
  /** 有條件更新（單一 UPDATE … WHERE … RETURNING）；條件不符或不存在時回 null。 */
  updateProject(
    token: string,
    patch: Partial<ProjectRow>,
    where?: { status?: ProjectStatus[] },
  ): Promise<ProjectRow | null>;
  /** 最新建立的在前，最多 200 筆。 */
  listProjects(status?: ProjectStatus): Promise<ProjectRow[]>;
  /** 連同商品一起刪（FK cascade）。 */
  deleteProject(token: string): Promise<boolean>;

  /** 專案內所有商品，依 position 排序。 */
  listProducts(projectToken: string): Promise<ProductRow[]>;
  listProductStatuses(projectTokens: string[]): Promise<{ project_token: string; status: ProductStatus }[]>;
  getProduct(projectToken: string, id: string): Promise<ProductRow | null>;
  /** sku 在專案內重複時回 "duplicate_sku"。 */
  insertProduct(row: { project_token: string; sku: string; position: number }): Promise<ProductRow | "duplicate_sku">;
  /** 有條件更新單一商品；條件不符或不存在時回 null。 */
  updateProduct(projectToken: string, id: string, patch: Partial<ProductRow>, where?: ProductWhere): Promise<
    ProductRow | null
  >;
  /** 只在 status=draft 時改 sku；不存在或不是 draft 回 null；重複回 "duplicate_sku"。 */
  renameProduct(projectToken: string, id: string, sku: string, updatedAt: string): Promise<
    ProductRow | null | "duplicate_sku"
  >;
  /** 有條件批次更新（單一 UPDATE）；回傳實際被更新的列。 */
  updateProducts(projectToken: string, ids: string[], patch: Partial<ProductRow>, where?: ProductWhere): Promise<
    ProductRow[]
  >;
  deleteProduct(projectToken: string, id: string, where?: ProductWhere): Promise<boolean>;
  /** 跨專案（或指定專案）的商品，status 在清單內；最多 500 筆。 */
  listQueue(statuses: ProductStatus[], projectToken?: string): Promise<QueueRow[]>;

  addPhoto(productId: string, photo: { path: string; name: string; size: number }, max: number): Promise<
    PhotoOpResult
  >;
  removePhoto(productId: string, path: string): Promise<PhotoOpResult>;
}

export interface StoredObject {
  size: number;
  mimetype?: string;
}

export interface Storage {
  createSignedUploadUrl(path: string): Promise<{ signed_url: string; token: string }>;
  /** 回傳與 paths 同順序的簽名下載網址；不存在的檔案回 null。 */
  signedUrls(paths: string[], expiresIn: number): Promise<(string | null)[]>;
  stat(path: string): Promise<StoredObject | null>;
  /** 遞迴列出 prefix 資料夾下（含子資料夾）所有檔案的完整路徑。 */
  listFolder(prefix: string): Promise<string[]>;
  remove(paths: string[]): Promise<void>;
}

export interface HandlerConfig {
  /** 環境變數後備：PANEL_WEBHOOK_URL／PANEL_WEBHOOK_KEY。 */
  webhookUrl?: string;
  webhookKey?: string;
}

export interface Deps {
  db: Db;
  storage: Storage;
  config?: HandlerConfig;
  fetch?: typeof fetch;
  now?: () => Date;
  random?: (n: number) => Uint8Array;
  log?: (msg: string) => void;
}

// ---- 限制 ----
export const LIMITS = {
  maxPhotos: 20,
  maxPhotoBytes: 8 * 1024 * 1024,
  photoTypes: { "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp" } as Record<string, string>,
  maxStateBytes: 64 * 1024,
  maxBodyBytes: 256 * 1024,
  nameMax: 120,
  fieldMax: 1000,
  maxColors: 30,
  maxCategories: 300,
  categoryMax: 100,
  titleMax: 200,
  skuMax: 40,
  maxProducts: 30,
  maxSubmit: 30,
  editedFinalMax: 5000,
  webhookNameMax: 60,
  maxSentencesPerRoute: 200,
  sentenceMax: 1000,
  summaryMax: 4000,
  webhookTimeoutMs: 5000,
  userUrlTtl: 3600,
  botUrlTtl: 7200,
  closedRetentionDays: 180,
};

const ROUTES = ["A", "B", "C"] as const;
const FIELD_KEYS = ["name", "category", "colors", "material", "sizes", "price", "background", "feelings"] as const;
const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
// 字母數字、-_.、中日韓文字（漢字、平假名、片假名含長音符、韓文）；不含空白與斜線。
const SKU_RE = /^[A-Za-z0-9\-_.\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}ー々]+$/u;

const CORS: Record<string, string> = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "content-type, x-bot-key",
  "access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
  "access-control-max-age": "86400",
};

class HttpError extends Error {
  constructor(public status: number, public code: string, message: string, public extra?: Record<string, unknown>) {
    super(message);
  }
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

function fail(status: number, code: string, message: string, extra?: Record<string, unknown>): never {
  throw new HttpError(status, code, message, extra);
}

// ---- 小工具 ----
function toHex(buf: ArrayBuffer): string {
  return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha256Hex(text: string): Promise<string> {
  return toHex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
}

/** 常數時間比較兩個字串（長度本身不是秘密：都是 64 字元 hex）。 */
export function timingSafeEqual(a: string, b: string): boolean {
  const ea = new TextEncoder().encode(a);
  const eb = new TextEncoder().encode(b);
  let diff = ea.length ^ eb.length;
  const n = Math.max(ea.length, eb.length);
  for (let i = 0; i < n; i++) diff |= (ea[i] ?? 0) ^ (eb[i] ?? 0);
  return diff === 0;
}

function base64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function emptyState() {
  return { draft: [], hearts: [], dislikes: [], banned_words: [], final: null };
}

function productName(p: { fields?: Record<string, unknown> | null }): string {
  return String(p.fields?.name ?? "");
}

function finalText(p: ProductRow): string | null {
  const f = (p.state ?? {}).final;
  return isObj(f) && typeof f.text === "string" ? f.text : null;
}

// ---- 驗證 ----
/** 回傳整理後的 sku，或 null（格式不符）。 */
export function normalizeSku(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const s = v.trim();
  if (!s || s.length > LIMITS.skuMax || !SKU_RE.test(s) || /^\.+$/.test(s)) return null;
  return s;
}

function requireSku(v: unknown): string {
  const sku = normalizeSku(v);
  if (!sku) {
    fail(
      400,
      "invalid_sku",
      `商品編號格式不正確：必填、最多 ${LIMITS.skuMax} 字，只能用英文字母、數字、-_. 與中日韓文字，不能有空白或斜線`,
    );
  }
  return sku;
}

function validateFields(input: unknown, categories: string[]): Record<string, unknown> {
  if (!isObj(input)) fail(400, "invalid_fields", "fields 必須是物件");
  const out: Record<string, unknown> = {};
  for (const key of FIELD_KEYS) {
    const v = input[key];
    if (key === "colors") {
      if (v === undefined || v === null) {
        out.colors = [];
        continue;
      }
      if (!Array.isArray(v) || v.some((c) => typeof c !== "string")) {
        fail(400, "invalid_fields", "colors 必須是文字清單");
      }
      const colors = (v as string[]).map((c) => c.trim()).filter((c) => c);
      if (colors.length > LIMITS.maxColors) fail(400, "invalid_fields", `色名最多 ${LIMITS.maxColors} 個`);
      if (colors.some((c) => c.length > LIMITS.fieldMax)) fail(400, "invalid_fields", "色名太長");
      out.colors = colors;
      continue;
    }
    if (v === undefined || v === null) {
      out[key] = "";
      continue;
    }
    if (typeof v !== "string") fail(400, "invalid_fields", `${key} 必須是文字`);
    const max = key === "name" ? LIMITS.nameMax : LIMITS.fieldMax;
    if (v.length > max) fail(400, "invalid_fields", `${key} 最多 ${max} 字`);
    out[key] = v;
  }
  const cat = (out.category as string).trim();
  if (cat && !categories.includes(cat)) fail(400, "invalid_category", "類別不在可選清單內");
  out.category = cat;
  return out;
}

function missingFields(p: ProductRow): string[] {
  const fields = p.fields ?? {};
  const missing: string[] = [];
  if (!normalizeSku(p.sku)) missing.push("sku");
  if (!String(fields.name ?? "").trim()) missing.push("name");
  if (!String(fields.category ?? "").trim()) missing.push("category");
  if (!Array.isArray(fields.colors) || fields.colors.length === 0) missing.push("colors");
  if (!String(fields.material ?? "").trim()) missing.push("material");
  if ((p.photos ?? []).length === 0) missing.push("photos");
  return missing;
}

const MISSING_LABEL: Record<string, string> = {
  sku: "商品編號",
  name: "商品名稱",
  category: "類別",
  colors: "色名",
  material: "材質",
  photos: "照片",
};

const STATUS_LABEL: Record<ProductStatus, string> = {
  draft: "草稿",
  submitted: "已送出",
  generating: "撰寫中",
  ready: "可挑句",
  finalized: "已定稿",
};

function strArray(v: unknown, what: string, maxItems: number): string[] {
  if (v === undefined) return [];
  if (!Array.isArray(v) || v.some((x) => typeof x !== "string")) fail(400, "invalid_state", `${what} 必須是文字清單`);
  if (v.length > maxItems) fail(400, "invalid_state", `${what} 太多`);
  return v as string[];
}

function validateState(input: unknown, nowIso: string): Record<string, unknown> {
  if (!isObj(input)) fail(400, "invalid_state", "state 必須是物件");
  const size = new TextEncoder().encode(JSON.stringify(input)).length;
  if (size > LIMITS.maxStateBytes) fail(413, "state_too_large", "挑選內容太大（上限 64KB）");
  const known = new Set(["draft", "hearts", "dislikes", "banned_words", "final"]);
  for (const k of Object.keys(input)) {
    if (!known.has(k)) fail(400, "invalid_state", `state 有不認得的欄位：${k}`);
  }
  let draft: Record<string, string>[] = [];
  if (input.draft !== undefined) {
    if (!Array.isArray(input.draft)) fail(400, "invalid_state", "draft 必須是清單");
    if (input.draft.length > 500) fail(400, "invalid_state", "draft 太多");
    draft = input.draft.map((d) => {
      if (!isObj(d) || typeof d.id !== "string" || typeof d.text !== "string") {
        fail(400, "invalid_state", "draft 每一項要有 id 與 text 文字");
      }
      if (d.origin !== undefined && d.origin !== null && typeof d.origin !== "string") {
        fail(400, "invalid_state", "draft.origin 必須是文字");
      }
      const item: Record<string, string> = { id: d.id, text: d.text };
      if (typeof d.origin === "string") item.origin = d.origin;
      return item;
    });
  }
  const hearts = strArray(input.hearts, "hearts", 1000);
  const dislikes = strArray(input.dislikes, "dislikes", 1000);
  const banned = strArray(input.banned_words, "banned_words", 200);
  let final: { text: string; saved_at: string } | null = null;
  if (input.final !== undefined && input.final !== null) {
    const f = input.final;
    if (!isObj(f) || typeof f.text !== "string") fail(400, "invalid_state", "final 必須是 {text, saved_at} 或 null");
    if (f.saved_at !== undefined && typeof f.saved_at !== "string") {
      fail(400, "invalid_state", "final.saved_at 必須是文字");
    }
    final = { text: f.text, saved_at: (f.saved_at as string | undefined) || nowIso };
  }
  return { draft, hearts, dislikes, banned_words: banned, final };
}

function validateResult(body: Record<string, unknown>, nowIso: string): Record<string, unknown> {
  const routesIn = body.routes;
  if (!isObj(routesIn)) fail(400, "invalid_result", "routes 必須是物件 {A:[...],B:[...],C:[...]}");
  const routes: Record<string, { id: string; text: string; check: boolean }[]> = {};
  for (const r of ROUTES) {
    const list = routesIn[r] ?? [];
    if (!Array.isArray(list)) fail(400, "invalid_result", `routes.${r} 必須是清單`);
    if (list.length > LIMITS.maxSentencesPerRoute) fail(400, "invalid_result", `routes.${r} 句子太多`);
    routes[r] = list.map((s, i) => {
      if (!isObj(s) || typeof s.text !== "string" || !s.text.trim()) {
        fail(400, "invalid_result", `routes.${r}[${i}] 要有非空的 text`);
      }
      if (s.text.length > LIMITS.sentenceMax) fail(400, "invalid_result", `routes.${r}[${i}] 太長`);
      if (s.check !== undefined && typeof s.check !== "boolean") {
        fail(400, "invalid_result", `routes.${r}[${i}].check 必須是 true/false`);
      }
      return { id: `${r}${i + 1}`, text: s.text, check: s.check === true };
    });
  }
  for (const k of Object.keys(routesIn)) {
    if (!(ROUTES as readonly string[]).includes(k)) fail(400, "invalid_result", `不認得的路線：${k}`);
  }
  if (routes.A.length + routes.B.length + routes.C.length === 0) fail(400, "invalid_result", "至少要有一句");
  const summary = body.product_summary ?? "";
  if (typeof summary !== "string") fail(400, "invalid_result", "product_summary 必須是文字");
  if (summary.length > LIMITS.summaryMax) fail(400, "invalid_result", "product_summary 太長");
  return { routes, product_summary: summary, generated_at: nowIso };
}

function validateCategories(v: unknown): string[] {
  const cats = v ?? [];
  if (!Array.isArray(cats) || cats.some((c) => typeof c !== "string")) {
    fail(400, "invalid_categories", "categories 必須是文字清單");
  }
  const clean = [...new Set((cats as string[]).map((c) => c.trim()).filter((c) => c))];
  if (clean.length > LIMITS.maxCategories || clean.some((c) => c.length > LIMITS.categoryMax)) {
    fail(400, "invalid_categories", "categories 太多或太長");
  }
  return clean;
}

// ---- 主體 ----
export function createHandler(deps: Deps): (req: Request) => Promise<Response> {
  const { db, storage } = deps;
  const env = deps.config ?? {};
  const doFetch = deps.fetch ?? fetch;
  const now = deps.now ?? (() => new Date());
  const random = deps.random ?? ((n: number) => crypto.getRandomValues(new Uint8Array(n)));
  const log = deps.log ?? ((m: string) => console.error(m));
  const nowIso = () => now().toISOString();

  async function readBody(req: Request): Promise<Record<string, unknown>> {
    const text = await req.text();
    if (new TextEncoder().encode(text).length > LIMITS.maxBodyBytes) fail(413, "body_too_large", "資料太大");
    if (!text.trim()) return {};
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      fail(400, "invalid_json", "資料格式錯誤（不是 JSON）");
    }
    if (!isObj(parsed)) fail(400, "invalid_json", "資料必須是 JSON 物件");
    return parsed;
  }

  async function requireBot(req: Request) {
    const key = req.headers.get("x-bot-key");
    if (!key) fail(401, "unauthorized", "缺少 Bot 金鑰");
    const cfg = await db.getConfig();
    const expected = (cfg.bot_key_sha256 ?? "").trim().toLowerCase();
    const got = await sha256Hex(key);
    // 即使沒設定金鑰也照樣做一次比較，避免時間差洩漏設定狀態。
    const ok = timingSafeEqual(got, expected || "0".repeat(64)) && expected.length === 64;
    if (!ok) fail(401, "unauthorized", "Bot 金鑰錯誤");
  }

  function isExpired(p: ProjectRow): boolean {
    return !!p.expires_at && new Date(p.expires_at).getTime() < now().getTime();
  }

  async function loadProject(token: string, opts: { allowExpired?: boolean } = {}): Promise<ProjectRow> {
    const msg = "找不到這個專案連結，請回聊天室跟小編要新連結";
    if (!TOKEN_RE.test(token)) fail(404, "not_found", msg);
    const row = await db.getProject(token);
    if (!row) fail(404, "not_found", msg);
    if (!opts.allowExpired && isExpired(row)) fail(404, "not_found", "這個專案連結已過期，請回聊天室跟小編要新連結");
    return row;
  }

  function requireOpen(p: ProjectRow) {
    if (p.status !== "open") fail(409, "project_closed", "這期專案已結束，只能檢視，不能再新增或修改");
  }

  async function loadProduct(project: ProjectRow, id: string): Promise<ProductRow> {
    if (!UUID_RE.test(id)) fail(404, "product_not_found", "找不到這個商品");
    const row = await db.getProduct(project.token, id.toLowerCase());
    if (!row) fail(404, "product_not_found", "找不到這個商品");
    return row;
  }

  function requireDraft(p: ProductRow, what: string) {
    if (p.status !== "draft") {
      fail(409, "not_draft", `這個商品已經送出（${STATUS_LABEL[p.status]}），不能再${what}`, { status: p.status });
    }
  }

  /** 條件更新失敗後，重新讀取並回報正確的錯誤（專案已結束、商品不見、狀態已變）。 */
  async function explainConflict(token: string, id: string, what: string): Promise<never> {
    const project = await db.getProject(token);
    if (!project) fail(404, "not_found", "找不到這個專案連結");
    requireOpen(project);
    const p = await db.getProduct(token, id);
    if (!p) fail(404, "product_not_found", "找不到這個商品");
    fail(409, "not_draft", `這個商品已經送出（${STATUS_LABEL[p.status]}），不能再${what}`, { status: p.status });
  }

  function categoriesOf(p: ProjectRow): string[] {
    const c = (p.options ?? {}).categories;
    return Array.isArray(c) ? c.filter((x): x is string => typeof x === "string") : [];
  }

  async function photoViews(p: ProductRow, ttl: number) {
    const sorted = [...(p.photos ?? [])].sort((a, b) => a.order - b.order);
    const urls = sorted.length ? await storage.signedUrls(sorted.map((x) => x.path), ttl) : [];
    return sorted.map((x, i) => ({ path: x.path, name: x.name, size: x.size, order: x.order, url: urls[i] ?? null }));
  }

  async function productView(project: ProjectRow, p: ProductRow, ttl: number, bot: boolean) {
    const view: Record<string, unknown> = {
      id: p.id,
      sku: p.sku,
      status: p.status,
      fields: p.fields ?? {},
      photos: await photoViews(p, ttl),
      result: p.result ?? null,
      state: p.state ?? {},
      state_rev: p.state_rev,
      edited_final: p.edited_final ?? null,
      edited_at: p.edited_at ?? null,
      updated_at: p.updated_at,
      server_time: nowIso(),
      project_status: project.status,
    };
    if (bot) {
      view.notify_status = p.notify_status ?? null;
      view.synced_at = p.synced_at ?? null;
      view.claimed_at = p.claimed_at ?? null;
      view.submitted_at = p.submitted_at ?? null;
    }
    return view;
  }

  // ---- webhook ----
  async function webhookSettings(): Promise<{ url: string; key: string }> {
    const cfg = await db.getConfig();
    return {
      url: (cfg.webhook_url ?? "").trim() || (env.webhookUrl ?? "").trim(),
      key: (cfg.webhook_key ?? "").trim() || (env.webhookKey ?? "").trim(),
    };
  }

  async function postWebhook(payload: Record<string, unknown>): Promise<string> {
    const { url, key } = await webhookSettings();
    if (!url) return "unset";
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (key) headers.authorization = `Bearer ${key}`;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), LIMITS.webhookTimeoutMs);
    try {
      const res = await doFetch(url, { method: "POST", headers, body: JSON.stringify(payload), signal: ctrl.signal });
      await res.body?.cancel();
      return res.ok ? "ok" : `failed:${res.status}`;
    } catch (e) {
      const name = e instanceof Error ? e.name : "";
      log(`webhook 失敗：${name || "error"}`);
      return name === "AbortError" ? "failed:timeout" : "failed:network";
    } finally {
      clearTimeout(timer);
    }
  }

  function productLists(products: ProductRow[]) {
    return {
      product_ids: products.map((p) => p.id),
      product_names: products.map((p) => productName(p).slice(0, LIMITS.webhookNameMax)),
      skus: products.map((p) => p.sku),
    };
  }

  // ---- 共用動作 ----
  /** 結束專案（使用者端與 Bot 端共用）。已結束時回現狀、不重複通知。 */
  async function closeProject(project: ProjectRow): Promise<ProjectRow> {
    if (project.status === "closed") return project;
    const closedAt = nowIso();
    const expiresAt = new Date(now().getTime() + LIMITS.closedRetentionDays * 86400_000).toISOString();
    const updated = await db.updateProject(
      project.token,
      { status: "closed", closed_at: closedAt, expires_at: expiresAt, updated_at: closedAt },
      { status: ["open"] },
    );
    if (!updated) {
      // 同時有另一個結束請求搶先：回目前狀態，不重複通知。
      const cur = await db.getProject(project.token);
      if (!cur) fail(404, "not_found", "找不到這個專案連結");
      return cur;
    }
    // 草稿不計入通知。
    const products = (await db.listProducts(project.token)).filter((p) => p.status !== "draft");
    const notify = await postWebhook({
      event: "panel.project.closed",
      project_token: project.token,
      ...productLists(products),
      submitted_at: closedAt,
      closed_at: closedAt,
    });
    return (await db.updateProject(project.token, { close_notify_status: notify })) ?? updated;
  }

  // ---- 使用者端 ----
  async function userRoute(req: Request, token: string, rest: string[]): Promise<Response> {
    const m = req.method;
    const [r0, pid, action] = rest;

    // GET /p/:token
    if (rest.length === 0 && m === "GET") {
      const project = await loadProject(token);
      const products = await db.listProducts(token);
      const closed = project.status === "closed";
      return json(200, {
        title: project.title,
        status: project.status,
        closed_at: project.closed_at ?? null,
        options: project.options ?? {},
        server_time: nowIso(),
        products: products.map((p) => {
          const v: Record<string, unknown> = {
            id: p.id,
            sku: p.sku,
            name: productName(p),
            status: p.status,
            position: p.position,
            photo_count: (p.photos ?? []).length,
            updated_at: p.updated_at,
            has_edited_final: !!p.edited_final,
          };
          if (closed) {
            v.final_text = finalText(p);
            v.edited_final = p.edited_final ?? null;
          }
          return v;
        }),
      });
    }

    // POST /p/:token/close
    if (rest.length === 1 && r0 === "close" && m === "POST") {
      const project = await loadProject(token);
      const body = await readBody(req);
      if (project.status === "closed") return json(200, { status: "closed", closed_at: project.closed_at });
      if (body.confirm !== true) fail(400, "confirm_required", "結束專案需要確認（confirm: true）");
      const done = await closeProject(project);
      return json(200, { status: done.status, closed_at: done.closed_at });
    }

    // POST /p/:token/submit
    if (rest.length === 1 && r0 === "submit" && m === "POST") {
      const project = await loadProject(token);
      requireOpen(project);
      return json(200, await submitProducts(project, await readBody(req)));
    }

    // POST /p/:token/products
    if (rest.length === 1 && r0 === "products" && m === "POST") {
      const project = await loadProject(token);
      requireOpen(project);
      const body = await readBody(req);
      const sku = requireSku(body.sku);
      const existing = await db.listProducts(token);
      if (existing.some((p) => p.sku === sku)) fail(409, "duplicate_sku", `商品編號「${sku}」已經在這期專案裡了`);
      if (existing.length >= LIMITS.maxProducts) {
        fail(409, "too_many_products", `每期專案最多 ${LIMITS.maxProducts} 個商品`);
      }
      const position = Math.max(0, ...existing.map((p) => p.position)) + 1;
      const row = await db.insertProduct({ project_token: token, sku, position });
      if (row === "duplicate_sku") fail(409, "duplicate_sku", `商品編號「${sku}」已經在這期專案裡了`);
      return json(201, { id: row.id, sku: row.sku, status: row.status, position: row.position });
    }

    if (r0 !== "products" || !pid || rest.length > 3) fail(404, "no_route", "沒有這個功能");

    // GET /p/:token/products/:pid
    if (rest.length === 2 && m === "GET") {
      const project = await loadProject(token);
      const p = await loadProduct(project, pid);
      return json(200, await productView(project, p, LIMITS.userUrlTtl, false));
    }

    const isWrite = (rest.length === 2 && (m === "PATCH" || m === "DELETE")) ||
      (rest.length === 3 && (
        (action === "upload-url" && m === "POST") ||
        (action === "photos" && (m === "POST" || m === "DELETE")) ||
        ((action === "fields" || action === "state" || action === "edited-final") && m === "PUT")
      ));
    if (!isWrite) fail(404, "no_route", "沒有這個功能");

    const project = await loadProject(token);
    requireOpen(project);
    const p = await loadProduct(project, pid);
    const folder = `${token}/${p.id}/`;

    // PATCH /p/:token/products/:pid  {sku}
    if (rest.length === 2 && m === "PATCH") {
      const body = await readBody(req);
      requireDraft(p, "修改商品編號");
      const sku = requireSku(body.sku);
      if (sku === p.sku) return json(200, { id: p.id, sku: p.sku });
      const r = await db.renameProduct(token, p.id, sku, nowIso());
      if (r === "duplicate_sku") fail(409, "duplicate_sku", `商品編號「${sku}」已經在這期專案裡了`);
      if (!r) return await explainConflict(token, p.id, "修改商品編號");
      return json(200, { id: r.id, sku: r.sku });
    }

    // DELETE /p/:token/products/:pid
    if (rest.length === 2 && m === "DELETE") {
      requireDraft(p, "刪除");
      const ok = await db.deleteProduct(token, p.id, { status: ["draft"] });
      if (!ok) return await explainConflict(token, p.id, "刪除");
      const paths = await storage.listFolder(`${token}/${p.id}`);
      if (paths.length) await storage.remove(paths);
      return json(200, { deleted: true, photos_removed: paths.length });
    }

    if (action === "upload-url") {
      const body = await readBody(req);
      requireDraft(p, "上傳照片");
      const type = body.type;
      const size = body.size;
      if (typeof type !== "string" || !(type in LIMITS.photoTypes)) {
        fail(400, "bad_type", "只能上傳 JPEG、PNG 或 WebP 照片");
      }
      if (typeof size !== "number" || !Number.isFinite(size) || size <= 0) fail(400, "bad_size", "檔案大小不正確");
      if (size > LIMITS.maxPhotoBytes) fail(413, "too_large", "單張照片不能超過 8MB");
      if ((p.photos ?? []).length >= LIMITS.maxPhotos) {
        fail(409, "too_many", `每個商品最多 ${LIMITS.maxPhotos} 張照片`);
      }
      const ts = now().getTime().toString(36);
      const rnd = Array.from(random(6), (b) => b.toString(16).padStart(2, "0")).join("");
      const path = `${folder}${ts}-${rnd}.${LIMITS.photoTypes[type]}`;
      const up = await storage.createSignedUploadUrl(path);
      return json(200, { path, signed_url: up.signed_url, token: up.token });
    }

    if (action === "photos") {
      const body = await readBody(req);
      requireDraft(p, "修改照片");
      const path = body.path;
      if (
        typeof path !== "string" || !path.startsWith(folder) || path.includes("..") ||
        path.slice(folder.length).includes("/") || path.length === folder.length || path.length > 300
      ) {
        fail(400, "bad_path", "照片路徑不正確");
      }
      const photoError = (r: { error: string }): never => {
        if (r.error === "too_many") fail(409, "too_many", `每個商品最多 ${LIMITS.maxPhotos} 張照片`);
        if (r.error === "project_closed") fail(409, "project_closed", "這期專案已結束，只能檢視，不能再新增或修改");
        if (r.error === "not_draft") fail(409, "not_draft", "這個商品已經送出，不能再修改照片");
        fail(404, "product_not_found", "找不到這個商品");
      };
      if (m === "DELETE") {
        const r = await db.removePhoto(p.id, path);
        if ("error" in r) return photoError(r);
        await storage.remove([path]);
        return json(200, { photos: r.photos });
      }
      const name = typeof body.name === "string" ? body.name.slice(0, 200) : "";
      const obj = await storage.stat(path);
      if (!obj) fail(400, "not_uploaded", "找不到已上傳的照片，請重新上傳");
      if (obj.size > LIMITS.maxPhotoBytes) {
        await storage.remove([path]);
        fail(413, "too_large", "單張照片不能超過 8MB");
      }
      if (obj.mimetype && !(obj.mimetype in LIMITS.photoTypes)) {
        await storage.remove([path]);
        fail(400, "bad_type", "只能上傳 JPEG、PNG 或 WebP 照片");
      }
      const r = await db.addPhoto(p.id, { path, name, size: obj.size }, LIMITS.maxPhotos);
      if ("error" in r) return photoError(r);
      return json(200, { photos: r.photos });
    }

    if (action === "fields") {
      const body = await readBody(req);
      requireDraft(p, "修改欄位");
      const fields = validateFields(body.fields, categoriesOf(project));
      const updated = await db.updateProduct(token, p.id, { fields, updated_at: nowIso() }, { status: ["draft"] });
      if (!updated) return await explainConflict(token, p.id, "修改欄位");
      return json(200, { fields: updated.fields });
    }

    if (action === "state") {
      const body = await readBody(req);
      if (p.status !== "ready" && p.status !== "finalized") {
        fail(409, "not_ready", "文案還沒產生，不能挑句子", { status: p.status });
      }
      if (typeof body.rev !== "number" || !Number.isInteger(body.rev)) fail(400, "invalid_rev", "缺少 rev");
      const state = validateState(body.state, nowIso());
      const stale = (cur: ProductRow): never =>
        fail(409, "stale", "內容在別處被修改過，已載入最新版本", { state: cur.state, state_rev: cur.state_rev });
      if (body.rev !== p.state_rev) stale(p);
      const status: ProductStatus = state.final ? "finalized" : "ready";
      const updated = await db.updateProduct(
        token,
        p.id,
        { state, state_rev: p.state_rev + 1, status, updated_at: nowIso() },
        { status: ["ready", "finalized"], state_rev: body.rev },
      );
      if (!updated) {
        const cur = await db.getProduct(token, p.id);
        if (!cur) fail(404, "product_not_found", "找不到這個商品");
        if (cur.status !== "ready" && cur.status !== "finalized") {
          fail(409, "not_ready", "文案正在重新產生", { status: cur.status });
        }
        return stale(cur);
      }
      return json(200, { state_rev: updated.state_rev, status: updated.status });
    }

    // PUT edited-final {text}
    const body = await readBody(req);
    if (p.status !== "finalized") {
      fail(409, "not_finalized", "定稿之後才能貼上修改後的最終文案", { status: p.status });
    }
    const text = body.text;
    if (typeof text !== "string") fail(400, "invalid_text", "text 必須是文字（空字串代表清除）");
    if (text.length > LIMITS.editedFinalMax) {
      fail(400, "text_too_long", `修改後的最終文案最多 ${LIMITS.editedFinalMax} 字`);
    }
    const clear = text.trim() === "";
    const editedAt = clear ? null : nowIso();
    const updated = await db.updateProduct(
      token,
      p.id,
      { edited_final: clear ? null : text, edited_at: editedAt, updated_at: nowIso() },
      { status: ["finalized"] },
    );
    if (!updated) {
      const cur = await db.getProduct(token, p.id);
      if (!cur) fail(404, "product_not_found", "找不到這個商品");
      fail(409, "not_finalized", "定稿之後才能貼上修改後的最終文案", { status: cur.status });
    }
    return json(200, { edited_at: updated.edited_at ?? null });
  }

  async function submitProducts(project: ProjectRow, body: Record<string, unknown>) {
    const token = project.token;
    const idsIn = body.product_ids;
    if (!Array.isArray(idsIn) || idsIn.length === 0 || idsIn.some((x) => typeof x !== "string")) {
      fail(400, "invalid_products", "product_ids 必須是 1 個以上的商品 id 清單");
    }
    const ids = [...new Set((idsIn as string[]).map((x) => x.toLowerCase()))];
    if (ids.length > LIMITS.maxSubmit) fail(400, "invalid_products", `一次最多送出 ${LIMITS.maxSubmit} 個商品`);

    const all = await db.listProducts(token);
    const byId = new Map(all.map((p) => [p.id, p]));
    const unknown = ids.filter((id) => !UUID_RE.test(id) || !byId.has(id));
    if (unknown.length) fail(404, "product_not_found", "有商品不在這期專案裡", { ids: unknown });
    const targets = ids.map((id) => byId.get(id)!);

    const bad = targets.filter((p) => p.status === "ready" || p.status === "finalized");
    if (bad.length) {
      fail(409, "bad_status", "有商品的文案已經產生，不能再送出", {
        items: bad.map((p) => ({ id: p.id, sku: p.sku, status: p.status })),
      });
    }

    // fields：只送一個商品時可直接給欄位物件（同 v1）；多個商品時用 {"<product id>": {...}}。
    let fieldsById = new Map<string, unknown>();
    if (body.fields !== undefined && body.fields !== null) {
      if (!isObj(body.fields)) fail(400, "invalid_fields", "fields 必須是物件");
      const keys = Object.keys(body.fields);
      const keyed = keys.length > 0 && keys.every((k) => UUID_RE.test(k));
      if (keyed) {
        for (const k of keys) {
          if (!ids.includes(k.toLowerCase())) fail(400, "invalid_fields", "fields 裡有不在 product_ids 的商品");
          fieldsById.set(k.toLowerCase(), body.fields[k]);
        }
      } else if (ids.length === 1) {
        fieldsById = new Map([[ids[0], body.fields]]);
      } else {
        fail(400, "invalid_fields", "一次送出多個商品時，fields 要用商品 id 當鍵");
      }
    }

    const drafts = targets.filter((p) => p.status === "draft");
    // 先驗證並保存欄位（只存草稿），使用者不會丟資料。
    const cats = categoriesOf(project);
    const validated = new Map<string, Record<string, unknown>>();
    for (const p of drafts) {
      if (fieldsById.has(p.id)) validated.set(p.id, validateFields(fieldsById.get(p.id), cats));
    }
    for (const p of drafts) {
      const f = validated.get(p.id);
      if (!f) continue;
      const saved = await db.updateProduct(token, p.id, { fields: f, updated_at: nowIso() }, { status: ["draft"] });
      if (saved) Object.assign(p, saved);
    }

    const items = drafts
      .map((p) => ({ id: p.id, sku: p.sku, missing: missingFields(p) }))
      .filter((x) => x.missing.length);
    if (items.length) {
      const desc = items.map((x) => `${x.sku}（${x.missing.map((k) => MISSING_LABEL[k]).join("、")}）`).join("；");
      fail(400, "incomplete", `有商品還缺必填欄位，都沒有送出：${desc}`, { items });
    }

    const already = targets.filter((p) => p.status === "submitted" || p.status === "generating").map((p) => p.id);
    if (!drafts.length) return { submitted: [], already, notify_status: null };

    const submittedAt = nowIso();
    const updated = await db.updateProducts(
      token,
      drafts.map((p) => p.id),
      { status: "submitted", submitted_at: submittedAt, updated_at: submittedAt },
      { status: ["draft"] },
    );
    const updatedIds = new Set(updated.map((p) => p.id));
    // 同時有另一個送出請求搶先的商品：視為已送出（不重複通知）。
    for (const p of drafts) if (!updatedIds.has(p.id)) already.push(p.id);
    const fresh = drafts.filter((p) => updatedIds.has(p.id));
    if (!fresh.length) return { submitted: [], already, notify_status: null };

    const notify = await postWebhook({
      event: "panel.products.submitted",
      project_token: token,
      ...productLists(fresh),
      submitted_at: submittedAt,
    });
    await db.updateProducts(token, fresh.map((p) => p.id), { notify_status: notify });
    return { submitted: fresh.map((p) => p.id), already, notify_status: notify };
  }

  // ---- Bot 端 ----
  function countsOf(statuses: ProductStatus[]) {
    const counts: Record<ProductStatus, number> = { draft: 0, submitted: 0, generating: 0, ready: 0, finalized: 0 };
    for (const s of statuses) if (s in counts) counts[s]++;
    return counts;
  }

  async function botConfig(req: Request): Promise<Response> {
    if (req.method === "GET") {
      const w = await webhookSettings();
      return json(200, { webhook_url: w.url || null, webhook_key_set: !!w.key });
    }
    if (req.method !== "PUT") fail(404, "no_route", "沒有這個功能");
    const body = await readBody(req);
    if (body.clear_webhook === true) {
      await db.deleteConfig(["webhook_url", "webhook_key"]);
    } else {
      const set: Record<string, string> = {};
      if (body.webhook_url !== undefined) {
        const u = body.webhook_url;
        let ok = false;
        if (typeof u === "string" && u.length <= 2000) {
          try {
            const parsed = new URL(u);
            ok = parsed.protocol === "https:" || parsed.protocol === "http:";
          } catch { /* ok=false */ }
        }
        if (!ok) fail(400, "invalid_url", "webhook_url 必須是 http(s) 網址");
        set.webhook_url = u as string;
      }
      if (body.webhook_key !== undefined) {
        const k = body.webhook_key;
        if (typeof k !== "string" || !k || k.length > 1000 || /[\r\n]/.test(k)) {
          fail(400, "invalid_key", "webhook_key 格式不正確");
        }
        set.webhook_key = k;
      }
      if (!Object.keys(set).length) fail(400, "nothing_to_set", "沒有要設定的項目");
      await db.setConfig(set);
    }
    const w = await webhookSettings();
    return json(200, { webhook_url: w.url || null, webhook_key_set: !!w.key });
  }

  async function botQueue(req: Request): Promise<Response> {
    if (req.method !== "GET") fail(404, "no_route", "沒有這個功能");
    const q = new URL(req.url).searchParams;
    const raw = (q.get("status") ?? "").trim() || "submitted";
    const statuses = [...new Set(raw.split(",").map((s) => s.trim()).filter((s) => s))];
    if (!statuses.length || statuses.some((s) => !PRODUCT_STATUSES.includes(s as ProductStatus))) {
      fail(400, "invalid_status", "status 不正確（可用 draft,submitted,generating,ready,finalized，以逗號分隔）");
    }
    const project = q.get("project")?.trim() || undefined;
    if (project !== undefined) await loadProject(project, { allowExpired: true });
    const rows = await db.listQueue(statuses as ProductStatus[], project);
    const key = (r: QueueRow) => r.submitted_at ? new Date(r.submitted_at).getTime() : Number.POSITIVE_INFINITY;
    rows.sort((a, b) => key(a) - key(b) || a.project_token.localeCompare(b.project_token) || a.position - b.position);
    return json(
      200,
      rows.map((r) => ({
        project_token: r.project_token,
        project_title: r.project_title,
        project_status: r.project_status,
        product_id: r.id,
        sku: r.sku,
        name: productName(r),
        status: r.status,
        submitted_at: r.submitted_at ?? null,
        claimed_at: r.claimed_at ?? null,
        updated_at: r.updated_at,
      })),
    );
  }

  async function botProjects(req: Request, rest: string[]): Promise<Response> {
    const m = req.method;
    if (rest.length === 0) {
      if (m === "POST") {
        const body = await readBody(req);
        const title = body.title ?? "";
        if (typeof title !== "string" || title.length > LIMITS.titleMax) {
          fail(400, "invalid_title", `title 必須是文字，最多 ${LIMITS.titleMax} 字`);
        }
        const categories = validateCategories(body.categories);
        const token = base64url(random(32));
        await db.insertProject({ token, title: title.trim(), options: { categories } });
        return json(201, { token });
      }
      if (m === "GET") {
        const st = new URL(req.url).searchParams.get("status") || undefined;
        if (st && st !== "open" && st !== "closed") fail(400, "invalid_status", "status 只能是 open 或 closed");
        const projects = await db.listProjects(st as ProjectStatus | undefined);
        const statuses = projects.length ? await db.listProductStatuses(projects.map((p) => p.token)) : [];
        return json(
          200,
          projects.map((p) => ({
            token: p.token,
            title: p.title,
            status: p.status,
            created_at: p.created_at,
            closed_at: p.closed_at ?? null,
            counts: countsOf(statuses.filter((s) => s.project_token === p.token).map((s) => s.status)),
          })),
        );
      }
      fail(404, "no_route", "沒有這個功能");
    }

    const [token, sub, pid, action] = rest;
    if (rest.length === 1) {
      const project = await loadProject(token, { allowExpired: true });
      if (m === "GET") {
        const products = await db.listProducts(token);
        return json(200, {
          token: project.token,
          title: project.title,
          status: project.status,
          closed_at: project.closed_at ?? null,
          close_notify_status: project.close_notify_status ?? null,
          options: project.options ?? {},
          created_at: project.created_at,
          products: products.map((p) => ({
            id: p.id,
            sku: p.sku,
            name: productName(p),
            status: p.status,
            position: p.position,
            photo_count: (p.photos ?? []).length,
            submitted_at: p.submitted_at ?? null,
            claimed_at: p.claimed_at ?? null,
            synced_at: p.synced_at ?? null,
            notify_status: p.notify_status ?? null,
            has_edited_final: !!p.edited_final,
            updated_at: p.updated_at,
          })),
        });
      }
      if (m === "DELETE") {
        const paths = await storage.listFolder(token);
        if (paths.length) await storage.remove(paths);
        await db.deleteProject(token);
        return json(200, { deleted: true, photos_removed: paths.length });
      }
      fail(404, "no_route", "沒有這個功能");
    }

    if (rest.length === 2 && sub === "export" && m === "GET") {
      const project = await loadProject(token, { allowExpired: true });
      const products = await db.listProducts(token);
      return json(200, {
        token: project.token,
        title: project.title,
        status: project.status,
        closed_at: project.closed_at ?? null,
        exported_at: nowIso(),
        products: products.map((p) => {
          const result = p.result ?? {};
          const st = { ...emptyState(), ...(p.state ?? {}) };
          return {
            id: p.id,
            sku: p.sku,
            position: p.position,
            status: p.status,
            fields: p.fields ?? {},
            product_summary: typeof result.product_summary === "string" ? result.product_summary : "",
            routes: isObj(result.routes) ? result.routes : { A: [], B: [], C: [] },
            state: {
              draft: st.draft,
              hearts: st.hearts,
              dislikes: st.dislikes,
              banned_words: st.banned_words,
              final: st.final ?? null,
            },
            edited_final: p.edited_final ?? null,
            edited_at: p.edited_at ?? null,
          };
        }),
      });
    }

    if (rest.length === 2 && sub === "close" && m === "POST") {
      const project = await loadProject(token, { allowExpired: true });
      const done = await closeProject(project);
      return json(200, {
        status: done.status,
        closed_at: done.closed_at,
        expires_at: done.expires_at,
        close_notify_status: done.close_notify_status ?? null,
      });
    }

    if (rest.length === 2 && sub === "reopen" && m === "POST") {
      const project = await loadProject(token, { allowExpired: true });
      if (project.status === "open") return json(200, { status: "open" });
      const updated = await db.updateProject(token, {
        status: "open",
        closed_at: null,
        expires_at: null,
        close_notify_status: null,
        updated_at: nowIso(),
      });
      return json(200, { status: updated?.status ?? "open" });
    }

    if (sub !== "products" || !pid || rest.length > 4) fail(404, "no_route", "沒有這個功能");
    const project = await loadProject(token, { allowExpired: true });

    if (rest.length === 3 && m === "GET") {
      const p = await loadProduct(project, pid);
      return json(200, await productView(project, p, LIMITS.botUrlTtl, true));
    }
    if (rest.length !== 4 || m !== "POST") fail(404, "no_route", "沒有這個功能");

    if (action === "claim") {
      const p = await loadProduct(project, pid);
      const body = await readBody(req);
      if (body.force !== undefined && typeof body.force !== "boolean") {
        fail(400, "invalid_force", "force 必須是 true/false");
      }
      const allowed: ProductStatus[] = body.force === true ? ["submitted", "generating"] : ["submitted"];
      const at = nowIso();
      const updated = await db.updateProduct(token, p.id, { status: "generating", claimed_at: at, updated_at: at }, {
        status: allowed,
      });
      if (updated) return json(200, { claimed: true, status: "generating", claimed_at: updated.claimed_at });
      const cur = await db.getProduct(token, p.id);
      if (!cur) fail(404, "product_not_found", "找不到這個商品");
      if (cur.status === "generating") {
        return json(200, { claimed: false, status: cur.status, claimed_at: cur.claimed_at ?? null });
      }
      return json(200, { claimed: false, status: cur.status });
    }

    if (action === "result") {
      const p = await loadProduct(project, pid);
      const body = await readBody(req);
      const ok: ProductStatus[] = ["submitted", "generating", "ready"];
      if (!ok.includes(p.status)) fail(409, "bad_status", "這個狀態不能上傳文案", { status: p.status });
      const result = validateResult(body, nowIso());
      const updated = await db.updateProduct(token, p.id, {
        result,
        status: "ready",
        state: emptyState(),
        state_rev: p.state_rev + 1,
        edited_final: null,
        edited_at: null,
        updated_at: nowIso(),
      }, { status: ok });
      if (!updated) fail(409, "bad_status", "這個狀態不能上傳文案");
      const routes = result.routes as Record<string, unknown[]>;
      return json(200, {
        status: "ready",
        counts: { A: routes.A.length, B: routes.B.length, C: routes.C.length },
        state_rev: updated.state_rev,
      });
    }

    if (action === "synced") {
      const p = await loadProduct(project, pid);
      const at = nowIso();
      await db.updateProduct(token, p.id, { synced_at: at });
      return json(200, { synced_at: at });
    }
    fail(404, "no_route", "沒有這個功能");
  }

  async function botRoute(req: Request, parts: string[]): Promise<Response> {
    const [res, ...rest] = parts;
    // 先比對路由（舊的 /bot/sessions… 等未知路徑一律 404），再驗證金鑰。
    const known = (res === "config" && rest.length === 0) || (res === "queue" && rest.length === 0) ||
      res === "projects";
    if (!known) fail(404, "no_route", "沒有這個功能");
    await requireBot(req);
    if (res === "config") return await botConfig(req);
    if (res === "queue") return await botQueue(req);
    return await botProjects(req, rest);
  }

  return async function handler(req: Request): Promise<Response> {
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
    try {
      const pathname = new URL(req.url).pathname;
      // Supabase 會把路徑帶成 /panel-api/...（或 /functions/v1/panel-api/...）；兩種都接受。
      const idx = pathname.indexOf("/panel-api");
      const rest = idx >= 0 ? pathname.slice(idx + "/panel-api".length) : pathname;
      let parts: string[];
      try {
        parts = rest.split("/").filter((p) => p).map((p) => decodeURIComponent(p));
      } catch {
        fail(404, "no_route", "沒有這個功能");
      }
      if (parts[0] === "p" && parts.length >= 2 && parts.length <= 5) {
        return await userRoute(req, parts[1], parts.slice(2));
      }
      if (parts[0] === "bot" && parts.length >= 2) return await botRoute(req, parts.slice(1));
      fail(404, "no_route", "沒有這個功能");
    } catch (e) {
      if (e instanceof HttpError) {
        return json(e.status, { error: e.code, message: e.message, ...(e.extra ?? {}) });
      }
      log(`panel-api 內部錯誤：${e instanceof Error ? e.message : String(e)}`);
      return json(500, { error: "internal", message: "伺服器發生錯誤，請稍後再試" });
    }
  };
}
