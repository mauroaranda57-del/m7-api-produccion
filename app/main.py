"""API FastAPI asincrona sobre el orquestador multi-agente."""

import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.observability import init_observabilidad
from app.worker import EstadoJob, actualizar_job, ejecutar_job, leer_job, reanudar_job


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    """Enciende la observabilidad al arrancar la aplicacion."""
    init_observabilidad()
    yield


app = FastAPI(
    title="API del orquestador multi-agente de mantenimiento",
    description=(
        "Expone el sistema multi-agente de analisis de criticidad como una API "
        "asincrona, con estado en Redis, trazas en LangSmith y aprobacion humana "
        "para las tareas clasificadas como criticas."
    ),
    version="1.0.0",
    lifespan=ciclo_de_vida,
)


class SolicitudTarea(BaseModel):
    """Cuerpo del pedido para encolar una nueva tarea."""

    consulta: str = Field(
        description="Consulta en lenguaje natural para el orquestador",
        examples=["Analiza la criticidad del compresor C-02 segun su historico."],
    )


class RespuestaEncolado(BaseModel):
    """Respuesta inmediata al encolar: no espera a que la tarea termine."""

    job_id: str
    estado: str


class SolicitudAprobacion(BaseModel):
    """Decision humana sobre una tarea detenida en el nodo de aprobacion."""

    decision: str = Field(
        description="Decision del supervisor humano: 'aprobado' o 'rechazado'",
        examples=["aprobado"],
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Chequeo rapido de que la API esta viva."""
    return {"estado": "ok"}


@app.post("/tasks", response_model=RespuestaEncolado, status_code=202)
async def crear_tarea(
    solicitud: SolicitudTarea, tareas: BackgroundTasks
) -> RespuestaEncolado:
    """Encola una tarea y devuelve su identificador sin bloquear.

    El grafo corre en segundo plano: el endpoint responde apenas registra el
    job en Redis, de modo que el event loop nunca queda esperando al LLM.
    """
    job_id = str(uuid.uuid4())
    await actualizar_job(
        job_id,
        estado=EstadoJob.PENDING,
        consulta=solicitud.consulta,
    )
    tareas.add_task(ejecutar_job, job_id, solicitud.consulta)
    return RespuestaEncolado(job_id=job_id, estado=EstadoJob.PENDING)


@app.get("/tasks/{job_id}")
async def consultar_tarea(job_id: str) -> dict:
    """Devuelve el estado actual de un job desde Redis."""
    job = await leer_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No existe el job '{job_id}'.")
    return job


@app.post("/tasks/{job_id}/approve")
async def aprobar_tarea(
    job_id: str, solicitud: SolicitudAprobacion, tareas: BackgroundTasks
) -> dict:
    """Recibe la decision humana y reanuda el grafo detenido.

    Solo tiene efecto sobre un job que quedo en WAITING_APPROVAL: el grafo
    retoma desde el punto exacto donde se interrumpio, usando el checkpoint
    guardado en Redis.
    """
    job = await leer_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No existe el job '{job_id}'.")

    if job.get("estado") != EstadoJob.WAITING_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail=(
                f"El job esta en estado '{job.get('estado')}' y no espera aprobacion."
            ),
        )

    decision = solicitud.decision.strip().lower()
    if decision not in ("aprobado", "rechazado"):
        raise HTTPException(
            status_code=422,
            detail="La decision debe ser 'aprobado' o 'rechazado'.",
        )

    tareas.add_task(reanudar_job, job_id, decision)
    return {"job_id": job_id, "estado": EstadoJob.RUNNING, "decision": decision}