"""Prueba de carga: lanza 5 peticiones concurrentes contra la API."""

import asyncio
import time

import httpx

URL_BASE = "http://127.0.0.1:8000"

CONSULTAS = [
    "Analiza la criticidad del compresor C-02 segun su historico del ultimo anio.",
    "Analiza la criticidad de la bomba centrifuga B-07 segun su historico.",
    "Que indicadores de mantenimiento tiene el compresor C-02?",
    "Calcula el MTBF y la disponibilidad de la bomba B-07.",
    "Necesito el analisis de criticidad del equipo C-02 con recomendacion.",
]


async def lanzar(cliente: httpx.AsyncClient, consulta: str) -> dict:
    """Encola una tarea y devuelve el job_id junto con la latencia del encolado."""
    inicio = time.perf_counter()
    respuesta = await cliente.post(f"{URL_BASE}/tasks", json={"consulta": consulta})
    latencia = time.perf_counter() - inicio
    datos = respuesta.json()
    return {
        "job_id": datos["job_id"],
        "estado": datos["estado"],
        "latencia_encolado_seg": round(latencia, 4),
        "consulta": consulta,
    }


async def main() -> None:
    """Dispara las 5 peticiones en paralelo y hace polling hasta que terminen."""
    async with httpx.AsyncClient(timeout=30) as cliente:
        print("Lanzando 5 peticiones concurrentes...\n")
        inicio_total = time.perf_counter()

        resultados = await asyncio.gather(
            *(lanzar(cliente, consulta) for consulta in CONSULTAS)
        )

        encolado_total = time.perf_counter() - inicio_total
        for r in resultados:
            print(f"  {r['job_id']} -> {r['estado']} "
                  f"({r['latencia_encolado_seg']} s)")
        print(f"\nLas 5 encoladas en {encolado_total:.3f} s "
              f"(la API no se bloqueo esperando a los agentes).\n")

        print("Esperando a que los agentes terminen...\n")
        pendientes = {r["job_id"] for r in resultados}
        finales: dict[str, dict] = {}

        while pendientes:
            await asyncio.sleep(10)
            for job_id in list(pendientes):
                respuesta = await cliente.get(f"{URL_BASE}/tasks/{job_id}")
                job = respuesta.json()
                estado = job.get("estado")
                if estado in ("DONE", "FAILED", "WAITING_APPROVAL"):
                    finales[job_id] = job
                    pendientes.discard(job_id)
                    print(f"  {job_id} -> {estado} "
                          f"({job.get('duracion_seg')} s)")

        print(f"\nTiempo total: {time.perf_counter() - inicio_total:.1f} s")
        print("Revisa las trazas, el costo y la latencia p95 en LangSmith.")


if __name__ == "__main__":
    asyncio.run(main())