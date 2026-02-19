# [Nombre del Hackathon] — Bot / Agente Competitivo

<!--
  TEMPLATE DE HACKATHON PARA FORJA
  ================================
  Instrucciones:
  1. Llena cada seccion con la informacion especifica de TU competencia
  2. Pega la documentacion del API en la seccion correspondiente
  3. Pega las reglas del juego/competencia en su seccion
  4. Define tu estrategia en la seccion de Estrategia
  5. Guarda, cierra vim, y ejecuta: forja auto

  Forja se encarga del resto: spec review, build, tests, iteracion.
-->

## Contexto de la Competencia

- **Hackathon**: [Nombre del evento]
- **Formato**: [Vibe Racing / Hackathon clasico / Competencia online / etc.]
- **Duracion**: [2 horas / 24 horas / 1 semana]
- **Restricciones**: [Solo Python / cualquier stack / etc.]
- **Metrica de evaluacion**: [Profit %, score, velocidad, etc.]
- **Objetivo**: [Maximizar profit / ganar ranking / completar challenges]

## Reglas del Juego

<!--
  PEGA AQUI las reglas exactas de la competencia.
  Esto es CRITICO: Forja necesita saber que esta permitido y que no.
  Incluye:
  - Turnos, rondas, o ciclos del juego
  - Limites (tiempo, requests, presupuesto)
  - Condiciones de victoria
  - Penalizaciones
-->

```
[Pega aqui las reglas completas]
```

## API / Interfaz del Sistema

<!--
  PEGA AQUI la documentacion completa del API.
  Endpoints, payloads, responses, codigos de error.
  Si es un mock API, incluye los datos de prueba.
  Si es WebSocket, incluye el formato de mensajes.
-->

### Base URL
```
[URL del API, ej: https://api.hackathon.com/v1]
```

### Autenticacion
```
[API key, token, headers requeridos]
```

### Endpoints

```
[Pega aqui la documentacion de endpoints]
```

### Modelos de Datos

```
[Schemas, tipos, enums relevantes]
```

## Estrategia Competitiva

<!--
  ESTA es la seccion donde defines TU ventaja.
  Forja construira el bot siguiendo esta estrategia.
-->

### Enfoque Principal
- [Ej: "Optimizar margen por orden, no volumen"]
- [Ej: "Bid agresivo en horas pico, conservador en valle"]
- [Ej: "Priorizar items de alto margen sobre popularidad"]

### Heuristicas Clave
1. **[Nombre heuristica 1]**: [Descripcion, ej: "Si el margen < 15%, rechazar orden"]
2. **[Nombre heuristica 2]**: [Descripcion, ej: "Ajustar precios +10% en peak hours"]
3. **[Nombre heuristica 3]**: [Descripcion]

### Decisiones de Diseno
- **Lenguaje**: Python 3.12 (o el que requiera la competencia)
- **Arquitectura**: [Script simple / servidor HTTP / WebSocket client]
- **Estado**: [Stateless / in-memory dict / SQLite]
- **Logging**: Todo a stdout + archivo de decisiones para post-mortem

## Requerimientos Funcionales

### Feature 1: Conexion al API
- Conectar al API de la competencia con autenticacion
- Manejar rate limits y errores de red con retry exponencial
- Timeout de [N] segundos por request

### Feature 2: Motor de Decisiones
- Recibir [datos del juego: ordenes, estado, etc.]
- Aplicar las heuristicas definidas en la seccion de Estrategia
- Retornar decision en el formato requerido por el API
- Log de cada decision con razon y metricas

### Feature 3: Loop Principal
- [Polling cada N segundos / WebSocket listener / HTTP server]
- Manejar el ciclo completo: recibir → decidir → actuar → registrar
- Graceful shutdown en Ctrl+C

### Feature 4: Metricas y Post-Mortem
- Trackear: decisiones tomadas, aceptadas, rechazadas
- Calcular: [metrica principal, ej: profit acumulado, score]
- Dump final de metricas al terminar la competencia

## Requerimientos No-Funcionales

- **Latencia**: Responder en < [100ms / 500ms / 1s] por decision
- **Resiliencia**: No crashear por errores del API; retry y continuar
- **Simplicidad**: Codigo minimo y legible. Menos de 500 lineas total
- **Zero dependencies externas**: Solo stdlib + requests (o httpx)

## Validacion

### Tests
- Test unitario del motor de decisiones con datos mock
- Test de integracion con el API (si hay sandbox/staging)
- Test de edge cases: API down, respuestas invalidas, timeout

### Criterio de Exito
- [ ] Bot se conecta al API y completa un ciclo sin errores
- [ ] Motor de decisiones aplica las heuristicas correctamente
- [ ] Metricas se calculan y reportan al final
- [ ] [Metrica competitiva] > [umbral minimo, ej: profit > 0%]

## Notas para Forja

<!--
  Instrucciones especiales para el pipeline de Forja.
  Esto va directo al contexto del agente.
-->

- Este es un proyecto de COMPETENCIA: priorizar velocidad de entrega sobre perfeccion
- El codigo debe ser SIMPLE: un solo archivo main.py si es posible
- NO usar frameworks pesados (Django, FastAPI) — requests/httpx directo
- Incluir un README.md minimo con: como correr, como configurar, como ver resultados
- El bot debe funcionar con: `python main.py` (sin argumentos complicados)
- Variables de entorno para configuracion: API_KEY, API_URL, etc.
