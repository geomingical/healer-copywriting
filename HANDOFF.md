# 交接：接下來要做什麼（給在 Grok Bot 雲端電腦接手的 Claude Code）

最後更新：2026-11-03。先讀 `README.md` 了解現有三步驟流程，再讀這份。

## 目標

同事（使用者本人 + 一位同事）在 Grok Bot 手機 app 丟新品照片，補幾句「背景資訊」（補貨、改版、熱銷）和「自身感受」（試穿心得、身高尺寸、觸感），Bot 在雲端電腦跑這個 repo 的程式寫出 A／B／C 三版文案，同事再直覺地挑句子組成定稿。操作要「無腦」。主要寫**還沒上架的新品**。

## 已經決定的事

1. **寫作引擎只留一套：`scripts/` 的 Claude CLI 流程。** `app/` 裡的 claude.ai 網頁版（copy-studio）有自己一套寫作邏輯，Bot 叫不動它，**不要再往裡面加功能**，只當介面參考（它的面板操作：點句子、♡、反白設禁用詞、存定稿、訓練紀錄區）。
2. **「訓練」= 把團隊喜好寫進每次的寫作指示，不重新訓練模型。** 帶入：最近 40 句 ♡ 句子、所有禁用詞、最近 3 篇定稿。之前的微調實驗沒有比原模型好。
3. **訓練紀錄兩人共用一份。**
4. **♡ 句子和定稿只決定口氣與用詞，不能推翻「照片證明不了的句子要加 [方括號]」這條規則。**（網頁版目前寫的是「訓練紀錄優先於一般規則」，搬過來時要改成這個版本。）
5. 使用者填的背景資訊、自身感受可以直接用小編口吻寫進文案，不用加方括號。
6. **文字偏好（`style_notes`）由 Grok 在專案結束後讀 `export.md` 歸納**，只寫跨商品通用的口氣／句型／用字規則（短句、≤80 字、不含商品事實、不得與方括號規則衝突），用 `training.py note add` 寫入。準則見 README「歸納文字偏好的準則」。

## 待辦（依順序）

1. ✅ **已完成（2026-11-03，尚未 commit）：新品流程完全不依賴照片庫 `archive/`。** 使用者每次都會上傳新照片，所以 `archive/` 不需要（也不會放進 repo）。
   - `scripts/prepare.py --new` 只讀使用者上傳的照片；範例商品用 `data/fingerprints.json` 的快取指紋（以 sha256 為鍵，涵蓋全部 2492 張範例照片）比對，程式不再讀原圖、也不寫回快取。上傳資料夾沒照片時才會報錯。
   - 舊的「用商品編號重跑既有商品」模式仍保留，需要 `archive/`（`--healer-root`），預設流程不會碰到。
   - 已驗證：`--healer-root /不存在的路徑` 下新品模式正常產出；用快取重算 8 件第五輪商品，選出的範例與 `pilot-v5-結果/plan-r5.json` 全部相同。
   - 已清掉 `data/fingerprints.json` 裡 3 筆 Mac 測試路徑的舊資料。
2. ✅ **已完成（2026-11-03，尚未 commit）：CLI 引擎吃「背景資訊」「自身感受」兩欄。**
   - `--new` 描述檔多了選填的 `background`／`feelings`（或中文鍵名 `背景資訊`／`自身感受`，字串或字串清單）。有填時 task.md 在「# 新商品」後加「# 小編提供的資訊」，寫明可直接用小編口吻、不加方括號，優先於寫作規則的方括號那條，但只限實際寫出的內容、不可延伸誇大；其他證明不了的照舊加括號。
   - 已驗證：兩欄沒填或只有空白時，task.md 與修改前逐字相同。
3. ✅ **已完成（2026-11-03，尚未 commit）：訓練紀錄存成檔案 `data/training.json`**（起始為空）。
   - `scripts/training.py`：`heart`／`ban`／`unban`／`final`／`show`，原子寫入、加檔案鎖（兩人同時寫不會互蓋）、不重複加入。
   - `prepare.py` 在寫作規則後、範例文案前加「# 團隊喜好（訓練紀錄）」：最近 40 句 ♡、全部禁用詞、最近 3 篇定稿（常數 `HEARTS_SHOWN`、`FINALS_SHOWN`），並寫明不能推翻方括號規則、♡ 不可整句照抄。`--training` 可指定其他檔案。
   - 已驗證：紀錄空或檔案不存在時 task.md 逐字不變；用 /tmp 的測試紀錄（45 句 ♡、5 篇定稿）確認只帶入最後 40 句和最後 3 篇。
   - 網頁版 `app/` 的「訓練紀錄優先於一般規則」沒有改（照第 1 點不再動 `app/`）；第 5 項做面板時，寫回請呼叫 `scripts/training.py`。
4. 在雲端電腦上用一件新品從頭跑一次，給使用者看三版結果。
5. 🟡 **挑句子的介面：手機面板 v2（專案＋多個商品）開發中（2026-11-03，尚未 commit）。** 用法與 Bot 工作流程見 README「手機面板（v2）」。
   - v1（一個連結＝一件商品，`/s/<token>`）已上線過，只有測試資料；v2 改成「一期一個專案連結 `/p/<token>`，裡面多個商品」，舊 `/s/` 路由與 `hcp_sessions` 表移除。
   - 使用者需求：同時處理 2 個以上商品、有空閒一起處理；按「結束這期專案」才把網址關閉（變唯讀）；定稿後多一塊「我修改後的最終文案」，專案結束後讓 Bot 學使用者的文字偏好。
   - 已完成（Python 端）：
     - `scripts/panel_session.py` v2：create／projects／show／queue／pull／pull-all／push／sync／export／close／reopen／config-webhook／delete。`pull` 先原子 claim，已被領走 exit 3（`--force` 重領）；`pull-all` 一次領取全部已送出的商品；`sync` 同步整個專案並把修改後文案寫成 finals 的 `edited_text`（重跑會更新、不重複）；`export` 產出 `export.json`＋給 Grok 讀的 `export.md`（AI 三路線、草稿改字、定稿、修改後文案、逐行差異）。
     - `scripts/training.py`：finals 選填 `edited_text`；新增 `style_notes`（`note add/list/remove`，≤80 字、上限 30 條、不重複）；`show` 顯示筆數。
     - `scripts/prepare.py`：「團隊喜好」加「文字偏好」段（最近 12 條，寫明不能推翻方括號等規則），定稿有 `edited_text` 時改顯示修改後版本。
     - 已驗證：`python3 -m unittest discover -s scripts -p "test_*.py"`（CLI 對內建的記憶體版 v2 假 API；訓練紀錄；紀錄沒有新鍵時 task.md 與修改前逐字相同的回歸測試）。
   - 同時進行中（別人負責）：後端 `panel/supabase/`（migration `002_projects.sql`、v2 路由，含 Bot 用的 `POST /bot/projects/:token/close`）、前端 `panel/public/`、假後端與 e2e `panel/dev/`。
   - 尚待：
     - 部署 v2 migration 與 Edge Function；Vercel 加 `/p/:token` rewrite。
     - 用真的後端跑一次 Bot 全流程（create → 手機送出 2 個商品 → `pull-all` → `push` → 定稿＋修改後文案 → `sync` → 結束專案 → `export` → `note add`），確認 Python 端呼叫的路徑與欄位跟後端一致（兩邊都照 v2 規格各自寫，還沒對接過）。
     - 在雲端電腦設定 `HEALER_PANEL_API`／`HEALER_PANEL_BOT_KEY`／`HEALER_PANEL_URL`，用 `config-webhook` 設定 webhook，讓 Bot 處理 `panel.products.submitted`（→ `pull-all`）與 `panel.project.closed`（→ `sync` → `export` → 歸納文字偏好）；沒有 webhook 就定時跑 `queue`。
     - iPhone Safari／Android Chrome 實機：拍照上傳、HEIC 照片、EXIF 方向、深色模式。
   - 不能給連結時的備案（在聊天裡挑：Bot 列出編號句子，使用者回「A2 B5 C1，愛心 B5，禁用『超級』」）先保留。
6. ✅ **可自助部署（2026-11-03，尚未 commit）**：repo 不再寫死任何人的 Supabase／Vercel 網址（前端網址由 `panel/build.mjs` 依 `PANEL_API_URL` 產生），`data/training.json` 隨 repo 附上 Healer 的基底偏好（♡、不喜歡、定稿、`style_notes`），接手的 Bot 直接繼承後繼續累積；想從零開始可刪掉或改成空紀錄，且不可放個資／金鑰／真實專案 token，`panel_session.py` 會讀 `~/.config/healer-panel/panel.env`。別人的 Grok Bot clone 後照 `DEPLOY.md` 就能部署自己的一套。

## 注意

- `gen_one.sh` 用 `claude -p ... --model opus`，雲端電腦要先 `claude auth login`。
- 範例清單刻意排除正式考卷的 20 件商品，不要拿那 20 件來寫或測。
- `pilot-v5-結果/` 是第五輪的成果和原始資料，當對照組用，不要改。
