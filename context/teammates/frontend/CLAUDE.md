# Teammate: frontend

## Épica
Frontend web para gestionar tareas.

## Archivos a crear
- src/frontend/index.html (página principal con login/register y dashboard)
- src/frontend/style.css (diseño limpio y funcional)
- src/frontend/app.js (lógica JS vanilla)

## Comportamiento
- Página de login/register: formularios que llaman a POST /auth/register y POST /auth/login
- Guarda access_token en localStorage al hacer login
- Dashboard: lista tareas de GET /tasks con Authorization: Bearer <token>
- Crear tarea: formulario que hace POST /tasks
- Editar tarea: inline o modal, hace PUT /tasks/{id}
- Eliminar tarea: botón que hace DELETE /tasks/{id}
- Filtros por status: botones/select que agregan ?status=pending|in_progress|done
- API base URL: http://localhost:8000
- Si no hay token, muestra login. Si hay token, muestra dashboard.

## Servir archivos estáticos
Agrega en src/auth/main.py: `from fastapi.staticfiles import StaticFiles` y `app.mount("/", StaticFiles(directory="src/frontend", html=True), name="frontend")` — esto debe ir DESPUÉS de todos los routers.

## Paths
- Tu features.json está en context/teammates/frontend/features.json
- Tu validation_spec.json está en context/teammates/frontend/validation_spec.json

## Flujo de trabajo
- Cuando creas que un feature está listo, corre: `python3 .forja-tools/forja_features.py attempt [id] --dir context/teammates/frontend/`
- Si la validación pasa, corre: `python3 .forja-tools/forja_features.py pass [id] --dir context/teammates/frontend/`
- Cuando termines una tarea, lee features.json. Si hay features con passes: false, trabaja en el siguiente. No te detengas hasta que todos pasen.
- No pidas confirmación humana. Si 2 approaches fallan, escala al lead.
- Commit después de cada tarea: `git commit --author='teammate-frontend <frontend@forja>' -m '[mensaje]'`
- Usa python3 siempre, nunca python
