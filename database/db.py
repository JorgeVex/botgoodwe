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

            if existente["estado"] == "Restaurado" and alarma.estado == "Ocurriendo":
                # Estaba cerrada (por sincronización o porque se restauró) pero la plataforma
                # la sigue mostrando como activa con la misma hora de inicio: se reabre.
                conn.execute("""
                    UPDATE alarmas
                    SET estado='Ocurriendo', fecha_resolucion=NULL, ultima_deteccion=?, duracion=?
                    WHERE id=?
                """, (ahora, alarma.duracion, existente["id"]))
                return "persistente"

            return "sin_cambios"

    def sincronizar_activas(self, alarmas_vigentes: List[Alarma]) -> List[sqlite3.Row]:
        """
        Cierra las alarmas que la base de datos tiene como 'Ocurriendo' pero que YA NO aparecen
        en la pestaña Occurring de la plataforma (Goodwe a veces las saca sin marcarlas
        'Restaurado'). No se borran: pasan a 'Restaurado' con fecha_resolucion, para conservar
        el historial. Devuelve las filas que se cerraron.

        IMPORTANTE: llamar solo tras una lectura exitosa de la tabla (ver main.py).
        """
        vigentes = {(a.sn, a.nombre_alarma, a.hora_alarma)
                    for a in alarmas_vigentes if a.estado == "Ocurriendo"}
        ahora = datetime.now().isoformat(timespec="seconds")

        with self.db.conectar() as conn:
            activas = conn.execute("SELECT * FROM alarmas WHERE estado='Ocurriendo'").fetchall()
            cerradas = [r for r in activas
                        if (r["sn"], r["nombre_alarma"], r["hora_alarma"]) not in vigentes]
            for r in cerradas:
                conn.execute(
                    "UPDATE alarmas SET estado='Restaurado', fecha_resolucion=?, ultima_deteccion=? WHERE id=?",
                    (ahora, ahora, r["id"]),
                )
        return cerradas

    def purgar_resueltas(self, dias: int = None) -> int:
        """Borra del historial las alarmas resueltas hace más de `dias` días. Devuelve cuántas."""
        dias = dias or getattr(settings, "dias_retencion_resueltas", 90)
        with self.db.conectar() as conn:
            cur = conn.execute("""
                DELETE FROM alarmas
                WHERE estado='Restaurado'
                  AND fecha_resolucion IS NOT NULL
                  AND julianday('now', 'localtime') - julianday(fecha_resolucion) > ?
            """, (dias,))
            return cur.rowcount

    def actualizar_detalle(self, alarma: Alarma) -> None:
        """
        Guarda razón y sugerencia (ya traducidas) de una alarma. Hay que llamarlo DESPUÉS de
        scraper.completar_detalle_y_curva(), porque procesar_lectura() se ejecuta antes de
        abrir el detalle y por eso la base de datos las guardaba vacías.
        """
        with self.db.conectar() as conn:
            conn.execute("""
                UPDATE alarmas
                SET razon=COALESCE(?, razon), sugerencia=COALESCE(?, sugerencia)
                WHERE sn=? AND nombre_alarma=? AND hora_alarma=?
            """, (alarma.razon, alarma.sugerencia, alarma.sn, alarma.nombre_alarma, alarma.hora_alarma))

    def obtener_activas(self) -> List[sqlite3.Row]:
        """
        Alarmas que siguen ocurriendo. Agrega dos columnas calculadas:
          situacion  -> 'Nueva' si se detectó hoy, 'Persistente' si viene de días anteriores
          dias_activa -> días desde la primera detección del bot
        Orden: nuevas primero y, dentro de cada grupo, la más reciente primero.
        """
        with self.db.conectar() as conn:
            return conn.execute("""
                SELECT *,
                    CASE WHEN date(primera_deteccion) = date('now', 'localtime')
                         THEN 'Nueva' ELSE 'Persistente' END AS situacion,
                    CAST(julianday('now', 'localtime') - julianday(primera_deteccion) AS INTEGER) AS dias_activa
                FROM alarmas
                WHERE estado='Ocurriendo'
                ORDER BY (date(primera_deteccion) = date('now', 'localtime')) DESC,
                         ultima_deteccion DESC
            """).fetchall()

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