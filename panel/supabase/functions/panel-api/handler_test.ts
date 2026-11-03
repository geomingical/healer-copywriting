// 執行：deno test panel/supabase/functions/panel-api/
import {
  createHandler,
  type Db,
  LIMITS,
  normalizeSku,
  type Photo,
  type PhotoOpResult,
  type ProductRow,
  type ProductStatus,
  type ProductWhere,
  type ProjectRow,
  type ProjectStatus,
  type QueueRow,
  sha256Hex,
  type Storage,
} from "./handler.ts";

// ---- 斷言（不依賴外部套件） ----
function assert(cond: unknown, msg = "assertion failed"): asserts cond {
  if (!cond) throw new Error(msg);
}
function eq(actual: unknown, expected: unknown, msg = "") {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${msg}\n  實際：${a}\n  預期：${e}`);
}

const BOT_KEY = "test-bot-key-0123456789abcdefghijklmnopqrstuvwxyz";
const WEBHOOK_KEY = "whk-super-secret-value-987654321";
const ENV_WEBHOOK_KEY = "env-whk-secret-123";
const BASE = "http://localhost/functions/v1/panel-api";
const T0 = "2026-11-03T10:00:00.000Z";

// ---- 記憶體假物件（行為對應 002_projects.sql 與 index.ts） ----
class FakeDb implements Db {
  config = new Map<string, string>();
  projects = new Map<string, ProjectRow>();
  products = new Map<string, ProductRow>();
  private seq = 0;
  constructor(private now: () => Date) {}
  private iso() {
    return this.now().toISOString();
  }
  private match(r: ProductRow, where?: ProductWhere) {
    if (where?.status && !where.status.includes(r.status)) return false;
    if (where?.state_rev !== undefined && where.state_rev !== r.state_rev) return false;
    return true;
  }
  getConfig() {
    return Promise.resolve(Object.fromEntries(this.config));
  }
  setConfig(entries: Record<string, string>) {
    for (const [k, v] of Object.entries(entries)) this.config.set(k, v);
    return Promise.resolve();
  }
  deleteConfig(keys: string[]) {
    for (const k of keys) this.config.delete(k);
    return Promise.resolve();
  }
  getProject(token: string) {
    const r = this.projects.get(token);
    return Promise.resolve(r ? structuredClone(r) : null);
  }
  insertProject(row: { token: string; title: string; options: Record<string, unknown> }) {
    if (this.projects.has(row.token)) throw new Error("duplicate");
    this.projects.set(row.token, {
      ...row,
      status: "open",
      created_at: this.iso(),
      updated_at: this.iso(),
      closed_at: null,
      close_notify_status: null,
      expires_at: null,
    });
    return Promise.resolve();
  }
  updateProject(token: string, patch: Partial<ProjectRow>, where?: { status?: ProjectStatus[] }) {
    const r = this.projects.get(token);
    if (!r || (where?.status && !where.status.includes(r.status))) return Promise.resolve(null);
    Object.assign(r, structuredClone(patch));
    return Promise.resolve(structuredClone(r));
  }
  listProjects(status?: ProjectStatus) {
    const rows = [...this.projects.values()].filter((p) => !status || p.status === status).reverse();
    return Promise.resolve(structuredClone(rows));
  }
  deleteProject(token: string) {
    const had = this.projects.delete(token);
    for (const [id, p] of this.products) if (p.project_token === token) this.products.delete(id);
    return Promise.resolve(had);
  }
  listProducts(projectToken: string) {
    const rows = [...this.products.values()].filter((p) => p.project_token === projectToken)
      .sort((a, b) => a.position - b.position);
    return Promise.resolve(structuredClone(rows));
  }
  listProductStatuses(tokens: string[]) {
    return Promise.resolve(
      [...this.products.values()].filter((p) => tokens.includes(p.project_token))
        .map((p) => ({ project_token: p.project_token, status: p.status })),
    );
  }
  getProduct(projectToken: string, id: string) {
    const r = this.products.get(id);
    return Promise.resolve(r && r.project_token === projectToken ? structuredClone(r) : null);
  }
  insertProduct(row: { project_token: string; sku: string; position: number }) {
    if (!this.projects.has(row.project_token)) throw new Error("fk");
    for (const p of this.products.values()) {
      if (p.project_token === row.project_token && p.sku === row.sku) return Promise.resolve("duplicate_sku" as const);
    }
    this.seq++;
    const id = `00000000-0000-4000-8000-${String(this.seq).padStart(12, "0")}`;
    const r: ProductRow = {
      id,
      ...row,
      status: "draft",
      fields: {},
      photos: [],
      result: null,
      state: {},
      state_rev: 0,
      edited_final: null,
      edited_at: null,
      notify_status: null,
      submitted_at: null,
      claimed_at: null,
      synced_at: null,
      created_at: this.iso(),
      updated_at: this.iso(),
    };
    this.products.set(id, r);
    return Promise.resolve(structuredClone(r));
  }
  updateProduct(projectToken: string, id: string, patch: Partial<ProductRow>, where?: ProductWhere) {
    const r = this.products.get(id);
    if (!r || r.project_token !== projectToken || !this.match(r, where)) return Promise.resolve(null);
    Object.assign(r, structuredClone(patch));
    return Promise.resolve(structuredClone(r));
  }
  renameProduct(projectToken: string, id: string, sku: string, updatedAt: string) {
    const r = this.products.get(id);
    if (!r || r.project_token !== projectToken || r.status !== "draft") return Promise.resolve(null);
    for (const p of this.products.values()) {
      if (p.id !== id && p.project_token === projectToken && p.sku === sku) {
        return Promise.resolve("duplicate_sku" as const);
      }
    }
    Object.assign(r, { sku, updated_at: updatedAt });
    return Promise.resolve(structuredClone(r));
  }
  updateProducts(projectToken: string, ids: string[], patch: Partial<ProductRow>, where?: ProductWhere) {
    const out: ProductRow[] = [];
    for (const id of ids) {
      const r = this.products.get(id);
      if (!r || r.project_token !== projectToken || !this.match(r, where)) continue;
      Object.assign(r, structuredClone(patch));
      out.push(structuredClone(r));
    }
    return Promise.resolve(out);
  }
  deleteProduct(projectToken: string, id: string, where?: ProductWhere) {
    const r = this.products.get(id);
    if (!r || r.project_token !== projectToken || !this.match(r, where)) return Promise.resolve(false);
    return Promise.resolve(this.products.delete(id));
  }
  listQueue(statuses: ProductStatus[], projectToken?: string): Promise<QueueRow[]> {
    // 故意不排序（交給 handler 排）
    const rows = [...this.products.values()]
      .filter((p) => statuses.includes(p.status) && (!projectToken || p.project_token === projectToken))
      .map((p) => {
        const pj = this.projects.get(p.project_token)!;
        return { ...structuredClone(p), project_title: pj.title, project_status: pj.status };
      });
    return Promise.resolve(rows.reverse());
  }
  private photoGuard(id: string): PhotoOpResult | ProductRow {
    const r = this.products.get(id);
    if (!r) return { error: "not_found" };
    if (this.projects.get(r.project_token)?.status !== "open") return { error: "project_closed" };
    if (r.status !== "draft") return { error: "not_draft" };
    return r;
  }
  addPhoto(id: string, photo: { path: string; name: string; size: number }, max: number) {
    const r = this.photoGuard(id);
    if ("error" in r) return Promise.resolve(r);
    if (r.photos.some((p) => p.path === photo.path)) return Promise.resolve({ photos: structuredClone(r.photos) });
    if (r.photos.length >= max) return Promise.resolve({ error: "too_many" as const });
    const order = Math.max(0, ...r.photos.map((p) => p.order)) + 1;
    r.photos.push({ ...photo, order });
    return Promise.resolve({ photos: structuredClone(r.photos) });
  }
  removePhoto(id: string, path: string) {
    const r = this.photoGuard(id);
    if ("error" in r) return Promise.resolve(r);
    r.photos = r.photos.filter((p) => p.path !== path);
    return Promise.resolve({ photos: structuredClone(r.photos) });
  }
}

class FakeStorage implements Storage {
  objects = new Map<string, { size: number; mimetype?: string }>();
  createSignedUploadUrl(path: string) {
    return Promise.resolve({ signed_url: `https://storage.test/upload/${path}?token=up`, token: "up" });
  }
  signedUrls(paths: string[], expiresIn: number) {
    return Promise.resolve(
      paths.map((p) => (this.objects.has(p) ? `https://storage.test/sign/${p}?ttl=${expiresIn}` : null)),
    );
  }
  stat(path: string) {
    return Promise.resolve(this.objects.get(path) ?? null);
  }
  listFolder(prefix: string) {
    return Promise.resolve([...this.objects.keys()].filter((k) => k.startsWith(`${prefix}/`)));
  }
  remove(paths: string[]) {
    for (const p of paths) this.objects.delete(p);
    return Promise.resolve();
  }
}

interface FetchCall {
  url: string;
  init: RequestInit;
}

const goodFields = {
  name: "HEALER Made 高腰寬褲",
  category: "長褲",
  colors: ["深藍", "淺藍"],
  material: "✦材質成分：100%棉",
  sizes: "S/M/L",
  price: "1280",
  background: "補貨",
  feelings: "160 穿 M",
};

const sampleRoutes = {
  A: [{ text: "一穿就愛上" }, { text: "[小編超推]", check: true }],
  B: [{ text: "高腰顯瘦", check: false }],
  C: [{ text: "秋天通勤" }, { text: "週末出遊" }, { text: "搭白T" }],
};

async function setup(opts: { webhook?: boolean; envWebhook?: boolean; fetchImpl?: typeof fetch } = {}) {
  let clock = new Date(T0);
  const now = () => clock;
  const db = new FakeDb(now);
  const storage = new FakeStorage();
  await db.setConfig({ bot_key_sha256: await sha256Hex(BOT_KEY) });
  if (opts.webhook) await db.setConfig({ webhook_url: "https://bot.test/hook", webhook_key: WEBHOOK_KEY });
  const calls: FetchCall[] = [];
  const fetchImpl = opts.fetchImpl ?? (((url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} });
    return Promise.resolve(new Response("ok", { status: 200 }));
  }) as typeof fetch);
  let seed = 0;
  const handler = createHandler({
    db,
    storage,
    config: opts.envWebhook ? { webhookUrl: "https://env.test/hook", webhookKey: ENV_WEBHOOK_KEY } : {},
    fetch: fetchImpl,
    now,
    random: (n) => {
      seed++;
      return Uint8Array.from({ length: n }, (_, i) => (seed * 31 + i * 7) & 0xff);
    },
    log: () => {},
  });

  async function api(method: string, path: string, body?: unknown, headers: Record<string, string> = {}) {
    const res = await handler(
      new Request(BASE + path, {
        method,
        headers: { "content-type": "application/json", ...headers },
        body: body === undefined ? undefined : typeof body === "string" ? body : JSON.stringify(body),
      }),
    );
    const text = await res.text();
    // 每個回應都檢查：不可出現 bot key、其雜湊或 webhook key，且都有 CORS。
    const hash = await sha256Hex(BOT_KEY);
    for (const secret of [BOT_KEY, WEBHOOK_KEY, ENV_WEBHOOK_KEY, hash, hash.toUpperCase()]) {
      assert(!text.includes(secret), `回應洩漏了秘密：${method} ${path}`);
    }
    for (const v of res.headers.values()) {
      assert(!v.includes(WEBHOOK_KEY) && !v.includes(BOT_KEY) && !v.includes(hash), "header 洩漏秘密");
    }
    assert(res.headers.get("access-control-allow-origin") === "*", `缺 CORS：${method} ${path}`);
    // deno-lint-ignore no-explicit-any
    const data: any = text ? JSON.parse(text) : null;
    if (res.status >= 400) {
      assert(typeof data.error === "string" && typeof data.message === "string", "錯誤格式不正確");
    }
    return { status: res.status, data };
  }
  const bot = (method: string, path: string, body?: unknown) => api(method, path, body, { "x-bot-key": BOT_KEY });

  async function createProject(categories = ["長褲", "洋裝"], title = "十一月新品") {
    const r = await bot("POST", "/bot/projects", { title, categories });
    eq(r.status, 201, "建立專案");
    return r.data.token as string;
  }

  async function addProduct(token: string, sku: string) {
    const r = await api("POST", `/p/${token}/products`, { sku });
    eq(r.status, 201, `新增商品 ${sku}`);
    return r.data.id as string;
  }

  async function addPhoto(token: string, pid: string, opts: { size?: number; type?: string } = {}) {
    const type = opts.type ?? "image/jpeg";
    const u = await api("POST", `/p/${token}/products/${pid}/upload-url`, { name: "相片.jpg", type, size: 1000 });
    eq(u.status, 200, "upload-url");
    storage.objects.set(u.data.path, { size: opts.size ?? 1000, mimetype: type });
    const r = await api("POST", `/p/${token}/products/${pid}/photos`, { path: u.data.path, name: "相片.jpg" });
    return { ...r, path: u.data.path as string };
  }

  /** 建立一個填好欄位與照片的草稿商品。 */
  async function completeDraft(token: string, sku: string, fields: Record<string, unknown> = goodFields) {
    const pid = await addProduct(token, sku);
    eq((await addPhoto(token, pid)).status, 200);
    eq((await api("PUT", `/p/${token}/products/${pid}/fields`, { fields })).status, 200);
    return pid;
  }

  async function submitted(token: string, sku: string, fields: Record<string, unknown> = goodFields) {
    const pid = await completeDraft(token, sku, fields);
    const r = await api("POST", `/p/${token}/submit`, { product_ids: [pid] });
    eq(r.status, 200, "submit");
    return pid;
  }

  async function ready(token: string, sku: string) {
    const pid = await submitted(token, sku);
    eq((await bot("POST", `/bot/projects/${token}/products/${pid}/claim`)).data.claimed, true);
    const r = await bot("POST", `/bot/projects/${token}/products/${pid}/result`, {
      routes: sampleRoutes,
      product_summary: "寬褲",
    });
    eq(r.status, 200, "result");
    return pid;
  }

  async function finalized(token: string, sku: string, text = "定稿全文\n✦材質成分：100%棉") {
    const pid = await ready(token, sku);
    const rev = (await api("GET", `/p/${token}/products/${pid}`)).data.state_rev;
    const r = await api("PUT", `/p/${token}/products/${pid}/state`, {
      rev,
      state: { hearts: ["A1"], draft: [{ id: "A1", text: "一穿就愛上" }], final: { text } },
    });
    eq(r.data.status, "finalized");
    return pid;
  }

  return {
    db,
    storage,
    calls,
    api,
    bot,
    createProject,
    addProduct,
    addPhoto,
    completeDraft,
    submitted,
    ready,
    finalized,
    setClock: (d: Date) => (clock = d),
  };
}

const webhookBody = (c: FetchCall) => JSON.parse(String(c.init.body));

// ---- 基本 ----
Deno.test("OPTIONS 回 CORS 預檢（含 PATCH）", async () => {
  const { db, storage } = await setup();
  const h = createHandler({ db, storage });
  const res = await h(new Request(BASE + "/p/x", { method: "OPTIONS" }));
  eq(res.status, 204);
  eq(res.headers.get("access-control-allow-origin"), "*");
  assert(res.headers.get("access-control-allow-headers")!.includes("x-bot-key"));
  assert(res.headers.get("access-control-allow-methods")!.includes("PATCH"));
});

Deno.test("路徑前綴兩種都接受；舊的 /s/… 與 /bot/sessions… 一律 404", async () => {
  const t = await setup();
  const token = await t.createProject();
  const { db, storage } = t;
  const h = createHandler({ db, storage, log: () => {} });
  eq((await h(new Request("http://x/panel-api/bot/projects", { headers: { "x-bot-key": BOT_KEY } }))).status, 200);
  eq((await h(new Request(`http://x/panel-api/p/${token}`))).status, 200);
  eq((await h(new Request("http://x/panel-api/nope"))).status, 404);
  eq((await h(new Request("http://x/panel-api/p/%E0%A4%A"))).status, 404);
  eq((await t.api("GET", `/s/${token}`)).status, 404);
  eq((await t.api("POST", `/s/${token}/submit`, {})).status, 404);
  eq((await t.bot("GET", "/bot/sessions")).status, 404);
  eq((await t.bot("POST", "/bot/sessions", { categories: [] })).status, 404);
  eq((await t.bot("GET", `/bot/sessions/${token}`)).status, 404);
  eq((await t.api("GET", "/bot/sessions")).status, 404, "未授權也回 404（路由不存在）");
  eq((await t.api("GET", `/p/${token}/bogus`)).status, 404);
  eq((await t.api("POST", `/p/${token}/products/x/y/z`)).status, 404);
});

Deno.test("Bot 授權：缺少、錯誤、未設定金鑰都 401；正確才放行；所有 bot 路由都要金鑰", async () => {
  const t = await setup();
  const token = await t.createProject();
  eq((await t.api("GET", "/bot/projects")).status, 401);
  eq((await t.api("GET", "/bot/projects", undefined, { "x-bot-key": "wrong" })).status, 401);
  eq((await t.api("GET", "/bot/projects", undefined, { "x-bot-key": BOT_KEY + "x" })).status, 401);
  eq((await t.api("POST", "/bot/projects", { categories: [] }, { "x-bot-key": "" })).status, 401);
  for (
    const [m, p] of [
      ["GET", "/bot/queue"],
      ["GET", "/bot/config"],
      ["PUT", "/bot/config"],
      ["GET", `/bot/projects/${token}`],
      ["GET", `/bot/projects/${token}/export`],
      ["POST", `/bot/projects/${token}/close`],
      ["POST", `/bot/projects/${token}/reopen`],
      ["DELETE", `/bot/projects/${token}`],
    ]
  ) {
    eq((await t.api(m, p, m === "GET" ? undefined : {})).status, 401, `${m} ${p}`);
  }
  eq(t.db.projects.get(token)!.status, "open", "未授權的 close 沒有作用");
  eq((await t.bot("GET", "/bot/projects")).status, 200);
  t.db.config.delete("bot_key_sha256");
  eq((await t.bot("GET", "/bot/projects")).status, 401);
  t.db.config.set("bot_key_sha256", (await sha256Hex(BOT_KEY)).toUpperCase());
  eq((await t.bot("GET", "/bot/projects")).status, 200, "大寫 hex 也接受");
});

Deno.test("token：格式錯誤、不存在、過期都 404；product id 格式錯或不屬於專案 404", async () => {
  const t = await setup();
  eq((await t.api("GET", "/p/short")).status, 404);
  eq((await t.api("GET", "/p/" + "!".repeat(43))).status, 404);
  eq((await t.api("GET", "/p/" + "a".repeat(43))).status, 404);
  eq((await t.api("POST", "/p/" + "a".repeat(43) + "/submit", { product_ids: [] })).status, 404);
  eq((await t.bot("GET", "/bot/projects/" + "a".repeat(43))).status, 404);
  eq((await t.bot("GET", "/bot/projects/bad")).status, 404);
  const token = await t.createProject();
  const other = await t.createProject();
  const pid = await t.addProduct(token, "A001");
  eq((await t.api("GET", `/p/${token}/products/not-a-uuid`)).status, 404);
  eq((await t.api("GET", `/p/${token}/products/A001`)).data.error, "product_not_found", "不接受 sku");
  eq((await t.api("GET", `/p/${other}/products/${pid}`)).status, 404, "別的專案的商品");
  eq((await t.api("GET", `/p/${token}/products/${pid.toUpperCase()}`)).status, 200, "uuid 大小寫不拘");

  // 開啟中沒有過期
  t.setClock(new Date("2030-01-01T00:00:00Z"));
  eq((await t.api("GET", `/p/${token}`)).status, 200, "開啟中的專案不會過期");
  // 結束後 180 天過期
  await t.api("POST", `/p/${token}/close`, { confirm: true });
  eq(t.db.projects.get(token)!.expires_at, "2030-06-30T00:00:00.000Z");
  t.setClock(new Date("2030-06-29T23:59:59Z"));
  eq((await t.api("GET", `/p/${token}`)).status, 200);
  eq((await t.api("GET", `/p/${token}/products/${pid}`)).status, 200);
  t.setClock(new Date("2030-06-30T00:00:01Z"));
  eq((await t.api("GET", `/p/${token}`)).status, 404, "過期");
  eq((await t.api("GET", `/p/${token}/products/${pid}`)).status, 404, "過期");
  eq((await t.bot("GET", `/bot/projects/${token}`)).status, 200, "Bot 仍可讀取過期專案");
  eq((await t.bot("GET", `/bot/projects/${token}/products/${pid}`)).status, 200);
  eq((await t.bot("GET", `/bot/projects/${token}/export`)).status, 200);
});

// ---- 專案與商品 ----
Deno.test("建立專案：token 43 字元、類別去重、title；GET /p/:token 形狀", async () => {
  const t = await setup();
  const r = await t.bot("POST", "/bot/projects", { title: " 十一月 ", categories: ["長褲", "長褲", " 洋裝 ", ""] });
  eq(r.status, 201);
  eq(Object.keys(r.data), ["token"]);
  assert(/^[A-Za-z0-9_-]{43}$/.test(r.data.token));
  const g = await t.api("GET", `/p/${r.data.token}`);
  eq(g.data, {
    title: "十一月",
    status: "open",
    closed_at: null,
    options: { categories: ["長褲", "洋裝"] },
    server_time: T0,
    products: [],
  });
  eq((await t.bot("POST", "/bot/projects", {})).status, 201, "title、categories 可省略");
  eq((await t.bot("POST", "/bot/projects", { categories: "長褲" })).status, 400);
  eq((await t.bot("POST", "/bot/projects", { categories: [1] })).status, 400);
  eq((await t.bot("POST", "/bot/projects", { title: 1 })).status, 400);
  eq((await t.bot("POST", "/bot/projects", { title: "x".repeat(LIMITS.titleMax + 1) })).status, 400);
  eq((await t.bot("POST", "/bot/projects", "not json")).status, 400);
});

Deno.test("新增商品：sku 驗證、重複 409、position 遞增、上限 30", async () => {
  const t = await setup();
  const token = await t.createProject();
  const add = (sku: unknown) => t.api("POST", `/p/${token}/products`, { sku });
  const a = await add(" A-001_v2.1 ");
  eq(a.status, 201);
  eq(a.data.sku, "A-001_v2.1", "去頭尾空白");
  eq(a.data.status, "draft");
  eq(a.data.position, 1);
  assert(/^[0-9a-f-]{36}$/.test(a.data.id));
  eq(Object.keys(a.data).sort(), ["id", "position", "sku", "status"]);
  for (const ok of ["狎鷗亭01", "ワンピース", "ひらがな", "한글", "々A", "x".repeat(40)]) {
    eq((await add(ok)).status, 201, `應接受 ${ok}`);
  }
  for (const bad of ["", "   ", "A 001", "A/001", "A\\001", "..", ".", "x".repeat(41), "A,1", "😀", 123, null]) {
    const r = await add(bad);
    eq(r.status, 400, `應拒絕 ${JSON.stringify(bad)}`);
    eq(r.data.error, "invalid_sku");
  }
  eq((await t.api("POST", `/p/${token}/products`, {})).status, 400);
  const dup = await add("A-001_v2.1");
  eq(dup.status, 409);
  eq(dup.data.error, "duplicate_sku");
  eq((await add("a-001_v2.1")).status, 201, "大小寫視為不同");
  const g = await t.api("GET", `/p/${token}`);
  eq(g.data.products.map((p: { position: number }) => p.position), [1, 2, 3, 4, 5, 6, 7, 8]);
  eq(g.data.products[0], {
    id: a.data.id,
    sku: "A-001_v2.1",
    name: "",
    status: "draft",
    position: 1,
    photo_count: 0,
    updated_at: T0,
    has_edited_final: false,
  });
  // 上限 30
  for (let i = g.data.products.length; i < LIMITS.maxProducts; i++) eq((await add(`N${i}`)).status, 201);
  const over = await add("N999");
  eq(over.status, 409);
  eq(over.data.error, "too_many_products");
  // 另一個專案可用同一個 sku
  const other = await t.createProject();
  eq((await t.api("POST", `/p/${other}/products`, { sku: "A-001_v2.1" })).status, 201);
  eq(normalizeSku("a/b"), null);
});

Deno.test("PATCH sku：只有 draft 可改、同驗證、重複 409；DELETE 商品連同照片、只限 draft", async () => {
  const t = await setup();
  const token = await t.createProject();
  const a = await t.addProduct(token, "A001");
  await t.addProduct(token, "B001");
  const patch = (pid: string, sku: unknown) => t.api("PATCH", `/p/${token}/products/${pid}`, { sku });
  eq((await patch(a, "A002")).data, { id: a, sku: "A002" });
  eq((await patch(a, "A002")).status, 200, "同名不變");
  eq((await patch(a, "B001")).data.error, "duplicate_sku");
  eq((await patch(a, "B 001")).status, 400);
  eq((await t.api("GET", `/p/${token}/products/${a}`)).data.sku, "A002");

  // 刪除：連同照片檔（含未登記上傳），其他商品不受影響
  const c = await t.addProduct(token, "C001");
  const ph = await t.addPhoto(token, c);
  t.storage.objects.set(`${token}/${c}/orphan.jpg`, { size: 1 });
  const keep = await t.addPhoto(token, a);
  const d = await t.api("DELETE", `/p/${token}/products/${c}`);
  eq(d.data, { deleted: true, photos_removed: 2 });
  assert(!t.storage.objects.has(ph.path));
  assert(t.storage.objects.has(keep.path));
  eq((await t.api("GET", `/p/${token}/products/${c}`)).status, 404);
  eq((await t.api("DELETE", `/p/${token}/products/${c}`)).status, 404);

  // 送出後不能改 sku、不能刪
  const s = await t.submitted(token, "S001");
  const p2 = await patch(s, "S002");
  eq(p2.status, 409);
  eq(p2.data.error, "not_draft");
  eq((await t.api("DELETE", `/p/${token}/products/${s}`)).data.error, "not_draft");
});

Deno.test("上傳網址 → 登記照片：路徑 <token>/<product_id>/、需真的在 storage、order 遞增、限自己資料夾", async () => {
  const t = await setup();
  const token = await t.createProject();
  const pid = await t.addProduct(token, "A001");
  const other = await t.addProduct(token, "B001");
  const base = `/p/${token}/products/${pid}`;
  const u = await t.api("POST", `${base}/upload-url`, { name: "a.png", type: "image/png", size: 500 });
  eq(u.status, 200);
  assert(u.data.path.startsWith(`${token}/${pid}/`) && u.data.path.endsWith(".png"), u.data.path);
  assert(u.data.signed_url.includes(u.data.path));
  eq(u.data.token, "up");
  const notYet = await t.api("POST", `${base}/photos`, { path: u.data.path, name: "a.png" });
  eq(notYet.data.error, "not_uploaded");
  t.storage.objects.set(u.data.path, { size: 500, mimetype: "image/png" });
  const p1 = await t.api("POST", `${base}/photos`, { path: u.data.path, name: "a.png", size: 999999 });
  eq(p1.data.photos, [{ path: u.data.path, name: "a.png", size: 500, order: 1 }], "size 以 storage 為準");
  eq((await t.api("POST", `${base}/photos`, { path: u.data.path })).data.photos.length, 1, "重複登記不重複加入");
  const p2 = await t.addPhoto(token, pid);
  eq(p2.data.photos.map((p: Photo) => p.order), [1, 2]);
  // 別的商品、別的專案、路徑穿越、子資料夾
  t.storage.objects.set(`${token}/${other}/x.jpg`, { size: 10 });
  eq((await t.api("POST", `${base}/photos`, { path: `${token}/${other}/x.jpg` })).status, 400);
  eq((await t.api("POST", `${base}/photos`, { path: `${token}/x.jpg` })).status, 400);
  eq((await t.api("POST", `${base}/photos`, { path: `${token}/${pid}/../${other}/x.jpg` })).status, 400);
  eq((await t.api("POST", `${base}/photos`, { path: `${token}/${pid}/a/b.jpg` })).status, 400);
  eq((await t.api("POST", `${base}/photos`, { path: `${token}/${pid}/` })).status, 400);
  eq((await t.api("POST", `${base}/photos`, {})).status, 400);
  // GET 帶 1 小時網址，依 order；形狀符合規格
  const g = await t.api("GET", base);
  eq(Object.keys(g.data), [
    "id",
    "sku",
    "status",
    "fields",
    "photos",
    "result",
    "state",
    "state_rev",
    "edited_final",
    "edited_at",
    "updated_at",
    "server_time",
    "project_status",
  ]);
  eq(g.data.photos.length, 2);
  assert(g.data.photos[0].url.includes("ttl=3600"));
  eq(g.data.project_status, "open");
  eq((await t.api("GET", `/p/${token}`)).data.products[0].photo_count, 2);
  // 刪照片
  const d = await t.api("DELETE", `${base}/photos`, { path: u.data.path });
  eq(d.data.photos.map((p: Photo) => p.path), [p2.path]);
  assert(!t.storage.objects.has(u.data.path));
  eq((await t.api("DELETE", `${base}/photos`, { path: `${token}/${other}/x.jpg` })).status, 400);
});

Deno.test("照片型別、大小、每商品 20 張上限；送出後不能改照片", async () => {
  const t = await setup();
  const token = await t.createProject();
  const pid = await t.addProduct(token, "A001");
  const up = (body: unknown) => t.api("POST", `/p/${token}/products/${pid}/upload-url`, body);
  eq((await up({ type: "image/gif", size: 10 })).status, 400);
  eq((await up({ size: 10 })).status, 400);
  eq((await up({ type: "image/jpeg", size: LIMITS.maxPhotoBytes + 1 })).status, 413);
  eq((await up({ type: "image/jpeg", size: 0 })).status, 400);
  eq((await up({ type: "image/jpeg", size: "10" })).status, 400);
  eq((await up({ type: "image/webp", size: LIMITS.maxPhotoBytes })).status, 200);
  const big = await up({ type: "image/jpeg", size: 10 });
  t.storage.objects.set(big.data.path, { size: LIMITS.maxPhotoBytes + 1, mimetype: "image/jpeg" });
  eq((await t.api("POST", `/p/${token}/products/${pid}/photos`, { path: big.data.path })).status, 413);
  assert(!t.storage.objects.has(big.data.path), "超過大小的檔案被刪掉");
  const gif = await up({ type: "image/jpeg", size: 10 });
  t.storage.objects.set(gif.data.path, { size: 10, mimetype: "image/gif" });
  eq((await t.api("POST", `/p/${token}/products/${pid}/photos`, { path: gif.data.path })).status, 400);
  for (let i = 0; i < LIMITS.maxPhotos; i++) eq((await t.addPhoto(token, pid)).status, 200);
  const over = await up({ type: "image/jpeg", size: 10 });
  eq(over.data.error, "too_many");
  t.storage.objects.set(`${token}/${pid}/extra.jpg`, { size: 10, mimetype: "image/jpeg" });
  eq((await t.api("POST", `/p/${token}/products/${pid}/photos`, { path: `${token}/${pid}/extra.jpg` })).status, 409);
  // 其他商品不受這個商品的上限影響
  const b = await t.addProduct(token, "B001");
  eq((await t.addPhoto(token, b)).status, 200);
  // 送出後
  const s = await t.submitted(token, "S001");
  const sp = (await t.api("GET", `/p/${token}/products/${s}`)).data.photos[0].path;
  eq((await t.api("DELETE", `/p/${token}/products/${s}/photos`, { path: sp })).data.error, "not_draft");
  eq((await t.api("POST", `/p/${token}/products/${s}/upload-url`, { type: "image/jpeg", size: 1 })).status, 409);
  assert(t.storage.objects.has(sp));
});

Deno.test("PUT fields：長度上限、類別必須在專案 categories、型別驗證；送出後 409", async () => {
  const t = await setup();
  const token = await t.createProject();
  const pid = await t.addProduct(token, "A001");
  const put = (fields: unknown) => t.api("PUT", `/p/${token}/products/${pid}/fields`, { fields });
  const ok = await put({ name: "褲", category: "", colors: [" 藍 ", ""], unknown: "x" });
  eq(ok.data.fields, {
    name: "褲",
    category: "",
    colors: ["藍"],
    material: "",
    sizes: "",
    price: "",
    background: "",
    feelings: "",
  });
  eq((await put({ name: "a".repeat(120) })).status, 200);
  eq((await put({ name: "a".repeat(121) })).status, 400);
  eq((await put({ feelings: "a".repeat(1001) })).status, 400);
  eq((await put({ category: "外套" })).data.error, "invalid_category");
  eq((await put({ category: "洋裝" })).status, 200);
  eq((await put({ colors: "藍" })).status, 400);
  eq((await put({ price: 1280 })).status, 400);
  eq((await put("x")).status, 400);
  eq((await t.api("PUT", `/p/${token}/products/${pid}/fields`, {})).status, 400);
  eq((await t.api("GET", `/p/${token}/products/${pid}`)).data.fields.category, "洋裝");
  eq((await t.api("GET", `/p/${token}`)).data.products[0].name, "", "最後一次 PUT 沒帶 name");
  eq((await t.api("PUT", `/p/${token}/products/${pid}/fields`, "x".repeat(LIMITS.maxBodyBytes + 1))).status, 413);
  const s = await t.submitted(token, "S001");
  eq((await t.api("PUT", `/p/${token}/products/${s}/fields`, { fields: goodFields })).data.error, "not_draft");
});

// ---- 一次送出多個商品 ----
Deno.test("submit：兩個商品一起送出，webhook 只發一次且 header/body 正確", async () => {
  const t = await setup({ webhook: true });
  const token = await t.createProject();
  const longName = "很長的名字".repeat(20);
  const a = await t.completeDraft(token, "A001");
  const b = await t.completeDraft(token, "B001", { ...goodFields, name: longName });
  const r = await t.api("POST", `/p/${token}/submit`, { product_ids: [a, b, a] });
  eq(r.status, 200);
  eq(r.data, { submitted: [a, b], already: [], notify_status: "ok" });
  eq(t.calls.length, 1, "webhook 只發一次");
  const call = t.calls[0];
  eq(call.url, "https://bot.test/hook");
  eq(call.init.method, "POST");
  const h = new Headers(call.init.headers);
  eq(h.get("authorization"), `Bearer ${WEBHOOK_KEY}`);
  eq(h.get("content-type"), "application/json");
  assert(call.init.signal instanceof AbortSignal, "要有逾時 signal");
  eq(webhookBody(call), {
    event: "panel.products.submitted",
    project_token: token,
    product_ids: [a, b],
    product_names: ["HEALER Made 高腰寬褲", longName.slice(0, 60)],
    skus: ["A001", "B001"],
    submitted_at: T0,
  });
  for (const pid of [a, b]) {
    const g = await t.bot("GET", `/bot/projects/${token}/products/${pid}`);
    eq(g.data.status, "submitted");
    eq(g.data.submitted_at, T0);
    eq(g.data.notify_status, "ok");
  }
  // 冪等：再送不重複通知；加上新的草稿只通知新的
  const again = await t.api("POST", `/p/${token}/submit`, { product_ids: [a, b] });
  eq(again.data, { submitted: [], already: [a, b], notify_status: null });
  eq(t.calls.length, 1);
  t.setClock(new Date("2026-11-03T11:00:00.000Z"));
  const c = await t.completeDraft(token, "C001");
  await t.bot("POST", `/bot/projects/${token}/products/${a}/claim`);
  const mix = await t.api("POST", `/p/${token}/submit`, { product_ids: [a, c] });
  eq(mix.data, { submitted: [c], already: [a], notify_status: "ok" });
  eq(t.calls.length, 2);
  eq(webhookBody(t.calls[1]).product_ids, [c]);
  eq(webhookBody(t.calls[1]).submitted_at, "2026-11-03T11:00:00.000Z");
  eq((await t.bot("GET", `/bot/projects/${token}/products/${a}`)).data.status, "generating", "generating 不受影響");
});

Deno.test("submit：其中一個缺欄位時全部不送出、列出缺哪項；已存欄位保留", async () => {
  const t = await setup({ webhook: true });
  const token = await t.createProject();
  const a = await t.completeDraft(token, "A001");
  const b = await t.addProduct(token, "B001");
  await t.api("PUT", `/p/${token}/products/${b}/fields`, { fields: { name: "洋裝", colors: [] } });
  const c = await t.addProduct(token, "C001");
  await t.addPhoto(token, c);
  const r = await t.api("POST", `/p/${token}/submit`, {
    product_ids: [a, b, c],
    fields: { [c]: { ...goodFields, material: "" } },
  });
  eq(r.status, 400);
  eq(r.data.error, "incomplete");
  eq(r.data.items, [
    { id: b, sku: "B001", missing: ["category", "colors", "material", "photos"] },
    { id: c, sku: "C001", missing: ["material"] },
  ]);
  assert(r.data.message.includes("B001") && r.data.message.includes("照片") && r.data.message.includes("材質"));
  eq(t.calls.length, 0, "沒有通知");
  for (const pid of [a, b, c]) eq((await t.api("GET", `/p/${token}/products/${pid}`)).data.status, "draft");
  const cf = (await t.api("GET", `/p/${token}/products/${c}`)).data.fields;
  eq(cf.name, goodFields.name, "送出時帶的欄位已保存");
  eq((await t.api("GET", `/p/${token}/products/${b}`)).data.fields.name, "洋裝");

  // 單一商品可直接給 fields 物件（同 v1）
  const one = await t.api("POST", `/p/${token}/submit`, { product_ids: [c], fields: goodFields });
  eq(one.data.submitted, [c]);
  // 多個商品時 fields 不是以 id 為鍵 → 400
  eq((await t.api("POST", `/p/${token}/submit`, { product_ids: [a, b], fields: goodFields })).status, 400);
  // fields 格式錯誤 → 400，且沒有任何變更
  const badFields = await t.api("POST", `/p/${token}/submit`, { product_ids: [a], fields: { category: "外套" } });
  eq(badFields.data.error, "invalid_category");
  eq((await t.api("GET", `/p/${token}/products/${a}`)).data.status, "draft");
});

Deno.test("submit：product_ids 驗證、不屬於專案 404、ready/finalized 409 bad_status", async () => {
  const t = await setup();
  const token = await t.createProject();
  const other = await t.createProject();
  const op = await t.addProduct(other, "X001");
  const a = await t.completeDraft(token, "A001");
  const sub = (body: unknown) => t.api("POST", `/p/${token}/submit`, body);
  eq((await sub({})).data.error, "invalid_products");
  eq((await sub({ product_ids: [] })).status, 400);
  eq((await sub({ product_ids: "x" })).status, 400);
  eq((await sub({ product_ids: [1] })).status, 400);
  eq((await sub({ product_ids: Array.from({ length: 31 }, (_, i) => `id${i}`) })).status, 400);
  const nf = await sub({ product_ids: [a, op, "zzz"] });
  eq(nf.status, 404);
  eq(nf.data.ids, [op, "zzz"]);
  const r = await t.ready(token, "R001");
  const bad = await sub({ product_ids: [a, r] });
  eq(bad.status, 409);
  eq(bad.data.error, "bad_status");
  eq(bad.data.items, [{ id: r, sku: "R001", status: "ready" }]);
  eq((await t.api("GET", `/p/${token}/products/${a}`)).data.status, "draft", "全部不送出");
});

Deno.test("webhook 未設定：照常送出、notify_status=unset；環境變數後備；hcp_config 優先；沒 key 不帶 authorization", async () => {
  const t = await setup();
  const token = await t.createProject();
  const a = await t.completeDraft(token, "A001");
  eq((await t.api("POST", `/p/${token}/submit`, { product_ids: [a] })).data.notify_status, "unset");
  eq(t.calls.length, 0);
  eq((await t.bot("GET", `/bot/projects/${token}/products/${a}`)).data.notify_status, "unset");
  eq((await t.bot("GET", "/bot/config")).data, { webhook_url: null, webhook_key_set: false });

  const e = await setup({ envWebhook: true });
  const et = await e.createProject();
  await e.submitted(et, "A001");
  eq(e.calls[0].url, "https://env.test/hook");
  eq(new Headers(e.calls[0].init.headers).get("authorization"), `Bearer ${ENV_WEBHOOK_KEY}`);
  await e.db.setConfig({ webhook_url: "https://db.test/hook" });
  await e.submitted(et, "B001");
  eq(e.calls[1].url, "https://db.test/hook");

  const n = await setup();
  await n.db.setConfig({ webhook_url: "https://nokey.test/hook" });
  const nt = await n.createProject();
  await n.submitted(nt, "A001");
  eq(new Headers(n.calls[0].init.headers).get("authorization"), null);
});

Deno.test("webhook 失敗（HTTP 500、網路錯誤、逾時）不影響送出，只記錄結果", async () => {
  const cases: [typeof fetch, string][] = [
    [(() => Promise.resolve(new Response("no", { status: 500 }))) as typeof fetch, "failed:500"],
    [(() => Promise.reject(new TypeError("network"))) as typeof fetch, "failed:network"],
    [
      ((_u: unknown, init?: RequestInit) =>
        new Promise((_, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        })) as typeof fetch,
      "failed:timeout",
    ],
  ];
  const saved = LIMITS.webhookTimeoutMs;
  LIMITS.webhookTimeoutMs = 30;
  try {
    for (const [fetchImpl, expected] of cases) {
      const t = await setup({ webhook: true, fetchImpl });
      const token = await t.createProject();
      const a = await t.completeDraft(token, "A001");
      const b = await t.completeDraft(token, "B001");
      const r = await t.api("POST", `/p/${token}/submit`, { product_ids: [a, b] });
      eq(r.status, 200);
      eq(r.data.notify_status, expected);
      for (const pid of [a, b]) {
        const g = await t.bot("GET", `/bot/projects/${token}/products/${pid}`);
        eq(g.data.status, "submitted");
        eq(g.data.notify_status, expected);
      }
      // close 的 webhook 失敗也不影響結束
      const c = await t.api("POST", `/p/${token}/close`, { confirm: true });
      eq(c.data.status, "closed");
      eq((await t.bot("GET", `/bot/projects/${token}`)).data.close_notify_status, expected);
    }
  } finally {
    LIMITS.webhookTimeoutMs = saved;
  }
});

// ---- Bot 流程 ----
Deno.test("claim：原子性（兩次只有一次 claimed:true，含同時）、force 重領、其他狀態", async () => {
  const t = await setup();
  const token = await t.createProject();
  const d = await t.addProduct(token, "D001");
  const claim = (pid: string, body?: unknown) => t.bot("POST", `/bot/projects/${token}/products/${pid}/claim`, body);
  eq((await claim(d)).data, { claimed: false, status: "draft" });
  const a = await t.submitted(token, "A001");
  const [r1, r2] = await Promise.all([claim(a), claim(a)]);
  eq([r1.data.claimed, r2.data.claimed].filter((x) => x).length, 1, "同時兩次只有一次成功");
  const won = r1.data.claimed ? r1 : r2;
  const lost = r1.data.claimed ? r2 : r1;
  eq(won.data, { claimed: true, status: "generating", claimed_at: T0 });
  eq(lost.data, { claimed: false, status: "generating", claimed_at: T0 });
  eq((await claim(a)).data, { claimed: false, status: "generating", claimed_at: T0 }, "第三次也失敗");
  // force：bot 當機後重領，claimed_at 更新
  t.setClock(new Date("2026-11-03T12:00:00.000Z"));
  eq((await claim(a, { force: true })).data, {
    claimed: true,
    status: "generating",
    claimed_at: "2026-11-03T12:00:00.000Z",
  });
  eq((await claim(a, { force: "yes" })).status, 400);
  const g = await t.bot("GET", `/bot/projects/${token}/products/${a}`);
  eq(g.data.claimed_at, "2026-11-03T12:00:00.000Z");
  // ready 時 force 也不行
  await t.bot("POST", `/bot/projects/${token}/products/${a}/result`, { routes: sampleRoutes });
  eq((await claim(a, { force: true })).data, { claimed: false, status: "ready" });
  eq((await t.api("POST", `/p/${token}/products/${a}/claim`)).status, 404, "使用者端沒有 claim");
  eq((await claim("00000000-0000-4000-8000-999999999999")).status, 404);
});

Deno.test("result：自動編號、check、狀態檢查、重送清空 state 與 edited_final", async () => {
  const t = await setup();
  const token = await t.createProject();
  const d = await t.addProduct(token, "D001");
  const result = (pid: string, body: unknown) => t.bot("POST", `/bot/projects/${token}/products/${pid}/result`, body);
  eq((await result(d, { routes: { A: [{ text: "x" }] } })).status, 409, "draft 不能上傳");
  const a = await t.submitted(token, "A001");
  eq((await result(a, { routes: { A: [{ text: "" }] } })).status, 400);
  eq((await result(a, { routes: { A: [{ text: "x", check: "y" }] } })).status, 400);
  eq((await result(a, { routes: { D: [{ text: "x" }] } })).status, 400);
  eq((await result(a, { routes: {} })).status, 400);
  eq((await result(a, {})).status, 400);
  const r = await result(a, {
    routes: { A: [{ text: "一穿就愛上", id: "Z9" }, { text: "[小編超推]", check: true }], C: [{ text: "秋天" }] },
    product_summary: "高腰寬褲",
  });
  eq(r.data, { status: "ready", counts: { A: 2, B: 0, C: 1 }, state_rev: 1 });
  const g = await t.api("GET", `/p/${token}/products/${a}`);
  eq(g.data.status, "ready");
  eq(g.data.result, {
    routes: {
      A: [{ id: "A1", text: "一穿就愛上", check: false }, { id: "A2", text: "[小編超推]", check: true }],
      B: [],
      C: [{ id: "C1", text: "秋天", check: false }],
    },
    product_summary: "高腰寬褲",
    generated_at: T0,
  });
  eq(g.data.state, { draft: [], hearts: [], dislikes: [], banned_words: [], final: null });
  // 定稿 → 不能重送；回到 ready 後重送清空 state 與 edited_final
  await t.api("PUT", `/p/${token}/products/${a}/state`, { rev: 1, state: { final: { text: "定稿" } } });
  await t.api("PUT", `/p/${token}/products/${a}/edited-final`, { text: "我的版本" });
  eq((await result(a, { routes: sampleRoutes })).data.error, "bad_status");
  await t.api("PUT", `/p/${token}/products/${a}/state`, { rev: 2, state: { hearts: ["A1"], final: null } });
  eq((await result(a, { routes: { B: [{ text: "新句" }] } })).data.state_rev, 4);
  const g2 = await t.api("GET", `/p/${token}/products/${a}`);
  eq(g2.data.state.hearts, []);
  eq(g2.data.edited_final, null);
  eq(g2.data.edited_at, null);
  eq((await t.api("PUT", `/p/${token}/products/${a}/state`, { rev: 3, state: {} })).data.error, "stale");
});

Deno.test("PUT state：rev 衝突 409、final 切換狀態、型別與大小驗證", async () => {
  const t = await setup();
  const token = await t.createProject();
  const d = await t.addProduct(token, "D001");
  eq((await t.api("PUT", `/p/${token}/products/${d}/state`, { rev: 0, state: {} })).data.error, "not_ready");
  const a = await t.ready(token, "A001");
  const put = (rev: unknown, state: unknown) => t.api("PUT", `/p/${token}/products/${a}/state`, { rev, state });
  const rev0 = (await t.api("GET", `/p/${token}/products/${a}`)).data.state_rev;
  const state = {
    draft: [{ id: "A1", text: "改過的字", origin: "A1" }],
    hearts: ["A1", "B1"],
    dislikes: ["A2"],
    banned_words: ["超級"],
    final: null,
  };
  eq((await put(rev0, state)).data, { state_rev: rev0 + 1, status: "ready" });
  const stale = await put(rev0, { hearts: [] });
  eq(stale.status, 409);
  eq(stale.data.error, "stale");
  eq(stale.data.state_rev, rev0 + 1);
  eq(stale.data.state, state);
  const fin = await put(rev0 + 1, { ...state, final: { text: "定稿全文", saved_at: "2026-11-03T11:00:00Z" } });
  eq(fin.data.status, "finalized");
  eq((await t.api("GET", `/p/${token}`)).data.products[1].status, "finalized");
  eq((await put(rev0 + 2, { ...state, final: null })).data.status, "ready");
  eq((await put(rev0 + 3, { final: { text: "x" } })).status, 200);
  eq((await t.api("GET", `/p/${token}/products/${a}`)).data.state.final, { text: "x", saved_at: T0 });
  const rev = rev0 + 4;
  for (
    const bad of [{ hearts: "A1" }, { hearts: [1] }, { draft: [{ id: "A1" }] }, { final: "定稿" }, { evil: 1 }, []]
  ) {
    eq((await put(rev, bad)).status, 400, JSON.stringify(bad));
  }
  eq((await t.api("PUT", `/p/${token}/products/${a}/state`, { state: {} })).data.error, "invalid_rev");
  eq((await put(rev, { draft: [{ id: "A1", text: "字".repeat(22000) }] })).status, 413);
  eq((await put(rev, { draft: [{ id: "A1", text: "a".repeat(60000) }] })).status, 200);
});

Deno.test("edited-final：只能 finalized、≤5000 字、可清除、不影響 state_rev", async () => {
  const t = await setup();
  const token = await t.createProject();
  const r = await t.ready(token, "R001");
  const ef = (pid: string, body: unknown) => t.api("PUT", `/p/${token}/products/${pid}/edited-final`, body);
  const notYet = await ef(r, { text: "x" });
  eq(notYet.status, 409);
  eq(notYet.data.error, "not_finalized");
  const d = await t.addProduct(token, "D001");
  eq((await ef(d, { text: "x" })).status, 409);

  const a = await t.finalized(token, "A001");
  const rev = (await t.api("GET", `/p/${token}/products/${a}`)).data.state_rev;
  t.setClock(new Date("2026-11-03T13:00:00.000Z"));
  const ok = await ef(a, { text: "我實際用的版本\n第二行" });
  eq(ok.data, { edited_at: "2026-11-03T13:00:00.000Z" });
  const g = await t.api("GET", `/p/${token}/products/${a}`);
  eq(g.data.edited_final, "我實際用的版本\n第二行");
  eq(g.data.edited_at, "2026-11-03T13:00:00.000Z");
  eq(g.data.state_rev, rev, "不影響 state_rev");
  eq((await t.api("GET", `/p/${token}`)).data.products.find((p: { id: string }) => p.id === a).has_edited_final, true);
  eq((await ef(a, { text: "字".repeat(5000) })).status, 200);
  eq((await ef(a, { text: "字".repeat(5001) })).data.error, "text_too_long");
  eq((await ef(a, {})).data.error, "invalid_text");
  eq((await ef(a, { text: 1 })).status, 400);
  // 清除
  eq((await ef(a, { text: "" })).data, { edited_at: null });
  const g2 = await t.api("GET", `/p/${token}/products/${a}`);
  eq([g2.data.edited_final, g2.data.edited_at], [null, null]);
  eq((await t.api("GET", `/p/${token}`)).data.products.find((p: { id: string }) => p.id === a).has_edited_final, false);
  // 回去修改（取消定稿）後就不能改 edited_final
  await ef(a, { text: "留著" });
  await t.api("PUT", `/p/${token}/products/${a}/state`, { rev, state: { final: null } });
  eq((await ef(a, { text: "y" })).data.error, "not_finalized");
  eq((await t.api("GET", `/p/${token}/products/${a}`)).data.edited_final, "留著", "取消定稿時保留");
});

Deno.test("close：需 confirm、冪等、webhook body、之後寫入全回 409 project_closed、GET 唯讀總覽", async () => {
  const t = await setup({ webhook: true });
  const token = await t.createProject();
  const a = await t.finalized(token, "A001", "A 的定稿");
  await t.api("PUT", `/p/${token}/products/${a}/edited-final`, { text: "A 我改過的" });
  const b = await t.finalized(token, "B001", "B 的定稿");
  const r = await t.ready(token, "R001");
  const d = await t.addProduct(token, "D001");
  await t.addPhoto(token, d);
  const callsBefore = t.calls.length;

  eq((await t.api("POST", `/p/${token}/close`)).data.error, "confirm_required");
  eq((await t.api("POST", `/p/${token}/close`, { confirm: "true" })).status, 400);
  eq(t.db.projects.get(token)!.status, "open");

  t.setClock(new Date("2026-11-04T09:00:00.000Z"));
  const c = await t.api("POST", `/p/${token}/close`, { confirm: true });
  eq(c.status, 200);
  eq(c.data, { status: "closed", closed_at: "2026-11-04T09:00:00.000Z" });
  eq(t.calls.length, callsBefore + 1);
  eq(webhookBody(t.calls.at(-1)!), {
    event: "panel.project.closed",
    project_token: token,
    product_ids: [a, b, r],
    product_names: [goodFields.name, goodFields.name, goodFields.name],
    skus: ["A001", "B001", "R001"],
    submitted_at: "2026-11-04T09:00:00.000Z",
    closed_at: "2026-11-04T09:00:00.000Z",
  });
  eq(new Headers(t.calls.at(-1)!.init.headers).get("authorization"), `Bearer ${WEBHOOK_KEY}`);
  const pj = t.db.projects.get(token)!;
  eq([pj.status, pj.closed_at, pj.expires_at, pj.close_notify_status], [
    "closed",
    "2026-11-04T09:00:00.000Z",
    "2027-05-03T09:00:00.000Z",
    "ok",
  ]);

  // 冪等：不需要 confirm、不重複通知
  t.setClock(new Date("2026-11-05T00:00:00.000Z"));
  eq((await t.api("POST", `/p/${token}/close`, {})).data, { status: "closed", closed_at: "2026-11-04T09:00:00.000Z" });
  eq((await t.bot("POST", `/bot/projects/${token}/close`)).data.closed_at, "2026-11-04T09:00:00.000Z");
  eq(t.calls.length, callsBefore + 1);

  // 之後所有使用者端寫入都 409 project_closed（就算 body 不合法、商品不存在）
  const dp = (await t.api("GET", `/p/${token}/products/${d}`)).data.photos[0].path;
  const writes: [string, string, unknown][] = [
    ["POST", `/p/${token}/products`, { sku: "NEW1" }],
    ["POST", `/p/${token}/products`, { sku: "bad sku" }],
    ["PATCH", `/p/${token}/products/${d}`, { sku: "D002" }],
    ["DELETE", `/p/${token}/products/${d}`, undefined],
    ["POST", `/p/${token}/products/${d}/upload-url`, { type: "image/jpeg", size: 10 }],
    ["POST", `/p/${token}/products/${d}/photos`, { path: dp }],
    ["DELETE", `/p/${token}/products/${d}/photos`, { path: dp }],
    ["PUT", `/p/${token}/products/${d}/fields`, { fields: goodFields }],
    ["PUT", `/p/${token}/products/${r}/state`, { rev: 1, state: {} }],
    ["PUT", `/p/${token}/products/${a}/edited-final`, { text: "x" }],
    ["PUT", `/p/${token}/products/00000000-0000-4000-8000-999999999999/fields`, { fields: {} }],
    ["POST", `/p/${token}/submit`, { product_ids: [d] }],
    ["POST", `/p/${token}/submit`, {}],
  ];
  for (const [m, p, body] of writes) {
    const res = await t.api(m, p, body);
    eq([res.status, res.data.error], [409, "project_closed"], `${m} ${p}`);
  }
  assert(t.storage.objects.has(dp), "照片沒有被刪");
  eq((await t.api("GET", `/p/${token}/products/${d}`)).data.status, "draft");

  // GET 唯讀總覽
  const g = await t.api("GET", `/p/${token}`);
  eq(g.data.status, "closed");
  eq(g.data.closed_at, "2026-11-04T09:00:00.000Z");
  eq(
    g.data.products.map((
      p: Record<string, unknown>,
    ) => [p.sku, p.status, p.final_text, p.edited_final, p.has_edited_final]),
    [
      ["A001", "finalized", "A 的定稿", "A 我改過的", true],
      ["B001", "finalized", "B 的定稿", null, false],
      ["R001", "ready", null, null, false],
      ["D001", "draft", null, null, false],
    ],
  );
  eq(Object.keys(g.data.products[0]), [
    "id",
    "sku",
    "name",
    "status",
    "position",
    "photo_count",
    "updated_at",
    "has_edited_final",
    "final_text",
    "edited_final",
  ]);
  const pg = await t.api("GET", `/p/${token}/products/${a}`);
  eq(pg.data.project_status, "closed");
  eq(pg.data.edited_final, "A 我改過的");
  // 開啟中的 GET 不帶 final_text／edited_final
  const o = await t.createProject();
  await t.addProduct(o, "X");
  assert(!("final_text" in (await t.api("GET", `/p/${o}`)).data.products[0]));
});

Deno.test("Bot close／reopen：bot 不需 confirm 並發 webhook；reopen 後可寫入、再結束會再通知", async () => {
  const t = await setup({ webhook: true });
  const token = await t.createProject();
  const a = await t.finalized(token, "A001");
  const n = t.calls.length;
  const c = await t.bot("POST", `/bot/projects/${token}/close`);
  eq(c.data, {
    status: "closed",
    closed_at: T0,
    expires_at: "2027-05-02T10:00:00.000Z",
    close_notify_status: "ok",
  });
  eq(t.calls.length, n + 1);
  eq(webhookBody(t.calls[n]).event, "panel.project.closed");
  eq(webhookBody(t.calls[n]).product_ids, [a]);
  eq((await t.api("PUT", `/p/${token}/products/${a}/edited-final`, { text: "x" })).data.error, "project_closed");

  const r = await t.bot("POST", `/bot/projects/${token}/reopen`);
  eq(r.data, { status: "open" });
  const pj = t.db.projects.get(token)!;
  eq([pj.status, pj.closed_at, pj.expires_at], ["open", null, null]);
  eq((await t.bot("POST", `/bot/projects/${token}/reopen`)).data, { status: "open" }, "冪等");
  eq((await t.api("PUT", `/p/${token}/products/${a}/edited-final`, { text: "x" })).status, 200);
  eq((await t.api("GET", `/p/${token}`)).data.status, "open");
  await t.api("POST", `/p/${token}/close`, { confirm: true });
  eq(t.calls.length, n + 2, "重新結束會再通知");

  // webhook 未設定時 close_notify_status = unset
  const u = await setup();
  const ut = await u.createProject();
  eq((await u.bot("POST", `/bot/projects/${ut}/close`)).data.close_notify_status, "unset");
  eq(u.calls.length, 0);
});

Deno.test("Bot：專案列表（status 篩選、counts）、專案詳細、商品 GET（2 小時網址等）、synced", async () => {
  const t = await setup();
  const p1 = await t.createProject(["長褲"], "第一期");
  t.setClock(new Date("2026-11-03T11:00:00.000Z"));
  const p2 = await t.createProject(["長褲"], "第二期");
  await t.addProduct(p1, "D1");
  await t.addProduct(p1, "D2");
  const s = await t.submitted(p1, "S1");
  await t.ready(p1, "R1");
  await t.finalized(p2, "F1");
  await t.api("POST", `/p/${p2}/close`, { confirm: true });

  const list = await t.bot("GET", "/bot/projects");
  eq(list.data.map((p: { token: string }) => p.token), [p2, p1], "新的在前");
  eq(list.data[1], {
    token: p1,
    title: "第一期",
    status: "open",
    created_at: T0,
    closed_at: null,
    counts: { draft: 2, submitted: 1, generating: 0, ready: 1, finalized: 0 },
  });
  eq(list.data[0].counts, { draft: 0, submitted: 0, generating: 0, ready: 0, finalized: 1 });
  eq((await t.bot("GET", "/bot/projects?status=open")).data.map((p: { token: string }) => p.token), [p1]);
  eq((await t.bot("GET", "/bot/projects?status=closed")).data.map((p: { token: string }) => p.token), [p2]);
  eq((await t.bot("GET", "/bot/projects?status=bogus")).status, 400);

  const d = await t.bot("GET", `/bot/projects/${p1}`);
  eq(Object.keys(d.data), [
    "token",
    "title",
    "status",
    "closed_at",
    "close_notify_status",
    "options",
    "created_at",
    "products",
  ]);
  eq(d.data.products.map((p: { sku: string }) => p.sku), ["D1", "D2", "S1", "R1"]);
  const sp = d.data.products[2];
  eq(sp, {
    id: s,
    sku: "S1",
    name: goodFields.name,
    status: "submitted",
    position: 3,
    photo_count: 1,
    submitted_at: "2026-11-03T11:00:00.000Z",
    claimed_at: null,
    synced_at: null,
    notify_status: "unset",
    has_edited_final: false,
    updated_at: "2026-11-03T11:00:00.000Z",
  });

  const g = await t.bot("GET", `/bot/projects/${p1}/products/${s}`);
  assert(g.data.photos[0].url.includes("ttl=7200"));
  for (const k of ["notify_status", "synced_at", "claimed_at", "submitted_at", "project_status"]) {
    assert(k in g.data, k);
  }
  const ug = await t.api("GET", `/p/${p1}/products/${s}`);
  for (const k of ["notify_status", "synced_at", "claimed_at", "submitted_at"]) {
    assert(!(k in ug.data), `使用者端不回 ${k}`);
  }
  const sy = await t.bot("POST", `/bot/projects/${p1}/products/${s}/synced`);
  eq(sy.data, { synced_at: "2026-11-03T11:00:00.000Z" });
  eq((await t.bot("POST", `/bot/projects/${p1}/products/${s}/synced`)).status, 200, "冪等");
  eq((await t.bot("GET", `/bot/projects/${p1}/products/${s}`)).data.synced_at, "2026-11-03T11:00:00.000Z");
  eq((await t.bot("POST", `/bot/projects/${p1}/products/${s}/bogus`)).status, 404);
  eq((await t.bot("GET", `/bot/projects/${p2}/products/${s}`)).status, 404, "商品不屬於該專案");
});

Deno.test("Bot queue：跨專案、指定專案、status 篩選、依 submitted_at 由舊到新", async () => {
  const t = await setup();
  const p1 = await t.createProject(["長褲"], "第一期");
  const p2 = await t.createProject(["長褲"], "第二期");
  t.setClock(new Date("2026-11-03T10:05:00.000Z"));
  const b = await t.submitted(p2, "B");
  t.setClock(new Date("2026-11-03T10:01:00.000Z"));
  const a = await t.submitted(p1, "A");
  t.setClock(new Date("2026-11-03T10:09:00.000Z"));
  const c = await t.submitted(p1, "C");
  await t.addProduct(p1, "DRAFT");
  await t.bot("POST", `/bot/projects/${p1}/products/${c}/claim`);
  t.setClock(new Date("2026-11-03T10:03:00.000Z"));
  const e = await t.submitted(p2, "E");
  await t.api("POST", `/p/${p2}/close`, { confirm: true });

  const q = await t.bot("GET", "/bot/queue");
  eq(q.data.map((r: { product_id: string }) => r.product_id), [a, e, b], "預設只有 submitted，舊到新");
  eq(q.data[0], {
    project_token: p1,
    project_title: "第一期",
    project_status: "open",
    product_id: a,
    sku: "A",
    name: goodFields.name,
    status: "submitted",
    submitted_at: "2026-11-03T10:01:00.000Z",
    claimed_at: null,
    updated_at: "2026-11-03T10:01:00.000Z",
  });
  eq(q.data[1].project_status, "closed");
  const both = await t.bot("GET", "/bot/queue?status=submitted,generating");
  eq(both.data.map((r: { product_id: string }) => r.product_id), [a, e, b, c]);
  eq(both.data[3].claimed_at, "2026-11-03T10:09:00.000Z");
  const only = await t.bot("GET", `/bot/queue?project=${p1}&status=submitted,generating`);
  eq(only.data.map((r: { product_id: string }) => r.product_id), [a, c]);
  eq((await t.bot("GET", "/bot/queue?status=draft")).data.map((r: { sku: string }) => r.sku), ["DRAFT"]);
  eq((await t.bot("GET", "/bot/queue?status=bogus")).status, 400);
  eq((await t.bot("GET", "/bot/queue?status=submitted,open")).status, 400);
  eq((await t.bot("GET", `/bot/queue?project=${"a".repeat(43)}`)).status, 404);
});

Deno.test("Bot export：整個專案（不含照片網址），closed／過期仍可匯出", async () => {
  const t = await setup();
  const token = await t.createProject(["長褲"], "十一月");
  const a = await t.finalized(token, "A001", "定稿全文");
  await t.api("PUT", `/p/${token}/products/${a}/edited-final`, { text: "修改後" });
  const d = await t.addProduct(token, "D001");
  await t.addPhoto(token, d);
  await t.api("POST", `/p/${token}/close`, { confirm: true });
  t.setClock(new Date("2030-01-01T00:00:00.000Z"));
  eq((await t.api("GET", `/p/${token}`)).status, 404, "已過期");
  const x = await t.bot("GET", `/bot/projects/${token}/export`);
  eq(x.status, 200);
  assert(!JSON.stringify(x.data).includes("storage.test"), "不含照片網址");
  eq(Object.keys(x.data), ["token", "title", "status", "closed_at", "exported_at", "products"]);
  eq([x.data.token, x.data.title, x.data.status, x.data.closed_at, x.data.exported_at], [
    token,
    "十一月",
    "closed",
    T0,
    "2030-01-01T00:00:00.000Z",
  ]);
  eq(x.data.products[0], {
    id: a,
    sku: "A001",
    position: 1,
    status: "finalized",
    fields: goodFields,
    product_summary: "寬褲",
    routes: {
      A: [{ id: "A1", text: "一穿就愛上", check: false }, { id: "A2", text: "[小編超推]", check: true }],
      B: [{ id: "B1", text: "高腰顯瘦", check: false }],
      C: [
        { id: "C1", text: "秋天通勤", check: false },
        { id: "C2", text: "週末出遊", check: false },
        { id: "C3", text: "搭白T", check: false },
      ],
    },
    state: {
      draft: [{ id: "A1", text: "一穿就愛上" }],
      hearts: ["A1"],
      dislikes: [],
      banned_words: [],
      final: { text: "定稿全文", saved_at: T0 },
    },
    edited_final: "修改後",
    edited_at: T0,
  });
  eq(x.data.products[1], {
    id: d,
    sku: "D001",
    position: 2,
    status: "draft",
    fields: {},
    product_summary: "",
    routes: { A: [], B: [], C: [] },
    state: { draft: [], hearts: [], dislikes: [], banned_words: [], final: null },
    edited_final: null,
    edited_at: null,
  });
});

Deno.test("Bot DELETE 專案：刪專案、全部商品、全部照片（含未登記上傳），其他專案不受影響", async () => {
  const t = await setup();
  const token = await t.createProject();
  const keep = await t.createProject();
  const a = await t.addProduct(token, "A");
  const b = await t.addProduct(token, "B");
  await t.addPhoto(token, a);
  await t.addPhoto(token, b);
  t.storage.objects.set(`${token}/${a}/orphan.jpg`, { size: 1 });
  t.storage.objects.set(`${token}/loose.jpg`, { size: 1 });
  const k = await t.addProduct(keep, "A");
  await t.addPhoto(keep, k);
  eq((await t.api("DELETE", `/bot/projects/${token}`)).status, 401);
  const d = await t.bot("DELETE", `/bot/projects/${token}`);
  eq(d.data, { deleted: true, photos_removed: 4 });
  eq((await t.api("GET", `/p/${token}`)).status, 404);
  eq((await t.bot("GET", `/bot/projects/${token}`)).status, 404);
  eq([...t.storage.objects.keys()].filter((x) => x.startsWith(token)).length, 0);
  eq([...t.db.products.values()].filter((p) => p.project_token === token).length, 0, "商品一起刪");
  eq([...t.storage.objects.keys()].filter((x) => x.startsWith(keep)).length, 1, "其他專案不受影響");
  eq((await t.api("GET", `/p/${keep}/products/${k}`)).status, 200);
  eq((await t.bot("DELETE", `/bot/projects/${token}`)).status, 404);
});

Deno.test("Bot config：設定、讀取不回金鑰、清除、驗證", async () => {
  const t = await setup();
  const put = await t.bot("PUT", "/bot/config", { webhook_url: "https://bot.test/h", webhook_key: WEBHOOK_KEY });
  eq(put.data, { webhook_url: "https://bot.test/h", webhook_key_set: true });
  eq(t.db.config.get("webhook_key"), WEBHOOK_KEY);
  eq((await t.bot("GET", "/bot/config")).data, { webhook_url: "https://bot.test/h", webhook_key_set: true });
  eq((await t.bot("PUT", "/bot/config", { webhook_url: "ftp://x" })).status, 400);
  eq((await t.bot("PUT", "/bot/config", { webhook_key: "" })).status, 400);
  eq((await t.bot("PUT", "/bot/config", {})).status, 400);
  eq((await t.bot("PUT", "/bot/config", { clear_webhook: true })).data, { webhook_url: null, webhook_key_set: false });
  assert(t.db.config.has("bot_key_sha256"), "清除 webhook 不影響 bot key");
});

Deno.test("完整流程：兩個商品一起送出 → 各自 claim／result → 挑句定稿 → 修改後文案 → 結束 → 匯出", async () => {
  const t = await setup({ webhook: true });
  const token = await t.createProject();
  const a = await t.completeDraft(token, "A001");
  const b = await t.completeDraft(token, "B001");
  eq((await t.api("POST", `/p/${token}/submit`, { product_ids: [a, b] })).data.submitted, [a, b]);
  for (const pid of [a, b]) {
    eq((await t.bot("POST", `/bot/projects/${token}/products/${pid}/claim`)).data.claimed, true);
    eq((await t.bot("POST", `/bot/projects/${token}/products/${pid}/result`, { routes: sampleRoutes })).status, 200);
    const rev = (await t.api("GET", `/p/${token}/products/${pid}`)).data.state_rev;
    await t.api("PUT", `/p/${token}/products/${pid}/state`, { rev, state: { final: { text: `定稿 ${pid}` } } });
  }
  await t.api("PUT", `/p/${token}/products/${a}/edited-final`, { text: "A 修改後" });
  await t.api("POST", `/p/${token}/close`, { confirm: true });
  const g = await t.api("GET", `/p/${token}`);
  eq(g.data.products.map((p: { edited_final: string | null }) => p.edited_final), ["A 修改後", null]);
  eq(g.data.products.map((p: { final_text: string }) => p.final_text), [`定稿 ${a}`, `定稿 ${b}`]);
  const x = await t.bot("GET", `/bot/projects/${token}/export`);
  eq(x.data.products.map((p: { edited_final: string | null }) => p.edited_final), ["A 修改後", null]);
  eq(t.calls.map((c) => webhookBody(c).event), ["panel.products.submitted", "panel.project.closed"]);
});

Deno.test("內部錯誤回 500 且不帶細節", async () => {
  const t = await setup();
  const logs: string[] = [];
  const h = createHandler({
    db: Object.assign(Object.create(t.db), { getProject: () => Promise.reject(new Error("db down")) }) as Db,
    storage: t.storage,
    log: (m) => logs.push(m),
  });
  const res = await h(new Request(`${BASE}/p/${"a".repeat(43)}`));
  eq(res.status, 500);
  const body = await res.json();
  eq(body.error, "internal");
  assert(!JSON.stringify(body).includes("db down"));
  eq(logs.length, 1);
});
