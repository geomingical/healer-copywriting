// 產生 public/config.js：面板前端要連的後端（Supabase Edge Function panel-api）網址。
// 純 Node，無相依套件。Vercel 部署時由 vercel.json 的 buildCommand 執行。
//
//   PANEL_API_URL=https://<專案代號>.supabase.co/functions/v1/panel-api node build.mjs
//   node build.mjs --api https://<專案代號>.supabase.co/functions/v1/panel-api
//
// 命令列 --api 優先於環境變數 PANEL_API_URL。config.js 只放公開網址，絕對不要放任何金鑰。
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SUFFIX = "/functions/v1/panel-api";
const HERE = dirname(fileURLToPath(import.meta.url));

function fail(message) {
  console.error(`錯誤：${message}`);
  console.error(`用法：PANEL_API_URL=https://<專案代號>.supabase.co${SUFFIX} node build.mjs`);
  console.error(`  或：node build.mjs --api https://<專案代號>.supabase.co${SUFFIX}`);
  process.exit(1);
}

function argValue(argv) {
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--api") {
      if (i + 1 >= argv.length) fail("--api 後面要接網址。");
      return argv[i + 1];
    }
    if (argv[i].startsWith("--api=")) return argv[i].slice("--api=".length);
  }
  return undefined;
}

const raw = (argValue(process.argv.slice(2)) ?? process.env.PANEL_API_URL ?? "").trim();
if (!raw) fail("沒有設定後端網址：請設定環境變數 PANEL_API_URL，或加 --api 網址。");

let url;
try {
  url = new URL(raw);
} catch {
  fail(`不是有效的網址：${raw}`);
}
if (url.protocol !== "https:") fail(`後端網址必須是 https：${raw}`);
if (url.search || url.hash || url.username || url.password) fail(`後端網址不能帶查詢字串、#、帳號密碼：${raw}`);
const path = url.pathname.replace(/\/+$/, "");
if (!path.endsWith(SUFFIX)) fail(`後端網址的路徑必須以 ${SUFFIX} 結尾：${raw}`);
const api = `${url.origin}${path}`;

const out = join(HERE, "public", "config.js");
writeFileSync(
  out,
  "// 由 panel/build.mjs 產生，不要手動修改、不要 commit（已列在 panel/.gitignore）。\n" +
    "// 面板後端（Supabase Edge Function panel-api）的網址。這裡只放公開網址，不放任何金鑰。\n" +
    `window.PANEL_CONFIG = { api: ${JSON.stringify(api)} };\n`,
);
console.log(`已寫出 ${out}：api = ${api}`);
