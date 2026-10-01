# API de produccion del orquestador multi-agente

API REST asincrona construida con FastAPI que expone el sistema multi-agente de
analisis de criticidad del Modulo 6, con estado persistido en Redis, trazas en
LangSmith y una pausa obligatoria para aprobacion humana en las tareas que el
sistema clasifica como criticas.

Pre-entrega 7 del curso de AI Engineering (Coderhouse).

## Endpoints

| Metodo | Ruta | Que hace |
|---|---|---|
| `GET` | `/health` | Chequeo de disponibilidad. |
| `POST` | `/tasks` | Encola una consulta y devuelve un `job_id`. Responde 202 sin esperar a los agentes. |
| `GET` | `/tasks/{job_id}` | Devuelve el estado del job desde Redis. |
| `POST` | `/tasks/{job_id}/approve` | Recibe la decision humana y reanuda el grafo detenido. |

## Orquestacion asincrona

`POST /tasks` registra el job en Redis, lo delega a un `BackgroundTask` y responde
de inmediato. El event loop nunca queda esperando al LLM: en la prueba de carga
las 5 peticiones se encolaron en **2,03 segundos**, mientras las ejecuciones de
los agentes tardaron entre 4 y 6 minutos cada una.

Todo el pipeline es asincrono de punta a punta (`ainvoke`, `async def`), de modo
que no hace falta `run_in_threadpool`: no hay llamadas bloqueantes dentro de los
endpoints.

## Estados del job en Redis

    PENDING -> RUNNING -> DONE
                       -> WAITING_APPROVAL -> RUNNING -> DONE
                       -> FAILED

Cada transicion se escribe en Redis bajo la clave `job:{job_id}`, con un TTL de
24 horas. El worker envuelve toda la ejecucion en un `try/except`: si el agente
falla en segundo plano, la excepcion se captura y el job pasa a `FAILED` con el
mensaje de error guardado, en lugar de dejar al cliente en un bucle infinito de
polling.

Redis cumple dos funciones distintas en el proyecto:

1. **Estado de los jobs**: lo que consulta el cliente en `GET /tasks/{job_id}`.
2. **Checkpoints de LangGraph**: `AsyncRedisSaver` persiste el estado del grafo,
   que es lo que permite congelar una ejecucion en el nodo de aprobacion y
   retomarla despues desde el punto exacto donde se interrumpio.

## Human-in-the-loop

El nodo `aprobacion` usa `interrupt()` de LangGraph. Cuando se dispara, el estado
queda guardado en Redis, el run termina y el job pasa a `WAITING_APPROVAL` con el
motivo y los aportes del equipo para que un humano decida.

El criterio de criticidad esta en `app/hitl.py`: se considera critica una tarea
cuyo costo de indisponibilidad supera los USD 20.000 (`COSTO_APROBACION_USD`).
La deteccion no depende de la redaccion exacta del modelo: se buscan con una
expresion regular todos los montos en dolares del aporte del analista y se
compara el mayor contra el umbral.

Al llamar a `POST /tasks/{job_id}/approve`, el grafo se reanuda con
`Command(resume=decision)` y la respuesta final incorpora la decision tomada.

En la prueba de carga el criterio discrimino correctamente: las 3 consultas sobre
el compresor C-02 (USD 40.800 de indisponibilidad) quedaron en `WAITING_APPROVAL`,
y las 2 de la bomba B-07 (USD 2.880) pasaron directo a `DONE`.

## Observabilidad

`app/observability.py` activa el tracing de LangSmith al arrancar la aplicacion.
Cada nodo del grafo aparece como un span dentro de la traza del run, con sus
tokens, su latencia y su costo.

Las capturas del dashboard estan en `/screenshots`:

- Lista de trazas de las 5 peticiones concurrentes.
- `Trace Latency`: percentiles de latencia por traza.
- `Cost per Trace` y `Total Cost`: costo por ejecucion y costo total de la corrida.

Nota sobre el percentil: el dashboard de LangSmith expone P50 y P99, no P95.
Se adjunta el P99, que es mas conservador que el p95 pedido en la consigna.

### Lectura de la corrida

Las 5 peticiones concurrentes costaron en total **USD 0,0279**. La latencia se
concentra casi por completo en las llamadas al LLM, no en la API ni en Redis: el
encolado tarda 2 segundos y cada ejecucion completa entre 4 y 6 minutos.

El nodo que mas tokens consume es el supervisor, porque interviene tres veces por
ejecucion (antes del investigador, antes del analista y para cerrar) y en cada
intervencion recibe la rubrica completa mas el historial de contribuciones.

## Control de ritmo del proveedor

La capa gratuita de Gemini limita a 5 peticiones por minuto por modelo. Con 5
tareas concurrentes que disparan 6 llamadas cada una, ese limite se supera de
inmediato y todas las ejecuciones terminan en `FAILED` por error 429.

`app/graph.py` resuelve esto con un semaforo y un intervalo minimo entre llamadas:
las peticiones a la API siguen siendo concurrentes, pero las llamadas al modelo se
serializan a nivel del proceso. Es el mismo patron que haria falta en produccion
para respetar el rate limit de cualquier proveedor.

## Estructura del proyecto

| Archivo | Contenido |
|---|---|
| `app/main.py` | Los cuatro endpoints de FastAPI. |
| `app/worker.py` | Ejecucion en segundo plano y maquina de estados en Redis. |
| `app/graph.py` | Orquestador multi-agente, limitador de ritmo y armado del grafo. |
| `app/hitl.py` | Nodo de aprobacion humana y criterio de criticidad. |
| `app/state.py` | Estado compartido del grafo. |
| `app/herramientas.py` | Herramientas de los agentes: historico, calculadora e indicadores. |
| `app/observability.py` | Inicializacion de LangSmith. |
| `app/config.py` | Variables de entorno con validacion al arranque. |
| `load_test.py` | Prueba de carga: 5 peticiones concurrentes. |
| `screenshots/` | Capturas del dashboard de observabilidad. |

## Como levantar el entorno

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt

Copiar `.env.example` a `.env` y completar las claves. Redis puede ser una
instancia local o una base gratuita en Redis Cloud; en ambos casos se configura
con `REDIS_HOST`, `REDIS_PORT` y `REDIS_PASSWORD`.

Levantar la API:

    uvicorn app.main:app --reload

La documentacion interactiva queda en `http://127.0.0.1:8000/docs`.

## Como lanzar las 5 peticiones concurrentes

Con la API corriendo, en otra terminal:

    python load_test.py

El script encola las 5 consultas en paralelo, mide la latencia del encolado y
hace polling hasta que todas alcanzan un estado final.

## Stack

Python 3.13 · FastAPI · Uvicorn · LangGraph · LangChain · Redis · LangSmith ·
Google Gemini · Pydantic · httpx · asyncio · type hints

