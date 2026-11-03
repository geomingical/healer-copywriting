-- Healer 文案面板 v2：專案（一期）＋多商品。可重複執行（idempotent）。
-- 建立 hcp_projects／hcp_products 與照片函式，移除 v1 的 hcp_sessions 與舊函式。
-- 001_init.sql 保留不改；先跑 001 再跑本檔（hcp_config 與 bucket 由 001 建立，這裡也補一次以防萬一）。

create table if not exists public.hcp_config (
  key   text primary key,
  value text not null
);

create table if not exists public.hcp_projects (
  token               text primary key,
  title               text not null default '',
  status              text not null default 'open' check (status in ('open', 'closed')),
  options             jsonb not null default '{}',
  created_at          timestamptz default now(),
  updated_at          timestamptz default now(),
  closed_at           timestamptz,
  close_notify_status text,
  expires_at          timestamptz  -- 開啟中為 null；結束時設為 now() + 180 天（僅作保留期限，無自動清理）
);

create table if not exists public.hcp_products (
  id            uuid primary key default gen_random_uuid(),
  project_token text not null references public.hcp_projects (token) on delete cascade,
  sku           text not null,
  position      int not null,
  status        text not null default 'draft'
                check (status in ('draft', 'submitted', 'generating', 'ready', 'finalized')),
  fields        jsonb not null default '{}',
  photos        jsonb not null default '[]',
  result        jsonb,
  state         jsonb not null default '{}',
  state_rev     int not null default 0,
  edited_final  text,
  edited_at     timestamptz,
  notify_status text,
  submitted_at  timestamptz,
  claimed_at    timestamptz,
  synced_at     timestamptz,
  created_at    timestamptz default now(),
  updated_at    timestamptz default now(),
  unique (project_token, sku)
);

create index if not exists hcp_products_project_position_idx on public.hcp_products (project_token, position);
create index if not exists hcp_products_status_idx on public.hcp_products (status);
create index if not exists hcp_projects_status_created_idx on public.hcp_projects (status, created_at desc);

-- RLS 全開、不建任何 policy：只有 service role 能讀寫。
alter table public.hcp_config   enable row level security;
alter table public.hcp_projects enable row level security;
alter table public.hcp_products enable row level security;

-- 照片 bucket（同 001，重跑無害）。
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('healer-panel', 'healer-panel', false, 8388608,
        array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- 移除 v1：舊函式（簽名是 text token）與舊表（只有測試資料，已匯出）。
drop function if exists public.hcp_photo_add(text, text, text, bigint, int);
drop function if exists public.hcp_photo_remove(text, text);
drop table if exists public.hcp_sessions;

-- 照片清單的原子新增／刪除（作用於商品；商品必須是 draft、專案必須 open）。
create or replace function public.hcp_photo_add(
  p_product uuid, p_path text, p_name text, p_size bigint, p_max int
) returns jsonb
language plpgsql
set search_path = public
as $$
declare
  v_status  text;
  v_project text;
  v_photos  jsonb;
  v_next    int;
begin
  select status, project_token, photos into v_status, v_project, v_photos
    from public.hcp_products where id = p_product for update;
  if not found then
    return jsonb_build_object('error', 'not_found');
  end if;
  if exists (select 1 from public.hcp_projects where token = v_project and status <> 'open') then
    return jsonb_build_object('error', 'project_closed');
  end if;
  if v_status <> 'draft' then
    return jsonb_build_object('error', 'not_draft');
  end if;
  if exists (select 1 from jsonb_array_elements(v_photos) e where e->>'path' = p_path) then
    return jsonb_build_object('photos', v_photos);
  end if;
  if jsonb_array_length(v_photos) >= p_max then
    return jsonb_build_object('error', 'too_many');
  end if;
  select coalesce(max((e->>'order')::int), 0) + 1 into v_next
    from jsonb_array_elements(v_photos) e;
  update public.hcp_products
     set photos = photos || jsonb_build_array(jsonb_build_object(
           'path', p_path, 'name', p_name, 'size', p_size, 'order', v_next)),
         updated_at = now()
   where id = p_product
  returning photos into v_photos;
  return jsonb_build_object('photos', v_photos);
end $$;

create or replace function public.hcp_photo_remove(p_product uuid, p_path text)
returns jsonb
language plpgsql
set search_path = public
as $$
declare
  v_status  text;
  v_project text;
  v_photos  jsonb;
begin
  select status, project_token, photos into v_status, v_project, v_photos
    from public.hcp_products where id = p_product for update;
  if not found then
    return jsonb_build_object('error', 'not_found');
  end if;
  if exists (select 1 from public.hcp_projects where token = v_project and status <> 'open') then
    return jsonb_build_object('error', 'project_closed');
  end if;
  if v_status <> 'draft' then
    return jsonb_build_object('error', 'not_draft');
  end if;
  select coalesce(jsonb_agg(e order by ord), '[]'::jsonb) into v_photos
    from jsonb_array_elements(v_photos) with ordinality as t(e, ord)
   where e->>'path' <> p_path;
  update public.hcp_products
     set photos = v_photos, updated_at = now()
   where id = p_product;
  return jsonb_build_object('photos', v_photos);
end $$;

-- 只給 service role 呼叫。
revoke all on function public.hcp_photo_add(uuid, text, text, bigint, int) from public, anon, authenticated;
revoke all on function public.hcp_photo_remove(uuid, text) from public, anon, authenticated;
grant execute on function public.hcp_photo_add(uuid, text, text, bigint, int) to service_role;
grant execute on function public.hcp_photo_remove(uuid, text) to service_role;
