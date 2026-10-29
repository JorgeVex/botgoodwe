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
                    resuelta_avisada INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(sn, nombre_alarma, hora_alarma)
                )
            """)
            # Bases creadas antes de esta versión: agregar la columna si falta
            columnas = {f["name"] for f in conn.execute("PRAGMA table_info(alarmas)")}
            if "resuelta_avisada" not in columnas:
                conn.execute("ALTER TABLE alarmas ADD COLUMN resuelta_avisada INTEGER NOT NULL DEFAULT 0")


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
                    SET estado='Ocurriendo', fecha_resolucion=NULL, resuelta_avisada=0, ultima_deteccion=?, duracion=?
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

    # ------------------------------------------------------------------
    # Seguimiento de alarmas resueltas (aparecen UNA sola vez, en los días de resumen)
    # ------------------------------------------------------------------
    def obtener_resueltas_pendientes(self, dias: int = 7) -> List[sqlite3.Row]:
        """
        Alarmas resueltas en los últimos `dias` días que todavía NO se han informado en un reporte.
        Orden: la resuelta más recientemente primero.
        """
        with self.db.conectar() as conn:
            return conn.execute("""
                SELECT * FROM alarmas
                WHERE estado='Restaurado'
                  AND resuelta_avisada=0
                  AND fecha_resolucion IS NOT NULL
                  AND julianday('now', 'localtime') - julianday(fecha_resolucion) <= ?
                ORDER BY fecha_resolucion DESC
            """, (dias,)).fetchall()

    def marcar_resueltas_avisadas(self, ids: List[int]) -> None:
        """Marca como informadas las resueltas ya incluidas en un reporte enviado (no vuelven a salir)."""
        if not ids:
            return
        with self.db.conectar() as conn:
            conn.executemany("UPDATE alarmas SET resuelta_avisada=1 WHERE id=?", [(i,) for i in ids])

    def descartar_resueltas_viejas(self, dias: int = 7) -> int:
        """Las resueltas hace más de `dias` días que nunca se informaron se dan por vistas: no se muestran tarde."""
        with self.db.conectar() as conn:
            cur = conn.execute("""
                UPDATE alarmas SET resuelta_avisada=1
                WHERE estado='Restaurado' AND resuelta_avisada=0
                  AND (fecha_resolucion IS NULL
                       OR julianday('now', 'localtime') - julianday(fecha_resolucion) > ?)
            """, (dias,))
            return cur.rowcount

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
        dias_rec = getattr(settings, "dias_recurrencia", 30)
        # "Previa" = misma alarma (mismo equipo y nombre) ya resuelta en los últimos N días.
        previa = """
            FROM alarmas p
            WHERE p.sn = a.sn AND p.nombre_alarma = a.nombre_alarma AND p.id <> a.id
              AND p.estado = 'Restaurado' AND p.fecha_resolucion IS NOT NULL
              AND julianday('now', 'localtime') - julianday(p.fecha_resolucion) <= :dias
        """
        with self.db.conectar() as conn:
            return conn.execute(f"""
                SELECT a.*,
                    CASE WHEN date(a.primera_deteccion) <> date('now', 'localtime') THEN 'Persistente'
                         WHEN EXISTS (SELECT 1 {previa}) THEN 'Recurrente'
                         ELSE 'Nueva' END AS situacion,
                    1 + (SELECT COUNT(*) {previa}) AS apariciones,
                    CAST(julianday('now', 'localtime') - julianday(a.primera_deteccion) AS INTEGER) AS dias_activa
                FROM alarmas a
                WHERE a.estado='Ocurriendo'
                ORDER BY CASE WHEN date(a.primera_deteccion) <> date('now', 'localtime') THEN 2
                              WHEN EXISTS (SELECT 1 {previa}) THEN 1 ELSE 0 END,
                         a.ultima_deteccion DESC
            """, {"dias": dias_rec}).fetchall()

    @staticmethod
    def agrupar_resueltas(filas) -> list:
        """
        Agrupa las resueltas por (equipo, alarma): una fila por alarma con cuántas veces se resolvió.
        Devuelve dicts con los datos de la ocurrencia más reciente + 'apariciones' + 'ids' (todas las
        filas agrupadas, para marcarlas como informadas). Orden: la resuelta más recientemente primero.
        """
        grupos: dict = {}
        for r in filas:
            d = {k: r[k] for k in r.keys()}
            clave = (d["sn"], d["nombre_alarma"])
            g = grupos.get(clave)
            if g is None:
                d["apariciones"], d["ids"] = 1, [d["id"]]
                grupos[clave] = d
                continue
            g["apariciones"] += 1
            g["ids"].append(d["id"])
            if str(d.get("fecha_resolucion") or "") > str(g.get("fecha_resolucion") or ""):
                d["apariciones"], d["ids"] = g["apariciones"], g["ids"]
                grupos[clave] = d
        return sorted(grupos.values(), key=lambda g: str(g.get("fecha_resolucion") or ""), reverse=True)

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