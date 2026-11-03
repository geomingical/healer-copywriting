# 部署指南（給 Grok Bot）

這份文件是寫給**你（Grok Bot）**照著一步一步做的。你會在自己的雲端電腦上部署一套「Healer 商品文案」：手機面板（Vercel）＋後端（Supabase）＋你自己的寫作流程。過程中有些事只有**擁有者（人類）**能做，會用「👤 **請擁有者…**」標出來。

**跟擁有者要東西的規則（每一步都適用）：**

- **金鑰、token、密碼**（Vercel token、Supabase access token、資料庫連線字串、webhook key……）：一律用聊天的 **secret 表單／秘密卡片**收。**不要**叫擁有者貼在一般聊天訊息裡；你拿到之後也**不要**印出來、不要寫進任何會 commit 的檔案、不要寫進回報。
- **網址**（Supabase 專案網址、Vercel 網址、routine 的 Webhook URL）不是秘密，可以直接貼在聊天。
- 每完成一節，用一兩句話跟擁有者回報做到哪裡、下一步要他做什麼。

指令裡的 `<…>` 是要換成實際值的地方；其他部分可以直接複製執行。

---

## 0. 這是什麼、需要什麼

- **做什麼**：擁有者（和同事）在手機面板上傳新品照片、填欄位、按送出 → 後端通知你 → 你讀照片、依 `rules/` 寫 A／B／C 三版文案、上傳 → 擁有者在手機挑句子定稿 → 專案結束後你學他們的文字偏好，下一期寫得更貼近。
- **帳號**（都用免費方案）：
  - **Supabase 免費版**：放資料庫、照片、後端函式。免費版最多 2 個專案。
  - **Vercel Hobby**：放手機面板（純靜態網頁）。
  - **你（Grok Bot）的 routines／Automation 功能**：要能建立「用 Webhook 觸發」的 routine。
- **預估費用**：0 元（都在免費額度內；照片很多時注意 Supabase 免費版的儲存空間上限）。
- **起始偏好**：repo 附有 Healer 的基底偏好（`data/training.json`），第一次產生的 `task.md` 就會帶入。
- **你的雲端電腦需要的工具**：`python3`（3.10 以上）、`node`（18 以上，含 `npx`）、`git`、`curl`；`deno` 選用（只有跑後端測試才需要）。

先確認工具：

```sh
python3 --version && node --version && npx --version && git --version && curl --version | head -1
```

👤 **請擁有者確認以下清單**（用一則訊息問完，等回覆再繼續）：

1. 有沒有 Supabase 帳號？免費版的 2 個專案是否已經用滿？
2. 有沒有 Vercel 帳號？要用 Vercel token（用 secret 表單給你）還是已經連接了 Vercel connector／MCP？
3. 有沒有 Supabase 的連接方式：已連接的 Supabase connector／MCP，或願意提供 Supabase personal access token（secret 表單），或自己到 Supabase 後台 SQL Editor 貼 SQL？
4. 面板的 Vercel 專案要叫什麼名字（例如 `healer-panel-<擁有者名字>`）？
5. 同意你用 routines 功能建立一個 webhook 觸發的 routine 嗎？

---

## 1. 取得程式、確認環境

```sh
git clone https://github.com/geomingical/healer-copywriting.git
cd healer-copywriting
python3 -m pip install --user Pillow
python3 -m unittest discover -s scripts -p "test_*.py"
```

最後一行要看到 `OK`（有些回歸測試沒有基準檔時會顯示 skipped，那是正常的）。之後所有指令都在 repo 根目錄執行，除非另外寫明。

選用：

```sh
python3 -m unittest panel/dev/test_build.py                                   # 需要 node
(cd panel/supabase/functions/panel-api && deno test handler_test.ts)          # 需要 deno
```

`data/training.json` 隨 repo 附上 Healer 的基底偏好（♡ 句子、不喜歡的句子、定稿範例、文字偏好 `style_notes`）。接手的 Bot 直接繼承，之後每一期照常 `sync` 與 `note add` 累積在自己的 clone 裡；若想從零開始，可以刪掉 `data/training.json` 或改成空紀錄（`{"hearts":[],"banned":[],"finals":[],"disliked":[],"style_notes":[]}`）。個人／團隊資料請不要放進去：只放句子與偏好，不放電子郵件、電話、金鑰、真實專案 token。

---

## 2. 建立 Supabase 專案

👤 **請擁有者**到 <https://supabase.com/dashboard> 建立一個新專案（Free plan，區域選離使用者近的，例如 Northeast Asia (Tokyo)），資料庫密碼自己保管、不用給你。建好後把**專案網址**（`https://<專案代號>.supabase.co`）或 **Project ref**（Settings → General）貼在聊天。

若你有 Supabase MCP，也可以經擁有者同意後由你建立（`create_project`），但仍要擁有者確認組織與費用（必須是 Free）。

**免費版 2 個專案已滿時**：可以沿用一個既有專案。這套東西建立的所有物件都以 `hcp_` 為前綴（`hcp_config`、`hcp_projects`、`hcp_products`、`hcp_photo_add`、`hcp_photo_remove`，以及會被刪掉的 `hcp_sessions`），照片放在私有 bucket `healer-panel`，Edge Function 叫 `panel-api`，不會碰其他表。風險：

- 如果那個專案剛好已經有同名的 `hcp_*` 表、`healer-panel` bucket 或 `panel-api` 函式，會被沿用或覆蓋。
- 會共用該專案的免費額度（資料庫、儲存空間、函式呼叫次數）。

👤 **這個選項一定要先問擁有者同意**，並請他確認那個專案沒有上述同名物件。

記下專案代號，之後的指令都用這兩個變數：

```sh
export REF=<專案代號>
export HEALER_PANEL_API=https://$REF.supabase.co/functions/v1/panel-api
```

---

## 3. 建立資料表與照片 bucket（migration）

**依序**執行兩個檔案，**先 001 再 002**：

1. `panel/supabase/migrations/001_init.sql`：建立 `hcp_config` 與私有 bucket `healer-panel`（也會建立 v1 的 `hcp_sessions` 表）。
2. `panel/supabase/migrations/002_projects.sql`：建立 `hcp_projects`、`hcp_products` 與照片函式，並刪掉 001 建的 `hcp_sessions`（全新部署的空表，刪掉無害）。

兩個檔都可以重複執行。三種做法擇一：

- **A. Supabase MCP**：`apply_migration`，`project_id` = `<專案代號>`，名稱分別用 `hcp_panel_init`、`hcp_panel_projects`，`query` 是檔案全文（用你讀到的檔案內容，不要自己改寫）。
- **B. 擁有者在 SQL Editor 貼上執行**：👤 **請擁有者**打開 Supabase 後台 → SQL Editor，先貼 `001_init.sql` 全文按 Run，再貼 `002_projects.sql` 全文按 Run。你可以把兩個檔案內容分兩則訊息給他（SQL 裡沒有秘密）。
- **C. psql**（需要資料庫連線字串，屬於秘密）：👤 **請擁有者**用 secret 表單提供連線字串（Settings → Database → Connection string），你存成環境變數 `SUPABASE_DB_URL`，然後：

  ```sh
  psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f panel/supabase/migrations/001_init.sql
  psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f panel/supabase/migrations/002_projects.sql
  ```

**確認**（MCP `execute_sql` 或 SQL Editor）：

```sql
select id, public from storage.buckets where id = 'healer-panel';
-- 應該一列：healer-panel | false
select table_name from information_schema.tables where table_schema = 'public' and table_name like 'hcp_%' order by 1;
-- 應該是 hcp_config、hcp_products、hcp_projects（沒有 hcp_sessions）
```

---

## 4. 部署 Edge Function `panel-api`

設定重點：名稱 `panel-api`、**`verify_jwt: false`**（面板與 Bot 都不帶 Supabase 登入憑證，授權由函式自己做）、入口 `index.ts`，只上傳 `index.ts` 與 `handler.ts`（**不要**上傳 `handler_test.ts`）。檔案在 `panel/supabase/functions/panel-api/`。

- **A. Supabase MCP**：`deploy_edge_function`，`name: "panel-api"`、`entrypoint_path: "index.ts"`、`verify_jwt: false`，`files` 放 `index.ts` 和 `handler.ts` 的全文。
- **B. Supabase CLI**：👤 **請擁有者**到 <https://supabase.com/dashboard/account/tokens> 建立一個 access token，用 secret 表單給你；你存成環境變數 `SUPABASE_ACCESS_TOKEN`（不要印出）。然後：

  ```sh
  cd panel
  npx supabase@latest functions deploy panel-api --no-verify-jwt --project-ref "$REF"
  cd ..
  ```

  如果出現需要 Docker 的錯誤，在指令最後加 `--use-api` 再試一次。CLI 可能會建立 `panel/supabase/.temp/`，它已列在 `.gitignore`。

**確認**：沒帶金鑰呼叫 Bot 端 API 要回 **401**：

```sh
curl -s -w '\nHTTP %{http_code}\n' "$HEALER_PANEL_API/bot/queue"
# → {"error":"unauthorized","message":"缺少 Bot 金鑰"}
#   HTTP 401
```

要同時看 HTTP 401 **和**這段中文 JSON（它是 `panel-api` 自己回的）。如果是 404，代表函式名稱或網址不對；如果也是 401 但內容不是上面那段（例如 Supabase 的 JWT／authorization header 錯誤訊息），代表 `verify_jwt` 沒關，重新部署並關掉。

---

## 5. 產生 Bot 金鑰

金鑰原文只放在你本機的設定檔，資料庫只存它的 SHA-256。下面這段會產生金鑰、**直接寫進** `~/.config/healer-panel/panel.env`（權限 600），畫面上**只印 SHA-256**，不印金鑰本身：

```sh
umask 077 && mkdir -p ~/.config/healer-panel && python3 - <<'EOF'
import hashlib, os, pathlib, secrets
path = pathlib.Path.home() / '.config/healer-panel/panel.env'
text = path.read_text(encoding='utf-8') if path.exists() else ''
if 'HEALER_PANEL_BOT_KEY=' in text:
    raise SystemExit('panel.env 已經有 HEALER_PANEL_BOT_KEY；要換金鑰請看第 13 節「金鑰外洩時」')
key = secrets.token_urlsafe(32)
with open(path, 'a', encoding='utf-8') as f:
    f.write(f'HEALER_PANEL_BOT_KEY={key}\n')
os.chmod(path, 0o600)
print('SHA-256（存進 hcp_config）：', hashlib.sha256(key.encode()).hexdigest())
EOF
```

把印出的 SHA-256 存進資料庫（MCP `execute_sql`，或 👤 **請擁有者**在 SQL Editor 執行；SHA-256 不是秘密，可以放在聊天裡給他）：

```sql
insert into public.hcp_config (key, value) values ('bot_key_sha256', '<上面印出的 SHA-256>')
on conflict (key) do update set value = excluded.value;
```

---

## 6. 完成 Bot 本機設定檔 `panel.env`

`scripts/panel_session.py` 會讀三個值：環境變數優先，沒設的從 `$HEALER_PANEL_ENV`（預設 `~/.config/healer-panel/panel.env`）補。格式是每行 `KEY=VALUE`（可以有 `export ` 前綴、引號、`#` 註解）；只會讀 `HEALER_PANEL_API`、`HEALER_PANEL_BOT_KEY`、`HEALER_PANEL_URL` 三個鍵。檔案權限不是 600 時會印警告。

第 5 步已經寫了 `HEALER_PANEL_BOT_KEY`，現在加上後端網址（面板網址要第 7 步之後才知道，先不寫）：

```sh
umask 077 && echo "HEALER_PANEL_API=https://$REF.supabase.co/functions/v1/panel-api" >> ~/.config/healer-panel/panel.env
chmod 600 ~/.config/healer-panel/panel.env
ls -l ~/.config/healer-panel/panel.env          # 應該是 -rw-------
sed 's/=.*/=…/' ~/.config/healer-panel/panel.env  # 只看有哪些鍵，不顯示值
```

確認金鑰能用（從設定檔讀進目前的 shell，不顯示內容）：

```sh
set -a && . ~/.config/healer-panel/panel.env && set +a
curl -s -o /dev/null -w '%{http_code}\n' -H "x-bot-key: $HEALER_PANEL_BOT_KEY" "$HEALER_PANEL_API/bot/queue"
# → 200
```

**不要**把 `panel.env` 放進 repo 目錄、不要 `cat` 它、不要把內容貼到聊天。

---

## 7. 部署手機面板到 Vercel（Hobby）

面板是 `panel/` 底下的靜態檔。它要連的後端網址不寫在 repo 裡：Vercel 建置時執行 `node build.mjs`（`panel/vercel.json` 的 `buildCommand`），依環境變數 `PANEL_API_URL` 產生 `public/config.js`。`PANEL_API_URL` 必須是 `https://…/functions/v1/panel-api`，否則建置會失敗並用中文說明原因。

**登入方式**（擇一）：

- 👤 **請擁有者**到 <https://vercel.com/account/tokens> 建立一個 token，用 secret 表單給你；你存成環境變數 `VERCEL_TOKEN`（不要印出）。下面指令用 `--token "$VERCEL_TOKEN"`。
- 或用已連接的 Vercel connector／MCP 部署 `panel/` 目錄，結果要跟下面的指令相同：Root Directory 是 `panel`、Build Command `node build.mjs`、Output Directory `public`、環境變數 `PANEL_API_URL`。

👤 **請擁有者決定專案名稱**（第 0 步問過，例如 `healer-panel-<擁有者名字>`）。

```sh
export VERCEL_PROJECT=<擁有者決定的名稱>
cd panel
npx vercel@latest link --yes --project "$VERCEL_PROJECT" --token "$VERCEL_TOKEN"
PANEL_API_URL="https://$REF.supabase.co/functions/v1/panel-api" npx vercel@latest deploy --prod --yes \
  --build-env PANEL_API_URL="https://$REF.supabase.co/functions/v1/panel-api" --token "$VERCEL_TOKEN"
cd ..
```

（擁有者的帳號底下有多個 team 時，`link` 與 `deploy` 都加 `--scope <team 代號>`。）也可以改成把 `PANEL_API_URL` 設成 Vercel 專案的環境變數（Production），之後每次部署都會用到：

```sh
cd panel
echo "https://$REF.supabase.co/functions/v1/panel-api" | npx vercel@latest env add PANEL_API_URL production --token "$VERCEL_TOKEN"
cd ..
```

部署會建立 `panel/.vercel/`（已列在 `.gitignore`，不要 commit）。

**面板網址**用專案的正式網域 `https://<專案名稱>.vercel.app`（部署輸出裡的「Aliased」那一個，或 Vercel 後台 → Project → Domains），**不要**用帶一串亂碼的單次部署網址。補進設定檔：

```sh
umask 077 && echo "HEALER_PANEL_URL=https://<專案名稱>.vercel.app" >> ~/.config/healer-panel/panel.env
```

**確認**：

```sh
export PANEL=https://<專案名稱>.vercel.app
curl -s -o /dev/null -w '%{http_code}\n' "$PANEL/"                 # → 200
curl -s "$PANEL/config.js"                                          # → 指向 https://<你的專案代號>.supabase.co/functions/v1/panel-api
curl -sI "$PANEL/" | grep -i -e content-security-policy -e x-frame-options -e x-robots-tag
```

最後一行要看到三個 header，CSP 裡有 `https://*.supabase.co`。

**Vercel Authentication（Deployment Protection）**：如果首頁回 401、或被轉到 `vercel.com/login`／`sso-api`，代表專案開了 Vercel Authentication，手機上的使用者會被擋住。👤 **請擁有者**到 Vercel 後台 → 這個專案 → Settings → Deployment Protection，把 **Vercel Authentication** 關掉（或確認正式網域 `<專案名稱>.vercel.app` 不受保護），存檔後你再跑一次上面的 `curl` 確認是 200。用手機無痕視窗打開首頁也是好的確認方式。

---

## 8. 驗證整條

```sh
python3 scripts/panel_session.py create --title "測試"
```

會印出 `{"token": "…", "link": "https://<專案名稱>.vercel.app/p/<token>"}`。接著（把 `<token>` 換成上面印出的值）：

```sh
curl -s -o /dev/null -w '%{http_code}\n' "https://<專案名稱>.vercel.app/p/<token>"   # → 200
curl -s -o /dev/null -w '%{http_code}\n' "$HEALER_PANEL_API/p/<token>"               # → 200
python3 scripts/panel_session.py queue                                               # → 沒有待處理的商品
python3 scripts/panel_session.py delete <token>                                      # 刪掉測試專案
```

選用：把連結傳給擁有者，請他用手機打開，確認看得到「測試」專案、可以新增商品（測完你再 `delete`）。

---

## 9. 建立 webhook routine

用**你的 routines 功能**建立一個「用 Webhook 觸發」的 routine。介面因平台而異，需要得到的結果是：

- 名稱：例如「Healer 面板通知」。
- 觸發方式：Webhook（平台會產生一個 **Webhook URL** 和一把 **Webhook key**，呼叫時要帶 `Authorization: Bearer <Webhook key>`，由平台驗證）。
- 執行環境：能在這台雲端電腦的 repo 目錄跑指令。
- 指示內容：下面這整段（把 `<repo 目錄>` 換成實際路徑）。

````text
你收到的是 Healer 文案面板後端的 webhook 通知。request body 是 JSON，用 event 欄位判斷要做什麼。
所有指令都在 <repo 目錄> 執行。面板設定已經在 ~/.config/healer-panel/panel.env，不要印出或回報裡面的內容。

【event = panel.products.submitted】（使用者在手機送出了商品）
1. python3 scripts/panel_session.py queue 看待處理的商品。
2. python3 scripts/panel_session.py pull-all 一次領取全部「已送出」的商品。被別的流程領走的（訊息寫「已被其他流程領走」，單獨 pull 時 exit 3）就略過，不要加 --force。
3. 對 pull-all 列出的每個商品資料夾 <資料夾>：
   a. python3 scripts/prepare.py --new <資料夾>/input/new-product.json --output <資料夾>/packet
   b. 讀 <資料夾>/packet/<商品編號>-S/task.md，並看同資料夾 photos/ 裡的每一張照片。
   c. 依 task.md（裡面已含 rules/ 的寫作規則、範例文案、團隊喜好）自己寫 A／B／C 三版，存成 <資料夾>/output.md，格式：
      【目標商品】一段話描述商品
      【路線A】
      一行一句
      【路線B】
      一行一句
      【路線C】
      一行一句
      照片證明不了的句子要用 [方括號] 包起來（task.md「小編提供的資訊」裡實際寫到的除外）。
   d. python3 scripts/panel_session.py push <專案前8碼> <商品編號> --file <資料夾>/output.md
4. 全部完成後通知擁有者：「<商品名稱>（<商品編號>）已可挑句」，一個商品一行，附上專案連結。
   有失敗的商品，說明是哪個、錯誤訊息是什麼。

【event = panel.project.closed】（使用者按了「結束這期專案」）
1. python3 scripts/panel_session.py sync <project_token>
2. python3 scripts/panel_session.py export <project_token>，記下印出的 export.md 路徑。
3. python3 scripts/training.py note list 看已經有的文字偏好。
4. 讀 export.md，依 README.md「歸納文字偏好的準則」找出使用者一再做的修改：只寫跨商品通用的口氣／句型／用字規則，
   一條一件事、≤80 字、不含商品事實、不得與方括號規則衝突；只出現一次的不寫；已有的不要換句話重寫；
   矛盾時先 note remove N 再加；滿 30 條先刪過時的。
5. 每條用 python3 scripts/training.py note add "規則" --source "專案 <前8碼>，<幾件商品>" 寫入。
6. 通知擁有者：這期學到了哪幾條（列出規則原文）；沒有新規則也要說。

【其他 event（例如 ping）】
只回報「收到 <event> 通知」，不要執行任何指令。
````

👤 **請擁有者**（或你建立後告訴他）：

- 把 routine 的 **Webhook URL** 貼在聊天（網址不是秘密）。
- **Webhook key** 用 **secret 卡片**提供給你。你讀取後存成環境變數 `WEBHOOK_KEY`（或平台的秘密儲存），**不寫進任何會 commit 的檔案、不印出、不放進回報**。

如果你的平台建立 routine 時直接把 URL 與 key 顯示給你，就不必請擁有者再提供，但同樣不要把 key 顯示在聊天。

---

## 10. 設定面板後端的 webhook

```sh
export WEBHOOK_URL=<routine 的 Webhook URL>
python3 scripts/panel_session.py config-webhook --url "$WEBHOOK_URL" --key "$WEBHOOK_KEY"
python3 scripts/panel_session.py config-webhook --show
```

`--show` 要看到 `"webhook_url": "<你的 Webhook URL>"`、`"webhook_key_set": true`（不會顯示金鑰）。金鑰一律用 `"$WEBHOOK_KEY"` 傳，不要把金鑰原文打在指令裡。

ping 探測（期望 HTTP 200，routine 會跑一次並只回報「收到 ping」）：

```sh
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$WEBHOOK_URL" \
  -H "Authorization: Bearer $WEBHOOK_KEY" -H "content-type: application/json" -d '{"event":"ping"}'
```

不是 200：401／403 代表 key 不對；404 代表 URL 不對。

要取消 webhook：`python3 scripts/panel_session.py config-webhook --clear`（之後改用 `queue` 輪詢）。

---

## 11. 事件說明

後端用 `POST` 發到 Webhook URL，header `content-type: application/json`、`authorization: Bearer <webhook key>`，逾時 5 秒；通知失敗不影響使用者的操作。完整規格見 [`panel/supabase/README.md`](panel/supabase/README.md)「webhook 通知內容」，Bot 流程見 [`README.md`](README.md)「Bot 工作流程」。

| 事件 | 什麼時候發 | body 欄位 | 你要做的事 |
| --- | --- | --- | --- |
| `panel.products.submitted` | 使用者勾選商品按「送出」（一次送出多個只發一次，只含這次新送出的） | `event`、`project_token`、`product_ids`（uuid 陣列）、`product_names`、`skus`、`submitted_at` | `queue` → `pull-all` → 每個商品 `prepare.py` → 寫 A／B／C → `push` → 通知擁有者可挑句 |
| `panel.project.closed` | 使用者按「結束這期專案」，或你執行 `close` | `event`、`project_token`、`product_ids`、`product_names`、`skus`（不含草稿商品）、`submitted_at`（＝`closed_at`）、`closed_at` | `sync` → `export` → 讀 `export.md` 歸納文字偏好 → `training.py note add` → 通知擁有者學到哪幾條 |
| 其他（例如 `ping`） | 你自己測試時 | — | 只回報收到 |

---

## 12. 日常使用

詳見 README「Bot 工作流程」，簡述：

1. **開新一期**：擁有者說要開新一期 → `python3 scripts/panel_session.py create --title "<第幾期>"`，把印出的 `link` 傳給他。整期都用這個連結。
2. **使用者送出商品** → 你收到 `panel.products.submitted` → 依第 9 節處理 → 通知可挑句。
3. **挑句、定稿**：擁有者在手機挑句子、定稿、貼「我修改後的最終文案」。期間可以隨時 `sync <專案>` 把 ♡、不喜歡、禁用詞、定稿寫進訓練紀錄（重跑不會重複）。
4. **結束專案**：擁有者按「結束這期專案」→ 你收到 `panel.project.closed` → `sync` → `export` → 歸納文字偏好。
5. **偏好學習**：`python3 scripts/training.py show` 看目前的紀錄；`note list`／`note remove N` 管理文字偏好。下一期的 `task.md` 會自動帶入。
6. 沒收到 webhook 時的備援：有空就跑 `python3 scripts/panel_session.py queue` 與 `projects --status closed`。

---

## 13. 隱私與安全

**絕對不要 commit／上傳到 GitHub**：

- `~/.config/healer-panel/panel.env`（Bot 金鑰；本來就不在 repo 目錄）
- Vercel token、Supabase access token、資料庫連線字串、Webhook key
- `panel/.vercel/`、`panel/supabase/.temp/`、任何 `.env`／`*.env`
- `panel/public/config.js`（由 build 產生；雖然只有公開網址，但每個部署不同）
- `runs/`（下載的商品照片、寫作任務、匯出檔）

以上都已列在 `.gitignore`。commit 前用 `git status` 確認沒有出現這些檔案。

**`data/training.json` 會被 commit**（它是隨 repo 附上的基底偏好）：commit 前看一下 diff，只能有句子與偏好，不能有電子郵件、電話、金鑰、真實專案 token 或其他個資。

**訓練紀錄 `data/training.json`**：`data/training.json` 隨 repo 附上 Healer 的基底偏好（♡ 句子、不喜歡的句子、定稿範例、文字偏好 `style_notes`）。接手的 Bot 直接繼承，之後每一期照常 `sync` 與 `note add` 累積在自己的 clone 裡；若想從零開始，可以刪掉 `data/training.json` 或改成空紀錄（`{"hearts":[],"banned":[],"finals":[],"disliked":[],"style_notes":[]}`）。個人／團隊資料請不要放進去：只放句子與偏好，不放電子郵件、電話、金鑰、真實專案 token。備份：

```sh
mkdir -p ~/healer-backup && cp data/training.json ~/healer-backup/training-$(date +%Y%m%d).json
```

（備份放在 repo 以外的地方；還原時把檔案複製回 `data/training.json`。）

**金鑰外洩時（Bot 金鑰）**：重新產生一組、寫回 `panel.env`、更新 `hcp_config`，舊金鑰立刻失效：

```sh
umask 077 && python3 - <<'EOF'
import hashlib, os, pathlib, re, secrets
path = pathlib.Path.home() / '.config/healer-panel/panel.env'
key = secrets.token_urlsafe(32)
lines = [l for l in path.read_text(encoding='utf-8').splitlines() if not re.match(r'\s*(export\s+)?HEALER_PANEL_BOT_KEY\s*=', l)]
path.write_text('\n'.join(lines + [f'HEALER_PANEL_BOT_KEY={key}']) + '\n', encoding='utf-8')
os.chmod(path, 0o600)
print('新的 SHA-256（更新 hcp_config）：', hashlib.sha256(key.encode()).hexdigest())
EOF
```

再執行第 5 節的 `insert … on conflict … do update` SQL（換成新的 SHA-256）。**Webhook key 外洩**：用 routines 功能重新產生 key（或重建 routine），再跑第 10 節的 `config-webhook`。Vercel／Supabase token 外洩：👤 **請擁有者**到各自後台撤銷（revoke）該 token。

**照片與保留期限**：照片存在私有 bucket `healer-panel`，只能透過有時效的簽名網址看（使用者端 1 小時、Bot 端 2 小時）。專案結束後連結保留 **180 天**（之後使用者端回 404，Bot 端仍讀得到）。**目前沒有自動清理**：照片和資料會一直留在 Supabase，直到你執行 `python3 scripts/panel_session.py delete <專案>`。建議專案結束、`export` 完成後，徵得擁有者同意再刪掉舊專案，避免超過免費版儲存空間。

---

## 14. 疑難排解

| 狀況 | 原因與處理 |
| --- | --- |
| `panel_session.py` 說「缺少環境變數」（exit 2） | `panel.env` 少了那幾個鍵，或 `HEALER_PANEL_ENV` 指到別的檔。用第 6 節的 `sed` 指令看有哪些鍵。 |
| 印出「警告：設定檔 … 的權限是 644」 | `chmod 600 ~/.config/healer-panel/panel.env` |
| 401「Bot 金鑰不對」 | `panel.env` 的金鑰與 `hcp_config` 的 SHA-256 不符，或還沒寫 `bot_key_sha256`。重做第 5 節（或第 13 節換金鑰）。 |
| 打 `/bot/queue` 回的 401 不是 `{"error":"unauthorized",…}`，而是 Supabase 的 JWT 錯誤 | 函式部署時沒關 `verify_jwt`。用 `verify_jwt: false`／`--no-verify-jwt` 重新部署。 |
| 409 `project_closed` | 專案已結束，不能再寫入。要補救用 `python3 scripts/panel_session.py reopen <專案>`，改完再請擁有者結束。 |
| `pull` exit 3 | 商品已被別的流程領走、還是草稿、或已經可挑句。webhook 流程裡直接略過；確定上次中斷要重做才加 `--force`。 |
| 面板照片不顯示、或上傳失敗，瀏覽器 console 有 CSP 錯誤 | `panel/vercel.json` 的 CSP 只允許 `https://*.supabase.co`。如果後端用了自訂網域，要把該網域加進 `img-src` 和 `connect-src` 後重新部署。 |
| 面板顯示「面板設定有誤（config.js 沒有 API 網址）」 | 部署時沒帶 `PANEL_API_URL`，或用了舊的部署。`curl <面板網址>/config.js` 檢查，重做第 7 節。 |
| Vercel 建置失敗，訊息「沒有設定後端網址」或「路徑必須以 /functions/v1/panel-api 結尾」 | `PANEL_API_URL` 沒傳或格式錯。用 `--build-env PANEL_API_URL=…` 或專案環境變數。 |
| 手機打開面板要求登入 Vercel | 開了 Vercel Authentication（Deployment Protection），或用了單次部署網址。見第 7 節最後一段。 |
| 第一次呼叫很慢（1–3 秒） | Edge Function 冷啟動，正常。之後就快了。 |
| 好幾天沒用後連不上後端 | Supabase 免費版長時間沒有使用可能會暫停專案。👤 **請擁有者**到 Supabase 後台按 Restore／Resume。 |
| 使用者送出了但 routine 沒有跑 | `python3 scripts/panel_session.py show <專案> --json` 看每個商品的 `notify_status`：`unset` = 沒設 webhook（做第 10 節）；`failed:401`／`failed:403` = key 不對；`failed:404` = URL 不對；`failed:timeout`／`failed:network` = 平台沒回應。`config-webhook --show` 確認設定，用第 10 節的 ping 測試。在修好之前，用 `queue`＋`pull-all` 手動處理（備援輪詢）。 |
| 結束專案後沒收到 `panel.project.closed` | `show <專案>` 看「結束通知」（或 `--json` 的 `close_notify_status`），處理方式同上；手動跑 `sync` → `export` → 歸納。 |
