"""Orquestador multi-agente con persistencia en Redis y aprobacion humana."""

import asyncio
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from app import config
from app.herramientas import HERRAMIENTAS_ANALISIS, HERRAMIENTAS_INVESTIGACION
from app.hitl import es_critica, nodo_aprobacion_humana
from app.state import EstadoOrquestador

PROMPT_INVESTIGADOR = (
    "Sos el agente investigador de un equipo de ingenieria de mantenimiento. "
    "Tu unica funcion es obtener datos concretos del historico de los equipos de "
    "planta usando tus herramientas. NO hagas calculos ni interpretes indicadores. "
    "Respondes en espanol, en una sola linea, con los datos crudos encontrados."
)

PROMPT_ANALISTA = (
    "Sos el agente analista de un equipo de ingenieria de mantenimiento. "
    "Tu unica funcion es calcular indicadores a partir de datos que ya te dieron: "
    "MTBF, MTTR, disponibilidad, costo de indisponibilidad y criticidad. "
    "NO busques informacion nueva. Respondes en espanol, en una sola linea."
)

SUPERVISOR_PROMPT = """Sos el Supervisor de un equipo de ingenieria de mantenimiento
con dos especialistas a cargo:

- investigador: obtiene el historico de fallas y paradas de un equipo de planta.
- analista: calcula MTBF, MTTR, disponibilidad, costo de indisponibilidad y
  criticidad a partir de datos ya obtenidos.

Rubrica de validacion. Antes de responder FINISH, verifica que se cumplan TODAS:
1. El investigador ya aporto el historico del equipo consultado.
2. El analista ya aporto los indicadores, incluyendo disponibilidad y criticidad.
3. Ningun aporte contiene un mensaje de error o de dato faltante.

Reglas de delegacion:
- Si falta el historico del equipo, delega en 'investigador'.
- Si el historico ya esta pero faltan los indicadores, delega en 'analista'.
- Si un especialista devolvio un error, volve a delegarle una sola vez.
- Si se cumplen las tres condiciones, respondes 'FINISH'.
- Nunca delegues dos veces seguidas en el mismo agente si ya cumplio su parte.

Contribuciones registradas hasta ahora:
{contribuciones}
"""

# Limitador de ritmo para la capa gratuita de Gemini: permite una llamada a la
# vez y espera un intervalo minimo entre llamadas, de modo que varias tareas
# concurrentes no superen el limite de peticiones por minuto del proveedor.
_SEMAFORO_LLM = asyncio.Semaphore(1)
_INTERVALO_MINIMO_SEG = 13.0
_ultima_llamada = 0.0


async def _esperar_turno() -> None:
    """Espera hasta que haya pasado el intervalo minimo desde la ultima llamada."""
    global _ultima_llamada
    reloj = asyncio.get_running_loop().time
    espera = _INTERVALO_MINIMO_SEG - (reloj() - _ultima_llamada)
    if espera > 0:
        await asyncio.sleep(espera)
    _ultima_llamada = reloj()


class DecisionSupervisor(BaseModel):
    """Salida estructurada del supervisor: a quien delega y por que."""

    next: Literal["investigador", "analista", "FINISH"] = Field(
        description="Proximo agente a invocar, o FINISH si la tarea esta completa"
    )
    razon: str = Field(description="Breve justificacion de la decision tomada")


def construir_llm() -> ChatGoogleGenerativeAI:
    """Instancia el modelo de Gemini usado por todos los nodos del grafo."""
    return ChatGoogleGenerativeAI(
        model=config.MODEL_NAME,
        google_api_key=config.GEMINI_API_KEY,
    )


def _texto_plano(mensaje) -> str:
    """Extrae solo el texto de un mensaje, descartando metadatos del modelo."""
    contenido = mensaje.content
    if isinstance(contenido, str):
        return contenido
    partes: list[str] = []
    for bloque in contenido:
        if isinstance(bloque, dict) and bloque.get("type") == "text":
            partes.append(bloque["text"])
        elif isinstance(bloque, str):
            partes.append(bloque)
    return "\n".join(partes)


def _formatear_contribuciones(state: EstadoOrquestador) -> str:
    """Arma el resumen de aportes que el supervisor usa para decidir."""
    contribuciones = state.get("contribuciones", [])
    if not contribuciones:
        return "(ninguna todavia)"
    return "\n".join(f"- {c['agente']}: {c['aporte']}" for c in contribuciones)


async def nodo_supervisor(state: EstadoOrquestador) -> dict:
    """Router inteligente: valida la rubrica y decide el proximo paso."""
    if state.get("pasos", 0) >= config.MAX_PASOS:
        return {
            "next_agent": "FINISH",
            "task_completed": True,
            "messages": [
                AIMessage(
                    content=(
                        f"Se alcanzo el limite de {config.MAX_PASOS} delegaciones. "
                        "Se cierra con la informacion disponible."
                    ),
                    name="supervisor",
                )
            ],
        }

    llm_estructurado = construir_llm().with_structured_output(DecisionSupervisor)
    pregunta_original = state["messages"][0].content
    mensajes = [
        {
            "role": "system",
            "content": SUPERVISOR_PROMPT.format(
                contribuciones=_formatear_contribuciones(state)
            ),
        },
        {"role": "user", "content": f"Consulta original: {pregunta_original}"},
    ]

    async with _SEMAFORO_LLM:
        await _esperar_turno()
        decision = await llm_estructurado.ainvoke(mensajes)

    return {
        "next_agent": decision.next,
        "task_completed": decision.next == "FINISH",
        "messages": [
            AIMessage(
                content=f"[Supervisor -> {decision.next}] {decision.razon}",
                name="supervisor",
            )
        ],
    }


async def nodo_investigador(state: EstadoOrquestador) -> dict:
    """Ejecuta al investigador con contexto acotado a la consulta original."""
    agente = create_react_agent(
        model=construir_llm(),
        tools=HERRAMIENTAS_INVESTIGACION,
        prompt=PROMPT_INVESTIGADOR,
    )
    tarea = state["messages"][0].content

    async with _SEMAFORO_LLM:
        await _esperar_turno()
        resultado = await agente.ainvoke({"messages": [HumanMessage(content=tarea)]})

    respuesta = _texto_plano(resultado["messages"][-1])
    return {
        "messages": [AIMessage(content=respuesta, name="investigador")],
        "contribuciones": [{"agente": "investigador", "aporte": respuesta}],
        "pasos": state.get("pasos", 0) + 1,
    }


async def nodo_analista(state: EstadoOrquestador) -> dict:
    """Ejecuta al analista con la consulta y los datos ya recolectados."""
    agente = create_react_agent(
        model=construir_llm(),
        tools=HERRAMIENTAS_ANALISIS,
        prompt=PROMPT_ANALISTA,
    )
    tarea = (
        f"Consulta original: {state['messages'][0].content}\n\n"
        f"Datos aportados por el equipo:\n{_formatear_contribuciones(state)}\n\n"
        "Calcula los indicadores de mantenimiento con estos datos."
    )

    async with _SEMAFORO_LLM:
        await _esperar_turno()
        resultado = await agente.ainvoke({"messages": [HumanMessage(content=tarea)]})

    respuesta = _texto_plano(resultado["messages"][-1])
    return {
        "messages": [AIMessage(content=respuesta, name="analista")],
        "contribuciones": [{"agente": "analista", "aporte": respuesta}],
        "pasos": state.get("pasos", 0) + 1,
    }


async def nodo_sintesis(state: EstadoOrquestador) -> dict:
    """Fase final: combina los aportes en una recomendacion operativa."""
    aprobacion = state.get("aprobacion_humana")
    nota = ""
    if aprobacion == "rechazado":
        nota = (
            "\n\nIMPORTANTE: un supervisor humano RECHAZO esta recomendacion. "
            "Indica explicitamente que la intervencion no fue autorizada y que "
            "se requiere revision antes de ejecutar cualquier accion."
        )
    elif aprobacion == "aprobado":
        nota = (
            "\n\nUn supervisor humano APROBO la ejecucion de esta recomendacion. "
            "Indicalo al cierre del informe."
        )

    prompt = (
        f"Consulta original: {state['messages'][0].content}\n\n"
        f"Aportes del equipo:\n{_formatear_contribuciones(state)}\n\n"
        "Redacta la respuesta final para un supervisor de mantenimiento: breve, "
        "con los indicadores obtenidos y una recomendacion operativa concreta. "
        f"Responde en espanol.{nota}"
    )

    async with _SEMAFORO_LLM:
        await _esperar_turno()
        respuesta = await construir_llm().ainvoke(prompt)

    return {"messages": [AIMessage(content=_texto_plano(respuesta), name="sintesis")]}


def enrutar(
    state: EstadoOrquestador,
) -> Literal["investigador", "analista", "aprobacion", "sintesis"]:
    """Arista condicional: traduce la decision del supervisor a un nodo.

    Antes de cerrar, si la tarea quedo clasificada como critica y todavia no
    hubo decision humana, el flujo pasa por el nodo de aprobacion.
    """
    if state.get("next_agent") == "FINISH":
        if es_critica(state) and not state.get("aprobacion_humana"):
            return "aprobacion"
        return "sintesis"
    return state["next_agent"]


def construir_grafo(checkpointer):
    """Arma y compila el grafo del orquestador con el checkpointer dado."""
    grafo = StateGraph(EstadoOrquestador)
    grafo.add_node("supervisor", nodo_supervisor)
    grafo.add_node("investigador", nodo_investigador)
    grafo.add_node("analista", nodo_analista)
    grafo.add_node("aprobacion", nodo_aprobacion_humana)
    grafo.add_node("sintesis", nodo_sintesis)

    grafo.add_edge(START, "supervisor")
    grafo.add_conditional_edges(
        "supervisor",
        enrutar,
        {
            "investigador": "investigador",
            "analista": "analista",
            "aprobacion": "aprobacion",
            "sintesis": "sintesis",
        },
    )
    grafo.add_edge("investigador", "supervisor")
    grafo.add_edge("analista", "supervisor")
    grafo.add_edge("aprobacion", "sintesis")
    grafo.add_edge("sintesis", END)

    return grafo.compile(checkpointer=checkpointer)


def estado_inicial(consulta: str) -> dict:
    """Construye el estado de arranque para una consulta del usuario."""
    return {
        "messages": [HumanMessage(content=consulta)],
        "next_agent": None,
        "contribuciones": [],
        "pasos": 0,
        "task_completed": False,
        "requiere_aprobacion": False,
        "aprobacion_humana": None,
    }