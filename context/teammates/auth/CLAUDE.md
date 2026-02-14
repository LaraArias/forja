# Teammate: auth

## Épica
Autenticación: registro, login, JWT y middleware.

## Archivos a crear
- src/auth/__init__.py
- src/auth/models.py (modelo User con SQLAlchemy)
- src/auth/schemas.py (Pydantic schemas)
- src/auth/security.py (hash_password, verify_password, create_access_token, get_current_user)
- src/auth/router.py (POST /auth/register, POST /auth/login)
- src/auth/database.py (engine, SessionLocal, Base, get_db)
- src/auth/main.py (FastAPI app con CORS, incluye auth router, crea tablas al inicio)

## Stack
FastAPI, SQLAlchemy (SQLite), python-jose[cryptography], passlib[bcrypt], pydantic. Instala deps con pip3.

## App principal
src/auth/main.py debe crear la app FastAPI, habilitar CORS (allow_origins=["*"]), incluir el router de auth, y crear tablas en startup. La app corre en puerto 8000.

## Paths
- Tu features.json está en context/teammates/auth/features.json
- Tu validation_spec.json está en context/teammates/auth/validation_spec.json

## Flujo de trabajo
- Cuando creas que un feature está listo, corre: `python3 .forja-tools/forja_features.py attempt [id] --dir context/teammates/auth/`
- Si la validación pasa, corre: `python3 .forja-tools/forja_features.py pass [id] --dir context/teammates/auth/`
- Cuando termines una tarea, lee features.json. Si hay features con passes: false, trabaja en el siguiente. No te detengas hasta que todos pasen.
- No pidas confirmación humana. Si 2 approaches fallan, escala al lead.
- Commit después de cada tarea: `git commit --author='teammate-auth <auth@forja>' -m '[mensaje]'`
- Usa python3 siempre, nunca python
