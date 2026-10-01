"""Capa de observabilidad: envia las trazas de cada ejecucion a LangSmith."""

import os

from app import config


def init_observabilidad() -> bool:
    """Activa el tracing de LangSmith si hay una clave configurada.

    LangChain y LangGraph instrumentan sus propias llamadas cuando estas
    variables de entorno estan presentes, de modo que cada nodo del grafo
    aparece como un span dentro de la traza del run.

    Devuelve True si el tracing quedo activo.
    """
    if not config.LANGSMITH_API_KEY:
        print("[observabilidad] LANGSMITH_API_KEY no configurada: tracing desactivado.")
        return False

    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_ENDPOINT"] = "https://api.smith.langchain.com"
    os.environ["LANGSMITH_API_KEY"] = config.LANGSMITH_API_KEY
    os.environ["LANGSMITH_PROJECT"] = config.LANGSMITH_PROJECT

    print(f"[observabilidad] tracing activo — proyecto: {config.LANGSMITH_PROJECT}")
    return True