import sqlite3
from datetime import datetime
from typing import List

from config.settings import settings
from database.models import Alarma


class Database:
    """Maneja la conexión y la creación del esquema de la base de datos."""

    def __init__(self, db_path=None):
        self.db_path = db_path or settings.db_path
        self._crear_esquema()

    def conectar(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _crear_esquema(self):
        with self.conectar() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alarmas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sn TEXT NOT NULL,
                    nombre_planta TEXT NOT NULL,
                    nombre_alarma TEXT NOT NULL,
                    nombre_alarma_es TEXT,
                    equipo TEXT,
                    tipo_alarma TEXT,
                    nivel TEXT,
                    estado TEXT NOT NULL,
                    hora_alarma TEXT NOT NULL,
                    duracion TEXT,
                    recovery_time TEXT,
                    confirm_status TEXT,
                    razon TEXT,
                    sugerencia TEXT,
                    primera_deteccion TEXT NOT NULL,
                    ultima_deteccion TEXT NOT NULL,
                    fecha_resolucion TEXT,
                    UNIQUE(sn, nombre_alarma, hora_alarma)
                )
            """)


class AlarmaRepository:
    """Encapsula la lógica de comparación de estado: nueva / persistente / resuelta."""

    def __init__(self, database: Database = None):
        self.db = database or Database()

    def procesar_lectura(self, alarma: Alarma) -> str:
        """
        Compara la alarma recién leída contra lo guardado y actualiza la base de datos.
        Devuelve: 'nueva', 'persistente', 'resuelta' o 'sin_cambios'.
        """
        ahora = datetime.now().isoformat(timespec="seconds")

        with self.db.conectar() as conn:
            existente = conn.execute(
                "SELECT * FROM alarmas WHERE sn=? AND nombre_alarma=? AND hora_alarma=?",
                (alarma.sn, alarma.nombre_alarma, alarma.hora_alarma),
            ).fetchone()

            if existente is None:
                conn.execute("""
                    INSERT INTO alarmas
                        (sn, nombre_planta, nombre_alarma, nombre_alarma_es, equipo, tipo_alarma,
                         nivel, estado, hora_alarma, duracion, recovery_time, confirm_status,
                         razon, sugerencia, primera_deteccion, ultima_deteccion)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    alarma.sn, alarma.nombre_planta, alarma.nombre_alarma, alarma.nombre_alarma_es,
                    alarma.equipo, alarma.tipo_alarma, alarma.nivel, alarma.estado, alarma.hora_alarma,
                    alarma.duracion, alarma.recovery_time, alarma.confirm_status,
                    alarma.razon, alarma.sugerencia, ahora, ahora,
                ))
                return "nueva"

            if existente["estado"] == "Ocurriendo" and alarma.estado == "Restaurado":
                conn.execute("""
                    UPDATE alarmas
                    SET estado=?, ultima_deteccion=?, fecha_resolucion=?, duracion=?, recovery_time=?
                    WHERE id=?
                """, (alarma.estado, ahora, ahora, alarma.duracion, alarma.recovery_time, existente["id"]))
                return "resuelta"

            if existente["estado"] == "Ocurriendo" and alarma.estado == "Ocurriendo":
                conn.execute("""
                    UPDATE alarmas SET ultima_deteccion=?, duracion=? WHERE id=?
                """, (ahora, alarma.duracion, existente["id"]))
                return "persistente"

            return "sin_cambios"

    def obtener_activas(self) -> List[sqlite3.Row]:
        with self.db.conectar() as conn:
            return conn.execute(
                "SELECT * FROM alarmas WHERE estado='Ocurriendo' ORDER BY ultima_deteccion DESC"
            ).fetchall()

    def obtener_persistentes(self, dias_minimo: int = None) -> List[sqlite3.Row]:
        dias_minimo = dias_minimo or settings.dias_alarma_cronica
        with self.db.conectar() as conn:
            return conn.execute("""
                SELECT *, julianday('now') - julianday(primera_deteccion) AS dias_activa
                FROM alarmas
                WHERE estado='Ocurriendo'
                  AND julianday('now') - julianday(primera_deteccion) >= ?
                ORDER BY dias_activa DESC
            """, (dias_minimo,)).fetchall()