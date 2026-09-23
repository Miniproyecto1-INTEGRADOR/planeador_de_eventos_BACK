-- Ejecutar una vez en Supabase Dashboard > SQL Editor.
-- Vincula public.users con Supabase Auth y limita eventos/subtareas al propietario.

create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;

create or replace function public.sync_auth_user_profile()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  profile_id uuid;
begin
  -- Vincula por correo el perfil legado, si existe. Nunca guarda la contraseÃ±a
  -- recibida en public.users; Supabase Auth es el Ãºnico proveedor de credenciales.
  select u.id into profile_id
  from public.users as u
  where lower(u.email) = lower(new.email)
  limit 1;

  if profile_id is not null then
    update public.users
    set id = new.id,
        first_name = coalesce(nullif(new.raw_user_meta_data ->> 'first_name', ''), first_name),
        last_name = coalesce(nullif(new.raw_user_meta_data ->> 'last_name', ''), last_name),
        email = lower(new.email),
        password = extensions.crypt(extensions.gen_random_uuid()::text, extensions.gen_salt('bf'))
    where id = profile_id;
  else
    insert into public.users (id, first_name, last_name, email, password, daily_limit_minutes, created_at)
    values (
      new.id,
      coalesce(nullif(new.raw_user_meta_data ->> 'first_name', ''), 'Usuario'),
      coalesce(nullif(new.raw_user_meta_data ->> 'last_name', ''), ''),
      lower(new.email),
      extensions.crypt(extensions.gen_random_uuid()::text, extensions.gen_salt('bf')),
      360,
      now()
    );
  end if;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created_sync_profile on auth.users;
create trigger on_auth_user_created_sync_profile
after insert on auth.users
for each row execute function public.sync_auth_user_profile();

-- Los RPC de autenticaciÃ³n anteriores ya no deben aceptar contraseÃ±as directas.
drop function if exists public.authenticate_app_user(text, text);
drop function if exists public.register_app_user(text, text, text, text);

alter table public.users enable row level security;
alter table public.events enable row level security;
alter table public.subtasks enable row level security;

revoke all on table public.users, public.events, public.subtasks from anon;
revoke all on table public.users from authenticated;
grant select (id, first_name, last_name, email, daily_limit_minutes, created_at),
      update (daily_limit_minutes)
on table public.users to authenticated;
grant select, insert, update, delete on table public.events to authenticated;
grant select, insert, update, delete on table public.subtasks to authenticated;

drop policy if exists users_read_self on public.users;
create policy users_read_self on public.users
for select to authenticated using (id = (select auth.uid()));
drop policy if exists users_update_self on public.users;
create policy users_update_self on public.users
for update to authenticated using (id = (select auth.uid())) with check (id = (select auth.uid()));

drop policy if exists events_read_own on public.events;
create policy events_read_own on public.events
for select to authenticated using (user_id::text = (select auth.uid())::text);
drop policy if exists events_insert_own on public.events;
create policy events_insert_own on public.events
for insert to authenticated with check (user_id::text = (select auth.uid())::text);
drop policy if exists events_update_own on public.events;
create policy events_update_own on public.events
for update to authenticated using (user_id::text = (select auth.uid())::text) with check (user_id::text = (select auth.uid())::text);
drop policy if exists events_delete_own on public.events;
create policy events_delete_own on public.events
for delete to authenticated using (user_id::text = (select auth.uid())::text);

drop policy if exists subtasks_read_own on public.subtasks;
create policy subtasks_read_own on public.subtasks
for select to authenticated using (
  exists (select 1 from public.events e where e.id = subtasks.event_id and e.user_id::text = (select auth.uid())::text)
);
drop policy if exists subtasks_insert_own on public.subtasks;
create policy subtasks_insert_own on public.subtasks
for insert to authenticated with check (
  exists (select 1 from public.events e where e.id = subtasks.event_id and e.user_id::text = (select auth.uid())::text)
);
drop policy if exists subtasks_update_own on public.subtasks;
create policy subtasks_update_own on public.subtasks
for update to authenticated using (
  exists (select 1 from public.events e where e.id = subtasks.event_id and e.user_id::text = (select auth.uid())::text)
) with check (
  exists (select 1 from public.events e where e.id = subtasks.event_id and e.user_id::text = (select auth.uid())::text)
);
drop policy if exists subtasks_delete_own on public.subtasks;
create policy subtasks_delete_own on public.subtasks
for delete to authenticated using (
  exists (select 1 from public.events e where e.id = subtasks.event_id and e.user_id::text = (select auth.uid())::text)
);