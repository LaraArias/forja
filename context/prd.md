# Task Manager API + Frontend

Aplicación web completa para gestionar tareas con autenticación.

## Épicas

### Auth
- POST /auth/register - crear usuario (email, password)
- POST /auth/login - devuelve JWT token
- Middleware que protege rutas con Bearer token
- Passwords hasheados con bcrypt

### API
- GET /tasks - listar tareas del usuario autenticado
- POST /tasks - crear tarea (title, description)
- PUT /tasks/:id - actualizar tarea (title, description, status)
- DELETE /tasks/:id - eliminar tarea
- Filtrar por status: pending, in_progress, done
- Cada tarea pertenece al usuario que la creó

### Frontend
- Página de login/register
- Dashboard con lista de tareas
- Crear, editar, eliminar tareas desde la UI
- Filtros por status
- Diseño limpio y funcional

## Stack
- Backend: Python + FastAPI
- Database: SQLite con SQLAlchemy
- Auth: JWT con python-jose, bcrypt
- Frontend: HTML + CSS + vanilla JavaScript
- CORS habilitado para desarrollo local
