# 文案面板後端（Supabase）— v2：專案＋多商品

面板後端是一支 Supabase Edge Function `panel-api`，加上幾張 `hcp_` 開頭的資料表和一個私有的照片 bucket
`healer-panel`。手機上的面板（`panel/public/`）和 Grok
Bot（`scripts/panel_session.py`）都透過這支函式讀寫資料，不會直接碰資料庫。

API 基底網址：`https://<專案代號>.supabase.co/functions/v1/panel-api`（`<專案代號>` 是你自己 Supabase 專案的 project ref）。

**全新部署請照 repo 根目錄的 [`DEPLOY.md`](../../DEPLOY.md) 一步步做**；這份文件是後端的規格與參考。

v2
把「一個連結＝一個商品」改成「一個連結＝一期專案，裡面可以有很多商品」：使用者可以新增多個商品、各自上傳照片和填欄位、一次送出好幾個；按「結束這期專案」之後連結變成唯讀總覽頁。v1
的 `/s/…` 與 `/bot/sessions…` 路由已經移除（呼叫一律回 404）。

## 檔案

| 檔案                                  | 用途                                                                                                                                          |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `migrations/001_init.sql`             | v1：`hcp_config`、bucket（v1 的 `hcp_sessions` 也在這裡建立，會被 002 移除）。保留不改。全新部署也要先跑它。                                  |
| `migrations/002_projects.sql`         | v2：建立 `hcp_projects`、`hcp_products`、新的照片函式；移除 `hcp_sessions` 與舊照片函式。可以重複執行。                                       |
| `functions/panel-api/index.ts`        | 進入點：建立 Supabase client，交給 `handler.ts` 處理。                                                                                        |
| `functions/panel-api/handler.ts`      | 所有路由、授權、驗證。資料庫、儲存空間、時間、亂數都由外部傳入（`createHandler({db, storage, config, fetch, now, random, log})`），方便測試。 |
| `functions/panel-api/handler_test.ts` | 測試，用記憶體裡的假資料庫與假儲存空間，不需要連線。                                                                                          |

## 資料表

- **`hcp_config`**（`key`, `value`）：設定值（v1 不變）。
  - `bot_key_sha256`：Bot 金鑰的 SHA-256（hex）。**必填**，沒有設定時所有 Bot 端 API 都回 401。
  - `webhook_url`、`webhook_key`：送出商品、結束專案時要通知的網址與金鑰（選填）。
- **`hcp_projects`**：一期專案，以 `token`（43 字元隨機字串）當主鍵，網址是 `/p/<token>`。
  - `title`、`options`（`{"categories": [...]}` 官方類別清單）。
  - `status`：`open`（進行中）或 `closed`（已結束）。資料庫有檢查。
  - `closed_at`、`close_notify_status`（結束時的通知結果）。
  - `expires_at`：進行中為 null（**不會自動過期**）；結束時設成 180 天後。過期後使用者端回 404，Bot
    端仍讀得到。只是保留期限，沒有自動清理。
- **`hcp_products`**：專案內的商品，主鍵 `id`（uuid），`project_token` 參照專案（刪專案會一起刪）。
  - `sku`（商品編號）：專案內不可重複（`unique (project_token, sku)`）。
  - `position`：顯示順序（新增時遞增）。
  - `status`：`draft`（草稿）→ `submitted`（已送出）→ `generating`（撰寫中，Bot 已領取）→ `ready`（可挑句）→
    `finalized`（已定稿）。資料庫有檢查。
  - `fields`、`photos`、`result`、`state`、`state_rev`：同 v1，只是改成每個商品各一份。
  - `edited_final`、`edited_at`：使用者貼上的「修改後的最終文案」（定稿後才能填，≤ 5000 字）。
  - `notify_status`、`submitted_at`、`claimed_at`、`synced_at`。
- **`hcp_photo_add(p_product uuid, …)`／`hcp_photo_remove(p_product uuid, p_path)`**：在資料庫裡一次完成「讀清單、加一張（或刪一張）、寫回去」，手機同時上傳多張也不會互相蓋掉。商品必須是
  `draft`、專案必須 `open`。只有 service role 能呼叫。
- **Storage bucket `healer-panel`**：私有，照片路徑 `<project_token>/<product_id>/<檔名>`。bucket 設了單檔 8MB、只收
  JPEG／PNG／WebP。

所有表都開了 RLS 而且沒有任何 policy，只有函式裡的 service role 能讀寫。

`notify_status`／`close_notify_status` 可能的值：`unset`（沒設定
webhook）、`ok`、`failed:<HTTP 狀態碼>`、`failed:network`、`failed:timeout`（超過 5 秒）。通知失敗不會讓送出或結束失敗。

## API 一覽

回應都是 JSON；錯誤格式 `{"error": "代碼", "message": "中文說明"}`（有些錯誤另外帶 `items`、`ids`、`status`
等欄位）。所有回應都帶 CORS（`access-control-allow-origin: *`）。商品一律用 **product id（uuid）** 識別，不接受 sku。

### 使用者端（網址裡的專案 token 就是授權）

token 格式錯、不存在、已過期都回 404。專案結束後，所有寫入都回 **409 `project_closed`**（GET 照常）。

| 方法與路徑                                 | 說明                                                                                                                                                                                                                                                    |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /p/:token`                            | `{title, status, closed_at, options, server_time, products:[{id, sku, name, status, position, photo_count, updated_at, has_edited_final}]}`。專案已結束時，每個商品另外帶 `final_text`（定稿全文或 null）與 `edited_final`（或 null），給唯讀總覽頁用。 |
| `POST /p/:token/products`                  | `{sku}` → 201 `{id, sku, status:"draft", position}`。sku 必填、≤ 40 字，只能用英文字母、數字、`-_.` 與中日韓文字，不能有空白或斜線（前後空白會去掉）；重複回 409 `duplicate_sku`；每期最多 30 個商品（409 `too_many_products`）。                       |
| `GET /p/:token/products/:pid`              | `{id, sku, status, fields, photos:[{path,name,size,order,url}], result, state, state_rev, edited_final, edited_at, updated_at, server_time, project_status}`（照片網址 1 小時有效）。                                                                   |
| `PATCH /p/:token/products/:pid`            | `{sku}` → `{id, sku}`。只有 `draft` 可改（409 `not_draft`），驗證與重複檢查同上。                                                                                                                                                                       |
| `DELETE /p/:token/products/:pid`           | 只有 `draft` 可刪，連同照片檔 → `{deleted:true, photos_removed}`。                                                                                                                                                                                      |
| `POST /p/:token/products/:pid/upload-url`  | `{name, type, size}` → `{path, signed_url, token}`。只收 JPEG／PNG／WebP、單檔 ≤ 8MB、每商品最多 20 張，且商品必須是 `draft`。前端拿 `signed_url` 用 PUT 直接傳到 Storage。                                                                             |
| `POST /p/:token/products/:pid/photos`      | `{path, name}`：確認檔案真的在 Storage，再加進照片清單（大小以 Storage 為準）→ `{photos}`。                                                                                                                                                             |
| `DELETE /p/:token/products/:pid/photos`    | `{path}` → `{photos}`。                                                                                                                                                                                                                                 |
| `PUT /p/:token/products/:pid/fields`       | `{fields}` → `{fields}`。欄位同 v1（name ≤ 120 字、其他 ≤ 1000 字），`category` 必須在專案的 `options.categories` 內（可空白）。                                                                                                                        |
| `PUT /p/:token/products/:pid/state`        | `{state, rev}` → `{state_rev, status}`。同 v1：只能在 `ready`／`finalized`；`rev` 不是最新回 409 `{error:"stale", state, state_rev}`；`final` 有值就變 `finalized`、設回 null 回到 `ready`；上限 64KB。                                                 |
| `PUT /p/:token/products/:pid/edited-final` | `{text}` → `{edited_at}`。只能在 `finalized`（409 `not_finalized`）；≤ 5000 字；空字串代表清除（`edited_final` 與 `edited_at` 都變 null）。不影響 `state_rev`。                                                                                         |
| `POST /p/:token/submit`                    | `{product_ids:[...], fields?}`：一次送出 1–30 個商品，見下方說明。→ `{submitted:[ids], already:[ids], notify_status}`。                                                                                                                                 |
| `POST /p/:token/close`                     | `{confirm:true}`（缺少回 400 `confirm_required`）→ `{status:"closed", closed_at}`。已結束時直接回現狀（不需 confirm、不重複通知）。                                                                                                                     |

**一次送出（submit）的規則**

1. 每個 id 都必須屬於這期專案，否則 404 `product_not_found`（`ids` 列出不認得的）。重複的 id 會合併。
2. 有任何 `ready`／`finalized` 的商品 → 409 `bad_status`（`items:[{id, sku, status}]`），全部不送出。
3. `fields`（選填）：只送一個商品時可以直接給欄位物件（同 v1）；多個商品時用
   `{"<product id>": {...欄位}}`。帶了就先驗證並存起來（只存草稿）。
4. 逐一檢查草稿商品：sku、name、category、至少 1 個色名、material、至少 1 張照片。任何一個缺 → 400
   `{error:"incomplete", items:[{id, sku, missing:[...]}]}`，**全部不送出**，但已存的欄位保留。
5. 已經是 `submitted`／`generating` 的商品視為冪等，放進 `already`、不重複通知。
6. 其餘草稿變成 `submitted`、`submitted_at` 設為現在，並**只發一次** webhook（只含這次新送出的）。沒有新送出的商品時
   `notify_status` 是 null。

### Bot 端（header `x-bot-key`，錯誤或缺少回 401）

Bot 端不受專案結束或過期的限制（都讀得到、改得到）。

| 方法與路徑                                        | 說明                                                                                                                                                                                                                                                                                |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /bot/projects`                              | `{title?, categories:[...]}` → 201 `{token}`。                                                                                                                                                                                                                                      |
| `GET /bot/projects?status=open\|closed`           | `[{token, title, status, created_at, closed_at, counts:{draft, submitted, generating, ready, finalized}}]`，新的在前，最多 200 筆。                                                                                                                                                 |
| `GET /bot/projects/:token`                        | `{token, title, status, closed_at, close_notify_status, options, created_at, products:[{id, sku, name, status, position, photo_count, submitted_at, claimed_at, synced_at, notify_status, has_edited_final, updated_at}]}`。                                                        |
| `GET /bot/queue?project=<token>&status=submitted` | 跨所有專案（或指定專案）的商品清單：`[{project_token, project_title, project_status, product_id, sku, name, status, submitted_at, claimed_at, updated_at}]`。`status` 預設 `submitted`，可用逗號指定多個（例如 `submitted,generating`）。依 `submitted_at` 由舊到新，最多 500 筆。  |
| `GET /bot/projects/:token/products/:pid`          | 同使用者端商品 GET，但照片網址 2 小時有效，另外回 `notify_status, synced_at, claimed_at, submitted_at`。                                                                                                                                                                            |
| `POST /bot/projects/:token/products/:pid/claim`   | `{force?}`。原子領取 `submitted`→`generating`：`{claimed:true, status:"generating", claimed_at}`；已被領走回 `{claimed:false, status:"generating", claimed_at}`；其他狀態回 `{claimed:false, status}`（都是 HTTP 200）。`force:true` 可重領 `generating`（`claimed_at` 改成現在）。 |
| `POST /bot/projects/:token/products/:pid/result`  | `{routes:{A:[{text,check}],B:[...],C:[...]}, product_summary}` → `{status:"ready", counts, state_rev}`。自動編號 A1、A2…；狀態需為 submitted／generating／ready；重送會清空 state、`state_rev`+1、清空 `edited_final`。                                                             |
| `POST /bot/projects/:token/products/:pid/synced`  | 記錄同步回訓練紀錄的時間 → `{synced_at}`。                                                                                                                                                                                                                                          |
| `GET /bot/projects/:token/export`                 | 匯出整個專案：`{token, title, status, closed_at, exported_at, products:[{id, sku, position, status, fields, product_summary, routes, state:{draft, hearts, dislikes, banned_words, final}, edited_final, edited_at}]}`（不含照片網址）。                                            |
| `POST /bot/projects/:token/close`                 | 以 Bot 身分結束專案，不需 confirm，行為同使用者端（會發 webhook）→ `{status, closed_at, expires_at, close_notify_status}`。                                                                                                                                                         |
| `POST /bot/projects/:token/reopen`                | 測試／補救用：closed → open，`closed_at`、`expires_at`、`close_notify_status` 清成 null → `{status:"open"}`。                                                                                                                                                                       |
| `DELETE /bot/projects/:token`                     | 刪專案、全部商品、資料夾裡全部照片檔（含沒登記的上傳）→ `{deleted:true, photos_removed}`。                                                                                                                                                                                          |
| `GET /bot/config`                                 | `{webhook_url, webhook_key_set}`，不會回傳金鑰本身。                                                                                                                                                                                                                                |
| `PUT /bot/config`                                 | `{webhook_url?, webhook_key?}` 或 `{clear_webhook: true}`。                                                                                                                                                                                                                         |

### webhook 通知內容

header 一律是 `content-type: application/json`；有設定 `webhook_key` 才帶 `authorization: Bearer <webhook_key>`。逾時 5
秒，失敗不影響使用者的操作。

送出商品（一次送出只發一次，只含這次新送出的；名稱各截 60 字）：

```
{"event":"panel.products.submitted","project_token":"...","product_ids":["<uuid>",...],
 "product_names":["...",...],"skus":["...",...],"submitted_at":"2026-11-03T10:00:00.000Z"}
```

結束專案（不含草稿商品；`submitted_at` 與 `closed_at` 相同）：

```
{"event":"panel.project.closed","project_token":"...","product_ids":[...],"product_names":[...],
 "skus":[...],"submitted_at":"<closed_at>","closed_at":"2026-11-04T09:00:00.000Z"}
```

## 部署

1. **建立資料表**：依序執行兩個 migration（Supabase MCP 的 `apply_migration`，或貼到 SQL Editor 執行）：
   1. `migrations/001_init.sql`（名稱例如 `hcp_panel_init`）：建立 `hcp_config` 與私有 bucket `healer-panel`。
   2. `migrations/002_projects.sql`（名稱例如 `hcp_panel_projects`）：建立 v2 的表與照片函式。

   **全新部署一定要先 001 再 002。** 001 會建立 v1 的 `hcp_sessions` 表與舊照片函式，002 接著把它們刪掉——空表被刪，
   無害。兩個檔都可以重複執行，不會清掉 v2 的資料。
   - 從 v1 升級的舊部署：已經跑過 001，只要跑 002（會**刪除 v1 的 `hcp_sessions` 表**和舊照片函式，裡面的資料請先匯出）。
     之後不要再單獨重跑 001：001 會重新建立空的 `hcp_sessions` 和舊函式（再跑一次 002 會刪掉，無害但沒必要）。
     v1 留在 bucket 裡的舊照片（路徑 `<舊 token>/<檔名>`）不會被自動刪除，要的話從 Supabase 後台的 Storage 手動刪。
   - 確認 bucket：`select id, public from storage.buckets where id = 'healer-panel';` 應該回一列、`public` 是 `false`。
2. **部署函式**：用 Supabase MCP 的 `deploy_edge_function`，名稱 `panel-api`、`verify_jwt: false`、入口 `index.ts`，上傳
   `index.ts` 和 `handler.ts` 兩個檔案（測試檔不用上傳）；或用 Supabase CLI
   `supabase functions deploy panel-api --no-verify-jwt --project-ref <專案代號>`。函式只用 Supabase 自帶的環境變數
   `SUPABASE_URL`、`SUPABASE_SERVICE_ROLE_KEY`，程式裡沒有任何金鑰。
   - `verify_jwt` 一定要關掉：面板和 Bot 都不會帶 Supabase 登入憑證，授權由函式自己做。
   - **資料表和函式要一起換**：新函式需要新表，舊前端／舊 CLI 用的 `/s/…`、`/bot/sessions…` 在新函式上會
     404。建議順序：migration → 函式 → 前端與 CLI。
3. **Bot 金鑰**：全新部署要產生一組新的金鑰，把 SHA-256 存進 `hcp_config`（見下一段與 `DEPLOY.md`）。
   沒有設定時所有 Bot 端 API 都回 401。已經設定過的部署不需要重設。
4. **（選填）webhook**：用 `python3 scripts/panel_session.py config-webhook --url ... --key ...`，或在函式
   Secrets 設 `PANEL_WEBHOOK_URL`、`PANEL_WEBHOOK_KEY` 當後備（`hcp_config` 的值優先）。沒設定也能用，Bot 用
   `GET /bot/queue` 定時查詢即可。
5. **前端**：`panel/` 部署到 Vercel，部署時用環境變數 `PANEL_API_URL` 指定上面的 API 基底網址
   （`panel/build.mjs` 會據此產生 `public/config.js`，見 `DEPLOY.md`）。

## 產生 Bot 金鑰

金鑰原文只放在 Bot 本機（環境變數或 `~/.config/healer-panel/panel.env`），資料庫只存它的 SHA-256。

```sh
python3 -c "import secrets, hashlib; k = secrets.token_urlsafe(32); print('原文（放 Bot 的 HEALER_PANEL_BOT_KEY）：', k); print('SHA-256（存進 hcp_config）：', hashlib.sha256(k.encode()).hexdigest())"
```

把 SHA-256 存進資料庫（SQL Editor 或 MCP 的 `execute_sql`）：

```sql
insert into public.hcp_config (key, value) values ('bot_key_sha256', '<上面印出的 SHA-256>')
on conflict (key) do update set value = excluded.value;
```

要換金鑰時重新產生一組、更新這一列、再改 Bot 的設定（環境變數或 panel.env）即可；舊金鑰立刻失效。

確認可以用：

```sh
curl -s -H "x-bot-key: $HEALER_PANEL_BOT_KEY" \
  https://<專案代號>.supabase.co/functions/v1/panel-api/bot/queue
# → []
```

## 測試

```sh
cd panel/supabase/functions/panel-api
deno test handler_test.ts           # 不需要網路與資料庫
deno check index.ts handler.ts      # 第一次會下載 supabase-js 型別
deno lint . && deno fmt --check --line-width 120 .
```

測試涵蓋授權、token／商品 id 錯誤與過期、新增商品與 sku
驗證、照片與欄位、一次送出多個商品（全部通過、其中一個缺欄位全部不送、冪等、webhook 只發一次）、claim 的原子性與
force、result、挑句與定稿、修改後的最終文案、結束專案（confirm、冪等、webhook、之後寫入
409、唯讀總覽）、匯出、reopen、queue、刪除專案，並在每一個回應檢查不會出現 Bot 金鑰、它的雜湊或 webhook 金鑰。

migration 的語法用 PGlite（嵌入式 Postgres，配一個假的 `storage` schema）驗證過：001 → 002 → 002 → 001 → 002
連跑都沒錯，並檢查了 unique、外鍵 cascade、status 檢查、照片函式、函式權限、條件 UPDATE 的 claim 只會成功一次。
