// 範例：真正的 config.js 由 panel/build.mjs 產生（不 commit），部署時設定環境變數 PANEL_API_URL：
//   PANEL_API_URL=https://<你的專案代號>.supabase.co/functions/v1/panel-api node build.mjs
// 這裡只放面板後端（Supabase Edge Function panel-api）的公開網址，絕對不要放任何金鑰。
// 部署步驟見 repo 根目錄的 DEPLOY.md。
window.PANEL_CONFIG = { api: "https://<你的專案代號>.supabase.co/functions/v1/panel-api" };
