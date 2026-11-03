# Healer 商品文案

讓 AI 看商品照片，用 Healer 官網的語氣一次寫出三種版本，再到「文案剪接台」挑句子、改字，組成最後的文案。這是 2026-10-29 第五輪試寫（pilot-v5）用的方法，從 `training/pilot-v5` 原樣搬過來。

> **想用自己的 Grok Bot 部署一套（手機面板＋後端）？照 [`DEPLOY.md`](DEPLOY.md) 一步步做**（Supabase 免費版＋Vercel Hobby，0 元）。
> `data/training.json` 隨 repo 附上 Healer 的基底偏好（♡ 句子、不喜歡的句子、定稿範例、文字偏好 `style_notes`）。接手的 Bot 直接繼承，之後每一期照常 `sync` 與 `note add` 累積在自己的 clone 裡；若想從零開始，可以刪掉 `data/training.json` 或改成空紀錄（`{"hearts":[],"banned":[],"finals":[],"disliked":[],"style_notes":[]}`）。個人／團隊資料請不要放進去：只放句子與偏好，不放電子郵件、電話、金鑰、真實專案 token。

## 三個步驟

```sh
cd healer-copywriting   # 這個 repo 的目錄

# 1. 準備（舊模式，用商品編號重跑官網既有商品，需要 archive/ 照片庫；新品請看下一段的 --new）：每件商品一份「寫作任務」，含寫作規則、3 篇同類商品的真實文案、開頭參考、照片
python3 scripts/prepare.py 2507100017 2609070031 --output work/2026-11-03/packets

# 2. 寫：每件商品各開一個乾淨的 Claude 對話寫三個版本（需要 Claude Code，一件約 US$0.35）
scripts/run.sh work/2026-11-03/packets work/2026-11-03/outputs

# 3. 剪：產生文案剪接台，用瀏覽器打開
python3 scripts/build_editor.py work/2026-11-03/packets work/2026-11-03/outputs --output work/2026-11-03/editor.html
open work/2026-11-03/editor.html
```

還沒上架的新商品，在第 1 步改用 `--new`，另外準備一個描述檔（照片放在同一層的資料夾）：

```json
{"id": "new-001", "name": "HEALER Made 商品全名", "category": "長褲",
 "colors": ["深藍", "淺藍"], "material": "✦材質成份：棉98%+彈性纖維2%", "photos": "photos",
 "background": "去年賣到斷貨，這次補貨改成高腰",
 "feelings": "小編 160 公分穿 M，長度剛好到腳踝；布料摸起來滑滑的"}
```

```sh
python3 scripts/prepare.py --new 新品/new-product.json --output work/2026-11-05/packets
```

類別要用官網既有的名稱（長褲、洋裝、鞋子……），打錯時程式會列出可用的名稱。

`background`（背景資訊：補貨、改版、熱銷……）和 `feelings`（自身感受：試穿心得、身高尺寸、觸感……）是選填的，也可以用中文鍵名 `背景資訊`、`自身感受`（同一欄只填一種）。有填的內容會放進寫作任務的「小編提供的資訊」，AI 可以直接用小編口吻寫進文案、不加方括號，但只能用你實際寫出的內容，不會延伸或誇大；其他照片證明不了的部分照舊加方括號。兩欄都沒填，寫作任務跟以前完全一樣。

## 三種版本

- **A 心動開場**：小編口吻，先講最讓人心動的外觀，可以推坑收尾。
- **B 賣點直擊**：第一行就講最大的功能賣點，語氣務實。
- **C 穿搭情境**：從季節、場合、想穿的人開場，講怎麼搭。

照片證明不了的句子（穿起來舒不舒服、小編心得、熱銷故事）會加 `[方括號]`，剪接台會用紅框標出來，要人確認或改寫後再拿掉括號。描述檔裡你自己填的背景資訊、自身感受例外，可以不加括號。材質行會自動接在最後。

## 訓練紀錄

「訓練」不是重新訓練模型，而是把團隊的喜好寫進每次的寫作任務。紀錄存在 `data/training.json`，兩人共用一份。

`data/training.json` 隨 repo 附上 Healer 的基底偏好（♡ 句子、不喜歡的句子、定稿範例、文字偏好 `style_notes`）。接手的 Bot 直接繼承，之後每一期照常 `sync` 與 `note add` 累積在自己的 clone 裡；若想從零開始，可以刪掉 `data/training.json` 或改成空紀錄（`{"hearts":[],"banned":[],"finals":[],"disliked":[],"style_notes":[]}`）。個人／團隊資料請不要放進去：只放句子與偏好，不放電子郵件、電話、金鑰、真實專案 token。沒加 `--training` 時用 `data/training.json`；檔案不存在＝空紀錄，第一次寫入時才建立。要備份就複製這個檔案。格式：

```json
{"hearts": [{"text": "句子", "route": "A", "product_id": "new-001", "added": "2026-11-03"}],
 "banned": ["超級"],
 "finals": [{"product_id": "new-001", "name": "商品名稱", "text": "定稿全文", "saved": "2026-11-03"}]}
```

用 `scripts/training.py` 寫入（Bot 也是用這支）：

```sh
python3 scripts/training.py heart "這件真的會讓人一穿就愛上" --route A --product new-001   # ♡ 一句（路線、商品可省略）
python3 scripts/training.py ban 超級            # 禁用詞
python3 scripts/training.py unban 超級          # 取消禁用
python3 scripts/training.py final --product new-001 --name "商品名稱" --file 定稿.txt   # 存定稿
python3 scripts/training.py show                # 看目前的紀錄
```

相同的 ♡ 句子、禁用詞不會重複加入；每次寫入都是先寫暫存檔再換掉原檔，寫到一半中斷也不會弄壞紀錄。

`prepare.py` 產生寫作任務時，會在寫作規則後面加一段「團隊喜好（訓練紀錄）」：最近 40 句 ♡ 句子、所有禁用詞、最近 3 篇定稿（數量在 `prepare.py` 的 `HEARTS_SHOWN`、`FINALS_SHOWN`）。這些只決定口氣與用詞，**不能推翻「照片證明不了的句子要加方括號」**，♡ 句子也不會被整句照抄。紀錄是空的（或檔案不存在）時，寫作任務跟以前完全一樣。要用別的紀錄檔，兩支程式都可以加 `--training 路徑`。

### 不喜歡的句子、尺寸與價格

- 紀錄多了 `disliked`（按 ✕ 不喜歡的句子，格式同 `hearts`）。舊檔沒有這個鍵也能讀，下次寫入時會自動補上。
  ```sh
  python3 scripts/training.py dislike "好看到不行" --route A --product new-001   # 路線、商品可省略
  python3 scripts/training.py undislike "好看到不行"
  ```
  寫作任務的「團隊喜好」會多一段「不喜歡的句子（不要寫出相同或很像的句子與說法）」，最近 40 句（`prepare.py` 的 `DISLIKED_SHOWN`）。
- `--new` 描述檔可以多填選填的 `sizes`（尺寸）、`price`（價格），有填才在「# 新商品」加「尺寸：」「價格：」兩行。
- 紀錄全空、沒填尺寸價格時，寫作任務跟以前逐字相同（`python3 -m unittest scripts/test_training_ext.py`）。

### 修改後文案與文字偏好（v2）

- `finals` 每篇多了選填的 `edited_text`：使用者在面板「我修改後的最終文案」貼上的、實際使用的版本（由 `panel_session.py sync` 寫入；同一商品再同步只會更新，不會多一篇）。
- 新增 `style_notes`（文字偏好）：從使用者修改後的文案歸納出的短句規則。舊檔沒有這個鍵也能讀，下次寫入時會自動補上。
  ```json
  "style_notes": [{"note": "句子短一點，一行只講一件事", "source": "專案 abcd1234，3 件商品的修改", "added": "2026-11-20"}]
  ```
  ```sh
  python3 scripts/training.py note add "句子短一點，一行只講一件事" --source "專案 abcd1234，3 件商品的修改"   # 可加 --date YYYY-MM-DD
  python3 scripts/training.py note list          # 看編號
  python3 scripts/training.py note remove 3      # 刪第 3 條
  python3 scripts/training.py final --product HL-001 --name 商品名稱 --file 定稿.txt --edited-file 修改後.txt
  ```
  規則與來源各 ≤80 字；完全相同的規則不重複加入；最多 30 條（`training.py` 的 `STYLE_NOTES_MAX`），滿了要先刪舊的。
- 寫作任務的「團隊喜好」會多一段「## 文字偏好（從使用者修改後的文案歸納，最近 N 條）」，最多 12 條（`prepare.py` 的 `STYLE_NOTES_SHOWN`），並寫明這些只調整口氣、句型、用字習慣，**不能推翻**方括號規則、官方色名、背景資訊／自身感受的使用限制、行數與字數規則。
- 「團隊最近的定稿」若該篇有 `edited_text`，顯示修改後的版本並註明「（使用者實際使用的修改後版本）」。
- 沒有文字偏好、也沒有 `edited_text` 時，寫作任務跟以前逐字相同（回歸測試）。

## 手機面板（v2：專案＋多個商品）

讓同事在手機上傳照片、填欄位、挑句子，不用碰電腦。前端是 `panel/public/`（純靜態檔，部署在 Vercel），後端是 Supabase Edge Function `panel-api`（`panel/supabase/`）。規格見 v2 規格書與 `panel/supabase/README.md`。

**部署自己的一套：照 [`DEPLOY.md`](DEPLOY.md)。** 前端要連的後端網址不寫在 repo 裡：部署時設定環境變數 `PANEL_API_URL`，由 `panel/build.mjs` 產生 `panel/public/config.js`（不 commit，範本是 `config.example.js`）。

- **一期一個專案連結** `<面板網址>/p/<token>`，一直有效，直到使用者按「結束這期專案」。結束後連結變成唯讀總覽頁（每個商品的定稿／修改後文案，可複製），保留 180 天。
- 專案裡可以新增很多**商品**（每個有商品編號 sku），各自上傳照片、填欄位，可以勾選 2 個以上一次送出。
- 商品狀態：草稿 → 已送出 → 撰寫中 → 可挑句 → 已定稿。定稿後多一塊「我修改後的最終文案」，貼上實際使用的版本。
- 舊版 `/s/<token>` 連結已停用。

### Bot 工作流程

1. **建立專案連結**：使用者說要開新一期 → `panel_session.py create --title "11 月第 1 期"`，把印出的 `link` 傳給使用者。整期都用這個連結。
2. 使用者在手機新增商品、上傳照片、填欄位，勾選後按「送出選取的商品」。
3. **webhook 觸發**：後端發 `panel.products.submitted` 給 Bot（body 有 `project_token`、`product_ids`、`skus`、`product_names`）。沒設 webhook 時，Bot 有空就跑 `queue` 自己查。
4. **領取**：`queue` 看有哪些待處理；有空閒就 `pull-all` 一次全部領走（每個商品放 `runs/<日期>-batch/<sku>/`），或 `pull PROJECT PRODUCT` 只領一個。領取是原子的：兩個流程同時領，只有一個會成功。
5. 每個商品：`prepare.py --new <資料夾>/input/new-product.json --output <資料夾>/packet` → **Grok 讀 `packet/<sku>-S/task.md` 和照片，自己寫 A／B／C 三版**到 `<資料夾>/output.md`（格式：`【目標商品】`、`【路線A】`、`【路線B】`、`【路線C】`，一行一句）→ `push PROJECT PRODUCT --file <資料夾>/output.md`。
6. 使用者在面板挑句子、定稿、貼修改後文案。Bot 可以隨時 `sync PROJECT` 把 ♡、不喜歡、禁用詞、定稿、修改後文案寫進訓練紀錄（重複執行不會重複加入；使用者之後再改，重跑會更新）。
7. 使用者按「**結束這期專案**」→ 後端發 webhook `panel.project.closed`（body 有 `project_token`、全部 `product_ids`、`skus`、`closed_at`）。
8. Bot 收到後：`sync PROJECT`（最後一次同步）→ `export PROJECT`（寫出 `runs/<日期>-<專案前8碼>/export.json` 與 `export.md`）。
9. **Grok 讀 `export.md` 歸納文字偏好**，每條用 `training.py note add "規則" --source "專案 <前8碼>，<幾件商品>"` 寫入。下一期的寫作任務就會帶入，越寫越好。

### 歸納文字偏好的準則（給 Grok）

讀 `export.md` 時，重點看「定稿 → 修改後最終文案」的差異（保留／改寫／刪除／新增）和「草稿」裡的改字，找出**使用者一再做的修改**：

- 只寫**跨商品通用**的口氣、句型、用字規則，例如「句尾少用驚嘆號」「開頭直接講穿上的畫面，不先講季節」「『超』『很』等程度副詞改成具體描述」。
- 用**短句**，一條只講一件事，單條 **≤80 字**。
- **不含商品事實**：不寫顏色、材質、尺寸、價格、商品名稱或某件商品的特點。
- **不得與方括號規則衝突**：不能寫「試穿感受不用加括號」之類的規則；也不能違反官方色名、背景資訊／自身感受的使用限制、行數與字數規則。
- 只有一件商品出現、看不出是習慣的修改，先不要寫。已經有的規則不要換句話再寫一次（先 `note list` 看）；跟舊規則矛盾時，用 `note remove N` 刪掉舊的再加新的。滿 30 條時先刪過時的。

### 環境變數（Bot 的雲端電腦）

| 變數 | 內容 |
| --- | --- |
| `HEALER_PANEL_API` | `https://<專案代號>.supabase.co/functions/v1/panel-api` |
| `HEALER_PANEL_BOT_KEY` | Bot 金鑰（後端只存它的 SHA-256） |
| `HEALER_PANEL_URL` | 面板網址（Vercel），連結會是 `<網址>/p/<token>` |

環境變數沒設的，會從設定檔補：`$HEALER_PANEL_ENV` 指定的檔案，否則 `~/.config/healer-panel/panel.env`（每行 `KEY=VALUE`，可用 `export ` 前綴、引號、`#` 註解；只讀這三個鍵；權限不是 600 會印警告）。環境變數優先。補完還缺任何一個，程式會說缺哪個並結束（exit 2）。

### Webhook 設定

```sh
python3 scripts/panel_session.py config-webhook --url https://<Bot 收通知的網址> --key <自訂的長隨機字串>
python3 scripts/panel_session.py config-webhook --show     # 看目前網址、有沒有設金鑰（不會顯示金鑰）
python3 scripts/panel_session.py config-webhook --clear    # 取消
```

後端會 `POST` 到該網址，header `authorization: Bearer <key>`，Bot 端要檢查這個值。兩種事件：

- `panel.products.submitted`：使用者送出商品（一次送出多個只發一次，只含這次新送出的）→ Bot 跑 `pull-all`（或 `pull`）。
- `panel.project.closed`：使用者（或 Bot 的 `close`）結束專案 → Bot 跑 `sync` → `export` → 歸納文字偏好。

通知失敗不影響使用者送出；結果記在 `show` 的通知欄位。沒有 webhook 也能用：定時跑 `queue` 與 `projects --status closed`。

### CLI：`scripts/panel_session.py`

`PROJECT` 可以是完整 token 或前 8 碼；`PRODUCT` 可以是商品編號（sku）、uuid 或 uuid 前 8 碼。

```sh
python3 scripts/panel_session.py create [--title 文字]                # 建立專案，印出 {"token","link"}
python3 scripts/panel_session.py projects [--status open|closed]     # 專案清單與各狀態數量（--json 給程式讀）
python3 scripts/panel_session.py show PROJECT                        # 專案內所有商品與狀態
python3 scripts/panel_session.py queue [--project PROJECT] [--status submitted,generating] [--json]
python3 scripts/panel_session.py pull PROJECT PRODUCT [--out DIR] [--force]
python3 scripts/panel_session.py pull-all [--project PROJECT] [--out DIR]
python3 scripts/panel_session.py push PROJECT PRODUCT --file output.md [--summary 文字]
python3 scripts/panel_session.py sync PROJECT [PRODUCT]              # 省略 PRODUCT＝全部可挑句／已定稿的商品
python3 scripts/panel_session.py export PROJECT [--out FILE.json] [--md FILE.md]
python3 scripts/panel_session.py close PROJECT --yes                 # 以 Bot 身分結束專案（也會發 webhook）
python3 scripts/panel_session.py reopen PROJECT                      # 測試／補救用
python3 scripts/panel_session.py config-webhook (--url URL --key KEY | --show | --clear)
python3 scripts/panel_session.py delete PROJECT                      # 刪整個專案、全部商品與照片
```

- `pull`：先原子領取（已送出 → 撰寫中），成功才下載照片到 `DIR/input/photos/01.jpg…` 並寫 `DIR/input/new-product.json`（`id` = 商品編號）。預設 `DIR` = `runs/<台北日期>-<專案前8碼>/<sku>`。**已被別的流程領走時 exit 3**、不下載，訊息會寫領取時間；確定要重做（例如上次當機）才加 `--force`。商品是草稿、可挑句或已定稿時也 exit 3。
- `pull-all`：把 `queue` 裡所有「已送出」的商品依序領取，領不到的略過；最後列出每個商品的資料夾與下一步指令。
- `push`：每段的每一行當一句，`[方括號]` 句在面板上標「⚠ 需確認」；跟訓練紀錄裡「不喜歡」完全相同的句子、含禁用詞的句子會被濾掉（列在畫面上）。重新 push 會清掉使用者原本的挑選。
- `sync`：訓練紀錄的 `product_id` 用商品編號（sku）。
- `export.md`：每個商品列出 AI 產生版（三路線全部句子）、使用者挑選的草稿（標出改字）、定稿、使用者修改後的最終文案，以及定稿與修改後文案的逐行差異（`=` 保留、`~` 改寫、`-` 刪除、`+` 新增）。
- 錯誤一律中文說明，exit 1；環境變數缺少或參數錯誤 exit 2；沒領到商品 exit 3。

### 本機測試

```sh
python3 -m unittest discover -s scripts -p "test_*.py"   # CLI（內建記憶體版假 v2 API）、訓練紀錄、寫作任務回歸
deno run -A panel/dev/mock-server.ts            # 記憶體版假後端 + 面板，http://localhost:8787/（Bot 金鑰 dev-bot-key）
python3 panel/dev/e2e.py                         # Playwright 手機尺寸端對端測試，截圖存到 /workspace/panel-shots/
python3 -m unittest panel/dev/test_build.py      # panel/build.mjs（產生 config.js）的測試，需要 node
```

寫作任務的逐字回歸測試需要先產生基準（用修改前的 `prepare.py`）：`/tmp/reg/setup_baseline.py`、`/tmp/reg2/setup_baseline.py`；沒有基準時那兩個測試會略過。

## 資料夾內容

- `rules/` 寫作規則和「Healer 真實文案統計」。想調整 AI 的寫法就改這裡。
- `scripts/` 上面三步用到的程式，以及剪接台的網頁樣板。
- `data/` 商品目錄、商品類別、可當範例的 150 件商品清單、照片指紋快取、訓練紀錄 `training.json`（隨 repo 附上的 Healer 基底偏好，接手後繼續累積）。
- `panel/` 手機面板：`public/` 前端、`build.mjs`（產生 `config.js`）、`vercel.json`、`dev/` 假後端與測試、`supabase/` 後端。
- `DEPLOY.md` 給 Grok Bot 照著做的部署步驟。
- `pilot-v5-結果/` 第五輪 8 件商品的成果：`editor.html`（原本那份剪接台）、AI 原始輸出、當時的寫作任務。

## 要注意

- **不需要照片庫 `archive/`。** 新商品用 `--new`，照片只用使用者這次上傳的那幾張；選範例時比對的是 `data/fingerprints.json` 裡的快取指紋，程式不會讀、也不會寫任何 archive 照片或快取。只有舊的「用商品編號重跑官網既有商品」模式（第 1 步第一種寫法）才需要 `archive/`，可用 `--healer-root` 指定位置；沒有 archive 時它會直接說明，請改用 `--new`。
- 選範例時會避開跟目標商品共用照片的商品，免得 AI 抄到幾乎同一件商品的文案。原本那份清單已經遺失，現在用 `data/fingerprints.json` 的快取指紋比對（不讀原圖）。用 8 件試寫商品驗證過，選出的範例和寫作任務跟當初逐字相同。
- 剪接台的草稿只存在你這台電腦的瀏覽器裡，換電腦或清除瀏覽器資料就沒了。剪完請按「複製文案」貼到別處保存。
- 範例清單刻意排除了正式考卷的 20 件商品。拿那 20 件來寫，等於讓 AI 看過答案，評分就不準了。
