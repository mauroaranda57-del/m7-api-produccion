"""Worker: ejecuta el grafo en segundo plano y persiste el estado en Redis."""

import json
import time
from enum import StrEnum
from typing import Any

import redis.asyncio as aioredis
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.types import Command

from app import config
from app.graph import construir_grafo, estado_inicial


class EstadoJob(StrEnum):
    """Estados posibles de un trabajo encolado."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    DONE = "DONE"
    FAILED = "FAILED"


def clave_job(job_id: str) -> str:
    """Clave con la que se guarda el job en Redis."""
    return f"job:{job_id}"


async def obtener_cliente() -> aioredis.Redis:
    """Cliente asincrono de Redis para el estado de los jobs."""
    return aioredis.from_url(config.redis_url(), decode_responses=True)


async def guardar_job(job_id: str, datos: dict[str, Any]) -> None:
    """Escribe el estado del job en Redis con vencimiento."""
    cliente = await obtener_cliente()
    try:
        await cliente.set(
            clave_job(job_id),
            json.dumps(datos, ensure_ascii=False),
            ex=config.TTL_JOB_SEGUNDOS,
        )
    finally:
        await cliente.aclose()


async def leer_job(job_id: str) -> dict[str, Any] | None:
    """Recupera el estado del job desde Redis."""
    cliente = await obtener_cliente()
    try:
        crudo = await cliente.get(clave_job(job_id))
    finally:
        await cliente.aclose()
    return json.loads(crudo) if crudo else None


async def actualizar_job(job_id: str, **campos: Any) -> dict[str, Any]:
    """Modifica algunos campos del job sin pisar el resto."""
    actual = await leer_job(job_id) or {"job_id": job_id}
    actual.update(campos)
    await guardar_job(job_id, actual)
    return actual


def _serializar_mensajes(resultado: dict[str, Any]) -> list[dict[str, str]]:
    """Convierte los mensajes del estado final en algo apto para JSON."""
    salida: list[dict[str, str]] = []
    for mensaje in resultado.get("messages", []):
        contenido = mensaje.content
        if not isinstance(contenido, str):
            partes = [
                b["text"]
                for b in contenido
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            contenido = "\n".join(partes)
        salida.append(
            {"autor": getattr(mensaje, "name", "sistema"), "contenido": contenido}
        )
    return salida


async def ejecutar_job(job_id: str, consulta: str) -> None:
    """Corre el grafo completo para una consulta y persiste el resultado.

    Cualquier excepcion se captura y deja el job en FAILED, de modo que el
    cliente que hace polling no quede esperando indefinidamente.
    """
    inicio = time.perf_counter()
    await actualizar_job(job_id, estado=EstadoJob.RUNNING, consulta=consulta)

    try:
        async with AsyncRedisSaver.from_conn_string(config.redis_url()) as saver:
            await saver.asetup()
            app_grafo = construir_grafo(saver)
            configuracion = {
                "configurable": {"thread_id": job_id},
                "recursion_limit": config.RECURSION_LIMIT,
            }
            resultado = await app_grafo.ainvoke(
                estado_inicial(consulta), config=configuracion
            )

            if "__interrupt__" in resultado:
                interrupcion = resultado["__interrupt__"][0]
                await actualizar_job(
                    job_id,
                    estado=EstadoJob.WAITING_APPROVAL,
                    solicitud_aprobacion=interrupcion.value,
                    duracion_seg=round(time.perf_counter() - inicio, 2),
                )
                return

            await actualizar_job(
                job_id,
                estado=EstadoJob.DONE,
                mensajes=_serializar_mensajes(resultado),
                contribuciones=resultado.get("contribuciones", []),
                pasos=resultado.get("pasos", 0),
                duracion_seg=round(time.perf_counter() - inicio, 2),
            )

    except Exception as error:
        await actualizar_job(
            job_id,
            estado=EstadoJob.FAILED,
            error=f"{type(error).__name__}: {error}",
            duracion_seg=round(time.perf_counter() - inicio, 2),
        )


async def reanudar_job(job_id: str, decision: str) -> None:
    """Reanuda un grafo detenido en el nodo de aprobacion humana."""
    inicio = time.perf_counter()
    await actualizar_job(job_id, estado=EstadoJob.RUNNING, aprobacion=decision)

    try:
        async with AsyncRedisSaver.from_conn_string(config.redis_url()) as saver:
            await saver.asetup()
            app_grafo = construir_grafo(saver)
            configuracion = {
                "configurable": {"thread_id": job_id},
                "recursion_limit": config.RECURSION_LIMIT,
            }
            resultado = await app_grafo.ainvoke(
                Command(resume=decision), config=configuracion
            )

            await actualizar_job(
                job_id,
                estado=EstadoJob.DONE,
                mensajes=_serializar_mensajes(resultado),
                contribuciones=resultado.get("contribuciones", []),
                pasos=resultado.get("pasos", 0),
                duracion_seg=round(time.perf_counter() - inicio, 2),
            )

    except Exception as error:
        await actualizar_job(
            job_id,
            estado=EstadoJob.FAILED,
            error=f"{type(error).__name__}: {error}",
        )