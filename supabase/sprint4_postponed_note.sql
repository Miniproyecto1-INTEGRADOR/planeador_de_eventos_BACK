alter table public.subtasks
add column if not exists postponed_note text;

notify pgrst, 'reload schema';