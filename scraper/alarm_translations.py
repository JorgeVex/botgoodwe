"""
Diccionario de traducción de nombres de alarma de Goodwe (inglés -> español).
Se usa un diccionario propio en vez de un servicio de traducción externo porque
son un catálogo fijo y relativamente pequeño de términos del fabricante, no texto libre.
"""

TRADUCCIONES = {
    "CT Loss": "Pérdida de TC",
    "PE Loss": "Pérdida de PE",
    "Grid Outage": "Fallo en la red",
    "Grid Frequency Out of Range": "Frecuencia de red fuera de rango",
    "Grid Voltage Out of Range": "Tensión de red fuera de rango",
}


def traducir_alarma(nombre_en: str) -> str | None:
    """Devuelve la traducción conocida, o None si el término aún no está mapeado."""
    return TRADUCCIONES.get(nombre_en.strip())