"""Configuracion central de la API: variables de entorno y validacion al arranque."""

import os

from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")

REDIS_HOST: str | None = os.getenv("REDIS_HOST")
REDIS_PORT: str | None = os.getenv("REDIS_PORT")
REDIS_PASSWORD: str | None = os.getenv("REDIS_PASSWORD")

LANGSMITH_API_KEY: str | None = os.getenv("LANGSMITH_API_KEY")
LANGSMITH_PROJECT: str = os.getenv("LANGSMITH_PROJECT", "m7-orquestador-api")

MODEL_NAME: str = "gemini-3.5-flash-lite"
MAX_PASOS: int = 6
RECURSION_LIMIT: int = 15

COSTO_APROBACION_USD: float = 20000.0
TTL_JOB_SEGUNDOS: int = 60 * 60 * 24


def redis_url() -> str:
    """Arma la URL de conexion a Redis a partir de las piezas del .env."""
    return f"redis://default:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}"


def validar_entorno() -> None:
    """Corta el arranque si falta alguna variable obligatoria."""
    obligatorias = (
        ("GEMINI_API_KEY", GEMINI_API_KEY),
        ("REDIS_HOST", REDIS_HOST),
        ("REDIS_PORT", REDIS_PORT),
        ("REDIS_PASSWORD", REDIS_PASSWORD),
    )
    faltantes = [nombre for nombre, valor in obligatorias if not valor]
    if faltantes:
        raise RuntimeError(
            f"Faltan variables de entorno en el archivo .env: {', '.join(faltantes)}"
        )


validar_entorno()