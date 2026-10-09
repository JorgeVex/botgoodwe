from dataclasses import dataclass
from typing import Optional


@dataclass
class Alarma:
    """Representa una alarma extraída de la plataforma Goodwe en una lectura del bot."""

    sn: str
    nombre_planta: str
    nombre_alarma: str
    equipo: str
    tipo_alarma: str
    nivel: str              # "Alarma" o "Fallo"
    estado: str              # "Ocurriendo" o "Restaurado"
    hora_alarma: str
    nombre_alarma_es: Optional[str] = None
    duracion: Optional[str] = None
    recovery_time: Optional[str] = None
    confirm_status: Optional[str] = None
    razon: Optional[str] = None
    sugerencia: Optional[str] = None
    ruta_curva: Optional[str] = None   # no se guarda en BD, solo se usa para el PDF del ciclo actual