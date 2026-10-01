"""Herramientas de los agentes especialistas del orquestador."""

import ast
import operator as op

from langchain_core.tools import tool

HISTORICO_FALLAS: dict[str, dict[str, object]] = {
    "C-02": {
        "equipo": "Compresor de tornillo C-02",
        "periodo": "ultimos 12 meses",
        "horas_operacion": 7200,
        "cantidad_fallas": 6,
        "horas_parada_total": 48,
        "costo_hora_parada_usd": 850,
    },
    "B-07": {
        "equipo": "Bomba centrifuga B-07",
        "periodo": "ultimos 12 meses",
        "horas_operacion": 8000,
        "cantidad_fallas": 2,
        "horas_parada_total": 9,
        "costo_hora_parada_usd": 320,
    },
}

_OPERADORES = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Pow: op.pow,
    ast.USub: op.neg,
}


def _evaluar_nodo(nodo: ast.AST) -> float:
    """Evalua recursivamente un nodo del arbol sintactico, solo aritmetica."""
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, (int, float)):
        return nodo.value
    if isinstance(nodo, ast.BinOp) and type(nodo.op) in _OPERADORES:
        return _OPERADORES[type(nodo.op)](
            _evaluar_nodo(nodo.left), _evaluar_nodo(nodo.right)
        )
    if isinstance(nodo, ast.UnaryOp) and type(nodo.op) in _OPERADORES:
        return _OPERADORES[type(nodo.op)](_evaluar_nodo(nodo.operand))
    raise ValueError("Expresion no permitida")


@tool
def consultar_historico_fallas(equipo_id: str) -> str:
    """Devuelve el historico de fallas y paradas de un equipo de planta.

    Usar cuando haga falta conocer cuantas fallas tuvo un equipo, cuantas horas
    estuvo parado, cuantas horas opero o cuanto cuesta su hora de indisponibilidad.
    El parametro equipo_id es el codigo del equipo, por ejemplo 'C-02' o 'B-07'.
    """
    clave = equipo_id.strip().upper()
    datos = HISTORICO_FALLAS.get(clave)
    if datos is None:
        disponibles = ", ".join(HISTORICO_FALLAS.keys())
        return (
            f"No hay historico cargado para el equipo '{equipo_id}'. "
            f"Los equipos con historico disponible son: {disponibles}."
        )
    return (
        f"Equipo {clave} ({datos['equipo']}) | Periodo: {datos['periodo']} | "
        f"Horas de operacion: {datos['horas_operacion']} | "
        f"Cantidad de fallas: {datos['cantidad_fallas']} | "
        f"Horas de parada acumuladas: {datos['horas_parada_total']} | "
        f"Costo por hora de parada: USD {datos['costo_hora_parada_usd']}"
    )


@tool
def calculadora(expresion: str) -> str:
    """Evalua una expresion matematica con +, -, *, /, ** y parentesis.

    Usar para cualquier calculo numerico. Ejemplo de entrada: '7200 / 6'.
    No acepta variables, funciones ni nombres: solo numeros y operadores.
    """
    try:
        resultado = _evaluar_nodo(ast.parse(expresion, mode="eval").body)
        return f"Resultado: {round(resultado, 4)}"
    except ZeroDivisionError:
        return f"Error al calcular '{expresion}': division por cero"
    except Exception as error:
        return f"Error al calcular '{expresion}': {error}"


@tool
def calcular_indicadores_mantenimiento(
    horas_operacion: float,
    cantidad_fallas: int,
    horas_parada_total: float,
    costo_hora_parada_usd: float,
) -> str:
    """Calcula los indicadores de mantenimiento de un equipo y su criticidad.

    Usar cuando ya se cuente con el historico de un equipo y haya que evaluarlo.
    Calcula MTBF, MTTR, disponibilidad porcentual y costo de indisponibilidad,
    y clasifica la criticidad segun la disponibilidad obtenida.
    """
    if cantidad_fallas <= 0:
        return "No se puede calcular el MTBF: la cantidad de fallas debe ser mayor a cero."
    if horas_operacion <= 0:
        return "No se puede calcular: las horas de operacion deben ser mayores a cero."

    mtbf = horas_operacion / cantidad_fallas
    mttr = horas_parada_total / cantidad_fallas
    disponibilidad = horas_operacion / (horas_operacion + horas_parada_total) * 100
    costo_total = horas_parada_total * costo_hora_parada_usd

    if disponibilidad >= 99:
        criticidad = "baja"
    elif disponibilidad >= 97:
        criticidad = "media"
    else:
        criticidad = "alta"

    return (
        f"MTBF: {mtbf:.1f} h entre fallas | "
        f"MTTR: {mttr:.1f} h por reparacion | "
        f"Disponibilidad: {disponibilidad:.2f} % | "
        f"Costo de indisponibilidad: USD {costo_total:,.0f} | "
        f"Criticidad: {criticidad}"
    )


HERRAMIENTAS_INVESTIGACION = [consultar_historico_fallas]
HERRAMIENTAS_ANALISIS = [calculadora, calcular_indicadores_mantenimiento]