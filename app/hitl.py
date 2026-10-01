"""Nodo de aprobacion humana (human-in-the-loop).

El grafo se detiene en este nodo cuando la tarea fue clasificada como critica.
La ejecucion no continua hasta que alguien responda desde el endpoint
POST /tasks/{job_id}/approve.
"""

import re

from langgraph.types import interrupt

from app.state import EstadoOrquestador

# Captura montos en dolares redactados de cualquier forma por el modelo:
# "USD 40,800", "USD 40800", "40,800 USD", "$40.800".
_PATRON_MONTO = re.compile(
    r"(?:USD|\$)\s*([\d][\d.,]*)|([\d][\d.,]*)\s*(?:USD|dolares)",
    re.IGNORECASE,
)


def _a_numero(texto: str) -> float | None:
    """Convierte un monto escrito con separadores de miles a float."""
    limpio = texto.strip().rstrip(".,")
    # Se descartan los separadores de miles, sean comas o puntos.
    limpio = limpio.replace(",", "").replace(".", "")
    try:
        return float(limpio)
    except ValueError:
        return None


def es_critica(state: EstadoOrquestador) -> bool:
    """Clasifica la tarea como critica segun el costo detectado en los aportes.

    Se considera critica cuando algun aporte del analista menciona un monto
    por encima del umbral configurado, porque a partir de ahi la recomendacion
    implica decisiones de parada de planta con impacto economico alto.

    La deteccion no depende de la redaccion exacta del modelo: se buscan todos
    los montos en dolares del aporte y se compara el mayor contra el umbral.
    """
    from app import config

    for contribucion in state.get("contribuciones", []):
        if contribucion.get("agente") != "analista":
            continue
        for coincidencia in _PATRON_MONTO.finditer(contribucion["aporte"]):
            crudo = coincidencia.group(1) or coincidencia.group(2)
            monto = _a_numero(crudo)
            if monto is not None and monto >= config.COSTO_APROBACION_USD:
                return True
    return False


def nodo_aprobacion_humana(state: EstadoOrquestador) -> dict:
    """Detiene la ejecucion y espera una decision humana externa.

    `interrupt()` guarda el estado en el checkpointer de Redis y corta el run.
    Cuando el endpoint de aprobacion reanuda el grafo con un Command(resume=...),
    la ejecucion vuelve exactamente a este punto y el valor recibido queda en
    `decision`.
    """
    resumen = "\n".join(
        f"- [{c['agente']}] {c['aporte']}" for c in state.get("contribuciones", [])
    )

    decision = interrupt(
        {
            "motivo": (
                "La tarea fue clasificada como critica por el costo de "
                "indisponibilidad involucrado. Requiere aprobacion de un "
                "supervisor de mantenimiento antes de emitir la recomendacion."
            ),
            "aportes": resumen,
            "respuestas_validas": ["aprobado", "rechazado"],
        }
    )

    return {
        "aprobacion_humana": str(decision),
        "requiere_aprobacion": False,
    }