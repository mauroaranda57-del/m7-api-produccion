"""Esquema de estado compartido entre el supervisor y los agentes especialistas."""

import operator
from typing import Annotated, Optional, TypedDict

from langgraph.graph import MessagesState


class Contribucion(TypedDict):
    """Un aporte individual de un agente especialista."""

    agente: str
    aporte: str


class EstadoOrquestador(MessagesState):
    """Estado compartido del sistema multi-agente.

    Hereda `messages` de MessagesState y suma:

    - `next_agent`: a quien delega el supervisor en el proximo paso.
    - `contribuciones`: registro acumulado de que agente aporto que dato,
      con `operator.add` como reducer para que los aportes se sumen en vez
      de pisarse.
    - `pasos`: contador de delegaciones, para cortar bucles infinitos.
    - `task_completed`: bandera que el supervisor levanta al validar el cierre.
    - `requiere_aprobacion`: marca que la tarea fue clasificada como critica
      y necesita intervencion humana antes de cerrar.
    - `aprobacion_humana`: decision recibida desde el endpoint de aprobacion.
    """

    next_agent: Optional[str]
    contribuciones: Annotated[list[Contribucion], operator.add]
    pasos: int
    task_completed: bool
    requiere_aprobacion: bool
    aprobacion_humana: Optional[str]