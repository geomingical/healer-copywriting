-- Healer 文案面板（panel）資料表。可重複執行（idempotent）。
-- 只建立 hcp_ 開頭的表與函式、以及私有 bucket healer-panel；不碰其他表。

create table if not exists public.hcp_config (
  key   text primary key,
  value text not null
);

create table if not exists public.hcp_sessions (
  token         text primary key,
  created_at    timestamptz default now(),
  updated_at    timestamptz default now(),
  status        text not null default 'open',
  options       jsonb not null default '{}',
  fields        jsonb not null default '{}',
  photos        jsonb not null default '[]',
  result        jsonb,
  state         jsonb not null default '{}',
  state_rev     int not null default 0,
  notify_status text,
  synced_at     timestamptz,
  expires_at    timestamptz default now() + interval '30 days'
);

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'hcp_sessions_status_check') then
    alter table public.hcp_sessions add constraint hcp_sessions_status_check
      check (status in ('open', 'submitted', 'generating', 'ready', 'finalized'));
  end if;
end $$;

create index if not exists hcp_sessions_status_updated_idx
  on public.hcp_sessions (status, updated_at desc);

-- RLS 全開、不建任何 policy：只有 service role 能讀寫。
alter table public.hcp_config   enable row level security;
alter table public.hcp_sessions enable row level security;

-- 照片：私有 bucket。大小與型別上限也在 bucket 層擋一次（與 API 檢查相同）。
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('healer-panel', 'healer-panel', false, 8388608,
        array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- 照片清單的原子新增／刪除（避免同時上傳多張時互相覆蓋）。
create or replace function public.hcp_photo_add(
  p_token text, p_path text, p_name text, p_size bigint, p_max int
) returns jsonb
language plpgsql
set search_path = public
as $$
declare
  v_status text;
  v_photos jsonb;
  v_next   int;
begin
  select status, photos into v_status, v_photos
    from public.hcp_sessions where token = p_token for update;
  if not found then
    return jsonb_build_object('error', 'not_found');
  end if;
  if v_status <> 'open' then
    return jsonb_build_object('error', 'not_open');
  end if;
  if exists (select 1 from jsonb_array_elements(v_photos) e where e->>'path' = p_path) then
    return jsonb_build_object('photos', v_photos);
  end if;
  if jsonb_array_length(v_photos) >= p_max then
    return jsonb_build_object('error', 'too_many');
  end if;
  select coalesce(max((e->>'order')::int), 0) + 1 into v_next
    from jsonb_array_elements(v_photos) e;
  update public.hcp_sessions
     set photos = photos || jsonb_build_array(jsonb_build_object(
           'path', p_path, 'name', p_name, 'size', p_size, 'order', v_next)),
         updated_at = now()
   where token = p_token
  returning photos into v_photos;
  return jsonb_build_object('photos', v_photos);
end $$;

create or replace function public.hcp_photo_remove(p_token text, p_path text)
returns jsonb
language plpgsql
set search_path = public
as $$
declare
  v_status text;
  v_photos jsonb;
begin
  select status, photos into v_status, v_photos
    from public.hcp_sessions where token = p_token for update;
  if not found then
    return jsonb_build_object('error', 'not_found');
  end if;
  if v_status <> 'open' then
    return jsonb_build_object('error', 'not_open');
  end if;
  select coalesce(jsonb_agg(e order by ord), '[]'::jsonb) into v_photos
    from jsonb_array_elements(v_photos) with ordinality as t(e, ord)
   where e->>'path' <> p_path;
  update public.hcp_sessions
     set photos = v_photos, updated_at = now()
   where token = p_token;
  return jsonb_build_object('photos', v_photos);
end $$;

-- 只給 service role 呼叫。
revoke all on function public.hcp_photo_add(text, text, text, bigint, int) from public, anon, authenticated;
revoke all on function public.hcp_photo_remove(text, text) from public, anon, authenticated;
grant execute on function public.hcp_photo_add(text, text, text, bigint, int) to service_role;
grant execute on function public.hcp_photo_remove(text, text) to service_role;
