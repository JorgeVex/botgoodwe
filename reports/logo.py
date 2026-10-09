"""reports/logo.py - Ubicación única del logo de SunnyApp (la usan el Excel y el PDF)."""
import logging
from pathlib import Path

from config.settings import settings

log = logging.getLogger(__name__)

# En orden de prioridad, relativo a la raíz del proyecto (settings.base_dir), no al directorio de ejecución.
RUTAS_LOGO = [
    Path("IMG") / "sunnyapp.jpg",
    Path("IMG") / "sunnyapp.jpeg",
    Path("IMG") / "sunnyapp.png",
    Path("assets") / "logo_sunnyapp.png",
]


def buscar_logo(preferido=None) -> Path | None:
    """Devuelve la ruta del logo, o None (con advertencia) si no existe; el reporte sigue sin imagen."""
    raiz = Path(settings.base_dir)
    candidatos = ([Path(preferido)] if preferido else []) + [raiz / r for r in RUTAS_LOGO]
    for ruta in candidatos:
        if ruta.is_file():
            return ruta
    log.warning("Logo de SunnyApp no encontrado. Se buscó en: %s", [str(c) for c in candidatos])
    return None