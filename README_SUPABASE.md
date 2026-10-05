# Configuración de Supabase

El backend usa Supabase Auth para iniciar sesión y el Data API para acceder a `public.users`, `public.events` y `public.subtasks`. El token de sesión del usuario se reenvía a PostgREST, donde RLS limita los datos.

## Configuración local

Crea `back/.env` a partir de `.env.example` y completa `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY`. No configures ni publiques una clave `secret`, `service_role`, ni credenciales PostgreSQL.

## Preparar Supabase

En Supabase Dashboard abre **SQL Editor**, ejecuta una vez [`supabase/supabase_auth_rls.sql`](supabase/supabase_auth_rls.sql) y luego reinicia el backend.

El script agrega un trigger que sincroniza cada cuenta nueva de Supabase Auth con `public.users`, y políticas para que cada usuario solo pueda consultar y modificar sus eventos y subtareas. También enlaza por correo perfiles existentes en `public.users`; no migra contraseñas. El usuario existente debe crear una cuenta en la aplicación con el mismo correo y contraseña. Si Supabase pide confirmar el correo, debe confirmarlo y luego iniciar sesión.

## Iniciar

Desde `back`:

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

API docs: `http://127.0.0.1:8000/docs`. El frontend corre en `http://127.0.0.1:5173`.

## Desplegar junto al frontend en Vercel

El frontend y la API deben ser proyectos separados de Vercel, aunque usen el mismo repositorio:

1. En Vercel, importa otra vez el repositorio y crea un proyecto para la API. En **Root Directory**, selecciona `planeador_de_eventos_BACK`. Vercel detecta FastAPI desde `app/main.py` y sus dependencias en `requirements.txt`.
2. En **Settings > Environment Variables** de ese proyecto, configura `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY`. Usa la URL del proyecto Supabase y una clave publicable vigente. El `.env` local no se despliega; no configures `DATABASE_URL` ni `SUPABASE_SERVICE_ROLE_KEY`.
3. Despliega el proyecto y comprueba `https://<dominio-de-la-api>/api/health/`. Debe devolver JSON, no el HTML del frontend.
4. En el proyecto Vercel del frontend, configura `VITE_API_URL` con el dominio base de la API, por ejemplo `https://<dominio-de-la-api>` (sin `/api`). Vuelve a desplegar el frontend para que Vite incorpore esa variable.

El backend ya permite solicitudes desde `https://planeador-de-eventos-fronted.vercel.app`. Si el dominio del frontend cambia, añade su origen a `CORS_ORIGINS` en el proyecto de la API.
