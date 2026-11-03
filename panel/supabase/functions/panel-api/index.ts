// panel-api 進入點：建立 Supabase client，把資料庫與儲存空間包成 handler 需要的介面。
// 所有路由邏輯都在 handler.ts。
import { createClient } from "npm:@supabase/supabase-js@2";
import {
  createHandler,
  type Db,
  type PhotoOpResult,
  type ProductRow,
  type ProductStatus,
  type ProjectRow,
  type ProjectStatus,
  type QueueRow,
  type Storage,
} from "./handler.ts";

const BUCKET = "healer-panel";
const PROJECTS = "hcp_projects";
const PRODUCTS = "hcp_products";
const CONFIG = "hcp_config";

const supabase = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
  auth: { persistSession: false, autoRefreshToken: false },
});

type PgError = { message: string; code?: string } | null;

function check<T>(r: { data: T; error: PgError }, what: string): T {
  if (r.error) throw new Error(`${what}: ${r.error.message}`);
  return r.data;
}

const isUniqueViolation = (e: PgError) => e?.code === "23505";

const db: Db = {
  async getConfig() {
    const rows = check(await supabase.from(CONFIG).select("key,value"), "讀取設定") ?? [];
    return Object.fromEntries(rows.map((r: { key: string; value: string }) => [r.key, r.value]));
  },
  async setConfig(entries) {
    const rows = Object.entries(entries).map(([key, value]) => ({ key, value }));
    check(await supabase.from(CONFIG).upsert(rows), "寫入設定");
  },
  async deleteConfig(keys) {
    check(await supabase.from(CONFIG).delete().in("key", keys), "刪除設定");
  },

  async getProject(token) {
    return check(await supabase.from(PROJECTS).select("*").eq("token", token).maybeSingle(), "讀取專案") as
      | ProjectRow
      | null;
  },
  async insertProject(row) {
    check(await supabase.from(PROJECTS).insert(row), "建立專案");
  },
  async updateProject(token, patch, where) {
    let q = supabase.from(PROJECTS).update(patch).eq("token", token);
    if (where?.status) q = q.in("status", where.status);
    return check(await q.select("*").maybeSingle(), "更新專案") as ProjectRow | null;
  },
  async listProjects(status?: ProjectStatus) {
    let q = supabase.from(PROJECTS).select("*").order("created_at", { ascending: false }).limit(200);
    if (status) q = q.eq("status", status);
    return (check(await q, "列出專案") ?? []) as ProjectRow[];
  },
  async deleteProject(token) {
    const rows = check(await supabase.from(PROJECTS).delete().eq("token", token).select("token"), "刪除專案");
    return (rows ?? []).length > 0;
  },

  async listProducts(projectToken) {
    const r = await supabase.from(PRODUCTS).select("*").eq("project_token", projectToken)
      .order("position", { ascending: true }).order("created_at", { ascending: true });
    return (check(r, "列出商品") ?? []) as ProductRow[];
  },
  async listProductStatuses(projectTokens) {
    const out: { project_token: string; status: ProductStatus }[] = [];
    for (let i = 0; i < projectTokens.length; i += 100) {
      const r = await supabase.from(PRODUCTS).select("project_token,status")
        .in("project_token", projectTokens.slice(i, i + 100));
      out.push(...((check(r, "統計商品狀態") ?? []) as typeof out));
    }
    return out;
  },
  async getProduct(projectToken, id) {
    const r = await supabase.from(PRODUCTS).select("*").eq("project_token", projectToken).eq("id", id).maybeSingle();
    return check(r, "讀取商品") as ProductRow | null;
  },
  async insertProduct(row) {
    const r = await supabase.from(PRODUCTS).insert(row).select("*").single();
    if (isUniqueViolation(r.error)) return "duplicate_sku";
    return check(r, "建立商品") as ProductRow;
  },
  async updateProduct(projectToken, id, patch, where) {
    let q = supabase.from(PRODUCTS).update(patch).eq("project_token", projectToken).eq("id", id);
    if (where?.status) q = q.in("status", where.status);
    if (where?.state_rev !== undefined) q = q.eq("state_rev", where.state_rev);
    return check(await q.select("*").maybeSingle(), "更新商品") as ProductRow | null;
  },
  async renameProduct(projectToken, id, sku, updatedAt) {
    const r = await supabase.from(PRODUCTS).update({ sku, updated_at: updatedAt })
      .eq("project_token", projectToken).eq("id", id).eq("status", "draft").select("*").maybeSingle();
    if (isUniqueViolation(r.error)) return "duplicate_sku";
    return check(r, "修改商品編號") as ProductRow | null;
  },
  async updateProducts(projectToken, ids, patch, where) {
    if (!ids.length) return [];
    let q = supabase.from(PRODUCTS).update(patch).eq("project_token", projectToken).in("id", ids);
    if (where?.status) q = q.in("status", where.status);
    if (where?.state_rev !== undefined) q = q.eq("state_rev", where.state_rev);
    return (check(await q.select("*"), "批次更新商品") ?? []) as ProductRow[];
  },
  async deleteProduct(projectToken, id, where) {
    let q = supabase.from(PRODUCTS).delete().eq("project_token", projectToken).eq("id", id);
    if (where?.status) q = q.in("status", where.status);
    const rows = check(await q.select("id"), "刪除商品");
    return (rows ?? []).length > 0;
  },
  async listQueue(statuses, projectToken) {
    let q = supabase.from(PRODUCTS)
      .select(
        "id,project_token,sku,position,status,fields,submitted_at,claimed_at,updated_at,project:hcp_projects!inner(title,status)",
      )
      .in("status", statuses)
      .order("submitted_at", { ascending: true, nullsFirst: false })
      .limit(500);
    if (projectToken) q = q.eq("project_token", projectToken);
    const r = await q;
    if (r.error) throw new Error(`列出待處理商品: ${r.error.message}`);
    type Raw = Omit<QueueRow, "project_title" | "project_status"> & {
      project: { title: string; status: ProjectStatus } | { title: string; status: ProjectStatus }[] | null;
    };
    return ((r.data ?? []) as unknown as Raw[]).map(({ project, ...rest }) => {
      const pj = Array.isArray(project) ? project[0] : project;
      return { ...rest, project_title: pj?.title ?? "", project_status: pj?.status ?? "open" };
    });
  },

  async addPhoto(productId, photo, max) {
    return check(
      await supabase.rpc("hcp_photo_add", {
        p_product: productId,
        p_path: photo.path,
        p_name: photo.name,
        p_size: photo.size,
        p_max: max,
      }),
      "新增照片",
    ) as PhotoOpResult;
  },
  async removePhoto(productId, path) {
    return check(
      await supabase.rpc("hcp_photo_remove", { p_product: productId, p_path: path }),
      "刪除照片",
    ) as PhotoOpResult;
  },
};

const bucket = () => supabase.storage.from(BUCKET);

const storage: Storage = {
  async createSignedUploadUrl(path) {
    const d = check(await bucket().createSignedUploadUrl(path), "建立上傳網址");
    if (!d) throw new Error("建立上傳網址：沒有回傳資料");
    return { signed_url: d.signedUrl, token: d.token };
  },
  async signedUrls(paths, expiresIn) {
    const d = check(await bucket().createSignedUrls(paths, expiresIn), "建立下載網址") ?? [];
    const byPath = new Map(d.map((x) => [x.path, x.error ? null : x.signedUrl]));
    return paths.map((p) => byPath.get(p) ?? null);
  },
  async stat(path) {
    const slash = path.lastIndexOf("/");
    const dir = path.slice(0, slash);
    const name = path.slice(slash + 1);
    const list = check(await bucket().list(dir, { limit: 100, search: name }), "查詢照片") ?? [];
    const obj = list.find((o) => o.name === name);
    if (!obj || !obj.id) return null;
    const meta = (obj.metadata ?? {}) as { size?: number; mimetype?: string };
    return { size: Number(meta.size ?? 0), mimetype: meta.mimetype };
  },
  async listFolder(prefix) {
    // Storage 的 list 不會遞迴：id 為 null 的項目是子資料夾（例如 <token>/<product_id>/）。
    const out: string[] = [];
    const walk = async (dir: string, depth: number) => {
      for (let offset = 0;; offset += 1000) {
        const list = check(await bucket().list(dir, { limit: 1000, offset }), "列出照片") ?? [];
        for (const o of list) {
          if (o.id) out.push(`${dir}/${o.name}`);
          else if (depth < 3) await walk(`${dir}/${o.name}`, depth + 1);
        }
        if (list.length < 1000) break;
      }
    };
    await walk(prefix, 0);
    return out;
  },
  async remove(paths) {
    for (let i = 0; i < paths.length; i += 1000) {
      check(await bucket().remove(paths.slice(i, i + 1000)), "刪除照片檔");
    }
  },
};

const handler = createHandler({
  db,
  storage,
  config: {
    webhookUrl: Deno.env.get("PANEL_WEBHOOK_URL") ?? undefined,
    webhookKey: Deno.env.get("PANEL_WEBHOOK_KEY") ?? undefined,
  },
});

Deno.serve(handler);
