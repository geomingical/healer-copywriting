# 轉移包（transfer/）

這裡放「把某一期專案的完成狀態，交給另一個 Grok Bot／另一套面板」時用的快照。

每個子資料夾是一期：匯出檔、照片、當時的 `training.json`，以及給接手方看的說明。

| 資料夾 | 說明 |
| --- | --- |
| [`2026-11-05-healer/`](2026-11-05-healer/) | 2026-11-05「11月上架（新）」兩件已定稿商品＋訓練紀錄快照 |

## 一鍵還原（對方部署好自己的面板之後）

```bash
# 在 repo 根目錄；panel.env 已設定好三個 HEALER_PANEL_* 變數
python3 scripts/restore_transfer.py transfer/2026-11-05-healer
```

細節、注意事項、失敗時只合併訓練紀錄的做法，見各期資料夾裡的 `README.md`。

未來若再打包一期，請新建 `transfer/YYYY-MM-DD-<名字>/`，並在本表加一列。
