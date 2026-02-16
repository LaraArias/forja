# Teammate: qa

## Épica
Testing de integración del sistema completo.

## Tests a ejecutar (todos con curl contra http://localhost:8000)
1. Arrancar servidor: `cd src && python3 -m uvicorn auth.main:app --port 8000 &` — esperar 3 segundos
2. POST /auth/register con {"email":"test@test.com","password":"test123"} — esperar 201
3. POST /auth/login con {"email":"test@test.com","password":"test123"} — esperar 200 con access_token
4. POST /tasks con Bearer token y {"title":"Test task","description":"Desc"} — esperar 201
5. GET /tasks con Bearer token — esperar 200 con lista que incluye la tarea creada
6. PUT /tasks/1 con {"status":"in_progress"} — esperar 200
7. GET /tasks?status=in_progress — esperar que retorne la tarea
8. DELETE /tasks/1 — esperar 200
9. GET /tasks — esperar lista vacía
10. Verificar que GET /tasks sin token retorna 401
11. Matar el servidor al terminar

## Paths
- Tu features.json está en context/teammates/qa/features.json

## Flujo de trabajo
- Cuando todos los tests pasen, corre: `python3 .forja-tools/forja_features.py attempt qa-001 --dir context/teammates/qa/`
- Si la validación pasa, corre: `python3 .forja-tools/forja_features.py pass qa-001 --dir context/teammates/qa/`
- No pidas confirmación humana. Si 2 approaches fallan, escala al lead.
- Commit después de cada tarea: `git commit --author='teammate-qa <qa@forja>' -m '[mensaje]'`
- Usa python3 siempre, nunca python
- Limpia: borra el archivo .db de SQLite y mata procesos uvicorn al terminar
