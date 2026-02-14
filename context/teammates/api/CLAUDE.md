# Teammate: api

## Épica
CRUD de tareas con autenticación JWT.

## Archivos a crear
- src/api/__init__.py
- src/api/models.py (modelo Task con SQLAlchemy: id, title, description, status, owner_id FK a users)
- src/api/schemas.py (Pydantic schemas para Task)
- src/api/router.py (GET/POST /tasks, PUT/DELETE /tasks/{id})

## Integración con auth
Importa desde src/auth: `from auth.database import Base, get_db`, `from auth.security import get_current_user`, `from auth.models import User`. El router usa `Depends(get_current_user)` para proteger todas las rutas. Registra el router en src/auth/main.py (la app principal).

## Importante
- Agrega el router de tasks en src/auth/main.py: `from api.router import router as tasks_router` y `app.include_router(tasks_router)`
- El modelo Task debe usar la misma Base de auth.database
- Status default: "pending". Valores válidos: pending, in_progress, done
- Cada tarea pertenece al usuario que la creó (owner_id)
- GET /tasks solo retorna tareas del usuario autenticado
- GET /tasks acepta query param ?status= para filtrar

## Paths
- Tu features.json está en context/teammates/api/features.json
- Tu validation_spec.json está en context/teammates/api/validation_spec.json

## Flujo de trabajo
- Cuando creas que un feature está listo, corre: `python3 .forja-tools/forja_features.py attempt [id] --dir context/teammates/api/`
- Si la validación pasa, corre: `python3 .forja-tools/forja_features.py pass [id] --dir context/teammates/api/`
- Cuando termines una tarea, lee features.json. Si hay features con passes: false, trabaja en el siguiente. No te detengas hasta que todos pasen.
- No pidas confirmación humana. Si 2 approaches fallan, escala al lead.
- Commit después de cada tarea: `git commit --author='teammate-api <api@forja>' -m '[mensaje]'`
- Usa python3 siempre, nunca python
