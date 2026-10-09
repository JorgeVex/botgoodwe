"""
Traducción inglés -> español de lo que muestra Goodwe, para los usuarios del reporte.

- Nombres y tipos de alarma: catálogo fijo del fabricante -> diccionario propio (exacto y predecible).
- Texto libre (razón y sugerencia): primero diccionario de frases conocidas; si no está,
  traducción automática (deep-translator) con caché en disco, y si falla se deja el original.
"""
import json
import logging
import re

from config.settings import settings

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Nombres de alarma (columna "Alarm Name")
# ----------------------------------------------------------------------
TRADUCCIONES = {
    "CT Loss": "Pérdida de TC",
    "PE Loss": "Pérdida de PE",
    "Grid Outage": "Fallo en la red",
    "Grid Power Outage": "Corte de energía de la red",
    "Grid Frequency Out of Range": "Frecuencia de red fuera de rango",
    "Grid Voltage Out of Range": "Tensión de red fuera de rango",
}

# ----------------------------------------------------------------------
# Tipos de alarma (columna "Alarm Type")
# ----------------------------------------------------------------------
TRADUCCIONES_TIPO = {
    "Protection Events": "Eventos de protección",
    "Operating Condition Information": "Información de condición de operación",
}

# ----------------------------------------------------------------------
# Frases completas de razón / sugerencia que ya tengas traducidas a mano
# (la clave se compara sin distinguir mayúsculas ni espacios de más).
# Ejemplo:  "check the grid voltage.": "Verifique la tensión de la red.",
# ----------------------------------------------------------------------
TRADUCCIONES_TEXTO: dict[str, str] = {}

_CACHE_PATH = settings.base_dir / "data" / "traducciones_cache.json"
_cache: dict[str, str] | None = None
_traductor = None  # se crea la primera vez que hace falta


def _norm(txt: str) -> str:
    return re.sub(r"\s+", " ", txt or "").strip()


def traducir_alarma(nombre_en: str) -> str | None:
    """Devuelve la traducción conocida, o None si el término aún no está mapeado."""
    clave = _norm(nombre_en)
    traduccion = TRADUCCIONES.get(clave)
    if traduccion is None and clave:
        log.warning("Alarma sin traducir (agregar a TRADUCCIONES): %r", clave)
    return traduccion


def traducir_tipo(tipo_en: str) -> str:
    """Tipo de alarma en español; si no está mapeado devuelve el original."""
    clave = _norm(tipo_en)
    traduccion = TRADUCCIONES_TIPO.get(clave)
    if traduccion is None and clave and clave != "-":
        log.warning("Tipo de alarma sin traducir (agregar a TRADUCCIONES_TIPO): %r", clave)
    return traduccion or tipo_en


def _cargar_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _guardar_cache():
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        log.warning("No se pudo guardar la caché de traducciones: %s", e)


def traducir_texto(texto_en: str | None) -> str | None:
    """
    Traduce razón / sugerencia al español. Orden: diccionario -> caché -> traducción
    automática. Si nada funciona, devuelve el texto original (el reporte nunca se rompe).
    """
    global _traductor
    texto = _norm(texto_en)
    if not texto:
        return texto_en

    if texto.lower() in {k.lower() for k in TRADUCCIONES_TEXTO}:
        return next(v for k, v in TRADUCCIONES_TEXTO.items() if k.lower() == texto.lower())

    cache = _cargar_cache()
    if texto in cache:
        return cache[texto]

    try:
        if _traductor is None:
            from deep_translator import GoogleTranslator
            _traductor = GoogleTranslator(source="en", target="es")
        traducido = _traductor.translate(texto)
        if traducido:
            cache[texto] = traducido
            _guardar_cache()
            return traducido
    except Exception as e:  # noqa: BLE001  (sin internet, librería no instalada, límite, etc.)
        log.warning("No se pudo traducir el texto (se deja en inglés): %s", e)
    return texto_en