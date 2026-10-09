"""
reports/excel_report.py - Reporte diario de alarmas de SunnyApp (plataforma GoodWe).

Recibe las filas de AlarmaRepository.obtener_activas() (sqlite3.Row o dict) y genera un .xlsx con:
  - Hoja "Reporte de alarmas": encabezado con logo, indicadores y tabla de Excel con filtros.
  - Hoja "Resumen ejecutivo": tablas y gráficos por planta, tipo y estado.

Interfaz que usa main.py (no cambia):  ExcelReportGenerator(alarmas).generar() -> ruta (str)
"""
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import openpyxl
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.table import Table, TableStyleInfo

from config.settings import settings
from reports.logo import buscar_logo
from scraper.alarm_translations import traducir_alarma, traducir_tipo

log = logging.getLogger(__name__)

# ======================================================================
# CONFIGURACIÓN (modificar aquí; nada está "quemado" en el resto del código)
# ======================================================================
ZONA_HORARIA = "America/Bogota"

# Valores del campo `nivel` que se consideran CRÍTICOS. Viene de la plataforma
# ("Fault" -> "Fallo"); no se deduce del nombre de la alarma.
NIVELES_CRITICOS = {"Fallo"}

# Una alarma activa con más días que esto se resalta en la columna Duración.
UMBRAL_DURACION_ALTA_DIAS = 7

# Textos de sugerencia ya APROBADOS por el equipo técnico, por nombre de alarma (en español o
# inglés). Solo se usan cuando la plataforma no entregó sugerencia. Ejemplo:
#   "Pérdida de TC": "Revisar el cableado y la polaridad del TC (informativo).",
SUGERENCIAS_APROBADAS: dict[str, str] = {}
SUGERENCIA_POR_DEFECTO = "Requiere revisión técnica"

# Paleta SunnyApp
AZUL = "548DD4"
NARANJA = "FF7417"
AZUL_OSCURO = "17365D"
VERDE_SUAVE = "E2F0D9"
AMARILLO_SUAVE = "FFF2CC"
ROJO_SUAVE = "FADBD8"
ROJO_TEXTO = "C00000"
GRIS_CLARO = "F2F2F2"
BLANCO = "FFFFFF"
FUENTE = "Calibri"

HOJA_PRINCIPAL = "Reporte de alarmas"
HOJA_RESUMEN = "Resumen ejecutivo"
NOMBRE_TABLA = "TablaAlarmas"

# (encabezado, ancho de columna)
COLUMNAS = [
    ("Hora de alarma", 21),
    ("Nombre de la planta", 36),
    ("SN", 22),
    ("Equipo", 20),
    ("Tipo de alarma", 28),
    ("Nombre de la alarma", 32),
    ("Estado", 14),
    ("Situación", 15),
    ("Duración", 20),
    ("Sugerencia", 46),
]
CAMPOS_REQUERIDOS = ("sn", "nombre_planta", "nombre_alarma", "hora_alarma", "estado")
FILA_ENCABEZADO_TABLA = 10


def _zona_horaria():
    """
    America/Bogota. En Windows Python no trae la base de zonas horarias (falta el paquete 'tzdata');
    como Colombia no tiene horario de verano, se usa UTC-5 fijo en ese caso.
    """
    try:
        return ZoneInfo(ZONA_HORARIA)
    except ZoneInfoNotFoundError:
        log.warning("Sin base de zonas horarias (pip install tzdata); se usa UTC-05:00 fijo.")
        return timezone(timedelta(hours=-5))


# ======================================================================
# Utilidades de datos (sin dependencias de openpyxl: fáciles de probar)
# ======================================================================
def _a_dict(fila) -> dict:
    """sqlite3.Row o dict -> dict (sqlite3.Row no tiene .get)."""
    if isinstance(fila, dict):
        return fila
    try:
        return {k: fila[k] for k in fila.keys()}
    except AttributeError:
        return dict(fila)


def _texto(valor, defecto="No disponible") -> str:
    valor = "" if valor is None else str(valor).strip()
    return valor if valor and valor != "-" else defecto


def parsear_fecha(texto) -> datetime | None:
    """Convierte 'dd/mm/yyyy hh:mm:ss' (formato de SEMS+) a datetime; None si no es válida."""
    if isinstance(texto, datetime):
        return texto
    for formato in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(texto).strip(), formato)
        except (ValueError, TypeError):
            continue
    return None


_RE_DURACION = re.compile(r"(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?!s))?\s*(?:(\d+)\s*s)?", re.I)


def parsear_duracion(texto) -> timedelta | None:
    """'42d 7h 39m 28s' -> timedelta. None si el texto no tiene un formato reconocible."""
    texto = (str(texto) if texto is not None else "").strip()
    if not texto or texto == "-":
        return None
    m = _RE_DURACION.fullmatch(texto)
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(g) if g else 0 for g in m.groups())
    return timedelta(days=d, hours=h, minutes=mi, seconds=s)


def formatear_duracion(delta: timedelta | None, original=None) -> str:
    """Formato legible que admite más de 24 h: '2 d 05 h 30 min' o '05 h 30 min'."""
    if delta is None:
        return _texto(original)
    total_min = int(delta.total_seconds() // 60)
    dias, resto = divmod(total_min, 1440)
    horas, minutos = divmod(resto, 60)
    if dias:
        return f"{dias} d {horas:02d} h {minutos:02d} min"
    return f"{horas:02d} h {minutos:02d} min"


def sugerencia_para(alarma: dict, nombre_es: str) -> str:
    """Prioridad: sugerencia de la plataforma -> aprobada por el equipo -> 'Requiere revisión técnica'."""
    de_plataforma = _texto(alarma.get("sugerencia"), "")
    if de_plataforma:
        return de_plataforma
    for clave in (nombre_es, alarma.get("nombre_alarma")):
        if clave in SUGERENCIAS_APROBADAS:
            return SUGERENCIAS_APROBADAS[clave]
    return SUGERENCIA_POR_DEFECTO


def preparar_registros(alarmas) -> list[dict]:
    """Normaliza las filas de la BD a registros listos para escribir (una sola vez, sin duplicar lógica)."""
    registros = []
    for i, fila in enumerate(alarmas or [], start=1):
        a = _a_dict(fila)
        faltantes = [c for c in CAMPOS_REQUERIDOS if not _texto(a.get(c), "")]
        if faltantes:
            log.warning("Alarma #%d con campos requeridos vacíos %s: se incluye igualmente.", i, faltantes)

        nombre_en = _texto(a.get("nombre_alarma"))
        nombre_es = a.get("nombre_alarma_es") or traducir_alarma(nombre_en) or nombre_en
        hora = parsear_fecha(a.get("hora_alarma"))
        if hora is None and _texto(a.get("hora_alarma"), ""):
            log.warning("Fecha inválida en alarma %s (%r): se muestra como texto.", a.get("sn"), a.get("hora_alarma"))

        delta = parsear_duracion(a.get("duracion"))
        estado = _texto(a.get("estado"))
        registros.append({
            "hora": hora,
            "hora_texto": _texto(a.get("hora_alarma")),
            "planta": _texto(a.get("nombre_planta")),
            "sn": _texto(a.get("sn")),
            "equipo": _texto(a.get("equipo")),
            "tipo": traducir_tipo(a["tipo_alarma"]) if _texto(a.get("tipo_alarma"), "") else "No disponible",
            "nombre": nombre_es,
            "estado": estado,
            "situacion": _texto(a.get("situacion"), "-"),
            "delta": delta,
            "duracion": formatear_duracion(delta, a.get("duracion")),
            "sugerencia": sugerencia_para(a, nombre_es),
            "critica": _texto(a.get("nivel"), "") in NIVELES_CRITICOS,
            "activa": estado == "Ocurriendo",
        })
    # Nuevas primero; dentro de cada grupo, la más reciente primero (fechas inválidas al final)
    registros.sort(key=lambda r: (r["situacion"] != "Nueva", -(r["hora"].timestamp() if r["hora"] else 0)))
    return registros


# ======================================================================
# Generador
# ======================================================================
class ExcelReportGenerator:
    """Genera el reporte diario de alarmas activas en formato Excel."""

    def __init__(self, alarmas, logo_path=None, ruta_salida=None):
        """
        alarmas: filas de AlarmaRepository.obtener_activas().
        logo_path: opcional; por defecto se busca <raíz>/IMG/sunnyapp.jpg (ver reports/logo.py).
        ruta_salida: carpeta de destino; por defecto settings.excel_dir.
        """
        self.ahora = datetime.now(_zona_horaria())
        self.registros = preparar_registros(alarmas)
        self.logo_path = logo_path
        self.carpeta = Path(ruta_salida) if ruta_salida else Path(settings.excel_dir)

        lado = Side(style="thin", color="BFBFBF")
        self.borde = Border(left=lado, right=lado, top=lado, bottom=lado)
        self.wb = openpyxl.Workbook()
        self.ws = self.wb.active
        self.ws.title = HOJA_PRINCIPAL
        self.ws.sheet_view.showGridLines = False

    # ------------------------------------------------------------------
    def generar(self) -> str:
        self._anchos(self.ws, [w for _, w in COLUMNAS])
        self._encabezado()
        self._indicadores()
        self._tabla()
        self._configurar_impresion(self.ws, f"A1:{get_column_letter(len(COLUMNAS))}{self._ultima_fila}",
                                   repetir=f"{FILA_ENCABEZADO_TABLA}:{FILA_ENCABEZADO_TABLA}")
        try:
            self._hoja_resumen()
        except Exception:  # la hoja principal debe sobrevivir a un fallo del análisis
            log.exception("No se pudo generar la hoja 'Resumen ejecutivo'; se entrega solo la hoja principal.")
            if HOJA_RESUMEN in self.wb.sheetnames:
                del self.wb[HOJA_RESUMEN]

        ruta = self._ruta_disponible()
        try:
            self.carpeta.mkdir(parents=True, exist_ok=True)
            self.wb.save(ruta)
        except OSError:
            log.exception("No se pudo guardar el Excel en %s", ruta)
            raise
        log.info("Excel generado: %s (%d alarmas)", ruta, len(self.registros))
        return str(ruta)

    def _ruta_disponible(self) -> Path:
        """SunnyApp_Reporte_Alarmas_2026-10-08.xlsx; si existe, agrega la hora y, si hace falta, un contador."""
        base = f"SunnyApp_Reporte_Alarmas_{self.ahora:%Y-%m-%d}"
        ruta = self.carpeta / f"{base}.xlsx"
        if ruta.exists():
            ruta = self.carpeta / f"{base}_{self.ahora:%H%M}.xlsx"
        n = 2
        while ruta.exists():
            ruta = self.carpeta / f"{base}_{self.ahora:%H%M}_{n}.xlsx"
            n += 1
        return ruta

    # ------------------------------------------------------------------
    # Encabezado
    # ------------------------------------------------------------------
    def _insertar_logo(self):
        ruta = buscar_logo(self.logo_path)
        if ruta is None:
            return
        try:
            from PIL import Image as PILImage
            with PILImage.open(ruta) as im:
                ancho, alto = im.size
            alto_obj = 96                                  # px; cabe en las filas 1-4
            img = XLImage(str(ruta))
            img.height = alto_obj
            img.width = round(ancho * alto_obj / alto)     # conserva las proporciones
            self.ws.add_image(img, "A1")
        except Exception:  # noqa: BLE001  imagen corrupta o formato no soportado
            log.exception("No se pudo insertar el logo %s; se continúa sin imagen.", ruta)

    def _encabezado(self):
        ws, ult = self.ws, get_column_letter(len(COLUMNAS))
        self._insertar_logo()

        z = self.ahora.strftime("%z")                      # '-0500' -> 'UTC-05:00'
        zona = f"{ZONA_HORARIA} (UTC{z[:3]}:{z[3:]})"
        textos = [
            (1, "SUNNY APP  |  REPORTE DIARIO DE ALARMAS", Font(name=FUENTE, size=20, bold=True, color=NARANJA), 34),
            (2, "Plataforma GoodWe", Font(name=FUENTE, size=13, bold=True, color=AZUL_OSCURO), 22),
            (3, f"Fecha del reporte: {self.ahora:%d/%m/%Y}   |   Generado: {self.ahora:%H:%M:%S}   |   "
                f"Zona horaria: {zona}", Font(name=FUENTE, size=10, color="404040"), 18),
            (4, "Período de consulta: alarmas activas al momento de la lectura en la plataforma",
             Font(name=FUENTE, size=10, italic=True, color="595959"), 18),
        ]
        for fila, texto, fuente, alto in textos:
            ws.merge_cells(f"B{fila}:{ult}{fila}")
            c = ws[f"B{fila}"]
            c.value, c.font = texto, fuente
            c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
            ws.row_dimensions[fila].height = alto
        # línea naranja de cierre del encabezado
        for col in range(1, len(COLUMNAS) + 1):
            ws.cell(row=5, column=col).border = Border(top=Side(style="medium", color=NARANJA))
        ws.row_dimensions[5].height = 8

    # ------------------------------------------------------------------
    # Indicadores
    # ------------------------------------------------------------------
    def _indicadores(self):
        ws = self.ws
        activas = [r for r in self.registros if r["activa"]]
        criticas = [r for r in activas if r["critica"]]
        resueltas = [r for r in self.registros if r["estado"] == "Restaurado"]
        plantas = {r["planta"] for r in activas}
        con_duracion = [r for r in activas if r["delta"] is not None]
        mayor = max(con_duracion, key=lambda r: r["delta"], default=None)
        texto_mayor = f"{mayor['duracion']}  ·  {mayor['nombre']}" if mayor else "No disponible"

        # (etiqueta, valor, columna inicial, columna final, color de fondo, color de texto)
        tarjetas = [
            ("Total de registros", len(self.registros), 1, 1, GRIS_CLARO, AZUL_OSCURO),
            ("Alarmas activas", len(activas), 2, 2, AZUL, BLANCO),
            ("Críticas activas", len(criticas), 3, 4, ROJO_SUAVE if criticas else VERDE_SUAVE,
             ROJO_TEXTO if criticas else "2E7D32"),
            ("Resueltas", len(resueltas), 5, 5, VERDE_SUAVE, "2E7D32"),
            ("Plantas afectadas", len(plantas), 6, 7, GRIS_CLARO, AZUL_OSCURO),
            ("Mayor duración activa", texto_mayor, 8, len(COLUMNAS), AMARILLO_SUAVE, "7F6000"),
        ]
        for etiqueta, valor, c1, c2, fondo, texto in tarjetas:
            if c2 > c1:
                for fila in (7, 8):
                    ws.merge_cells(start_row=fila, start_column=c1, end_row=fila, end_column=c2)
            lab, val = ws.cell(row=7, column=c1), ws.cell(row=8, column=c1)
            lab.value, val.value = etiqueta, valor
            lab.font = Font(name=FUENTE, size=9, bold=True, color=texto)
            val.font = Font(name=FUENTE, size=11 if isinstance(valor, str) else 22, bold=True, color=texto)
            for celda in (lab, val):
                celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            for fila in (7, 8):
                for col in range(c1, c2 + 1):
                    c = ws.cell(row=fila, column=col)
                    c.fill = PatternFill("solid", fgColor=fondo)
                    c.border = Border(left=Side(style="thin", color=BLANCO), right=Side(style="thin", color=BLANCO))
        ws.row_dimensions[6].height = 8
        ws.row_dimensions[7].height = 20
        ws.row_dimensions[8].height = 40
        ws.row_dimensions[9].height = 12

    # ------------------------------------------------------------------
    # Tabla principal
    # ------------------------------------------------------------------
    def _tabla(self):
        ws, hr = self.ws, FILA_ENCABEZADO_TABLA
        n_cols = len(COLUMNAS)
        for i, (titulo, _) in enumerate(COLUMNAS, start=1):
            c = ws.cell(row=hr, column=i, value=titulo)
            c.font = Font(name=FUENTE, bold=True, color=BLANCO, size=11)
            c.fill = PatternFill("solid", fgColor=AZUL)
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border = self.borde
        ws.row_dimensions[hr].height = 30

        if not self.registros:
            ws.merge_cells(start_row=hr + 1, start_column=1, end_row=hr + 1, end_column=n_cols)
            c = ws.cell(row=hr + 1, column=1, value="Sin alarmas activas en esta lectura.")
            c.font = Font(name=FUENTE, size=12, bold=True, color="2E7D32")
            c.fill = PatternFill("solid", fgColor=VERDE_SUAVE)
            c.alignment = Alignment(horizontal="center", vertical="center")
            ws.row_dimensions[hr + 1].height = 32
            self._ultima_fila = hr + 1
            ws.freeze_panes = f"A{hr + 1}"
            return

        for fila, r in enumerate(self.registros, start=hr + 1):
            valores = [r["hora"] or r["hora_texto"], r["planta"], r["sn"], r["equipo"], r["tipo"], r["nombre"],
                       r["estado"], r["situacion"], r["duracion"], r["sugerencia"]]
            fondo = ROJO_SUAVE if (r["critica"] and r["activa"]) else (GRIS_CLARO if fila % 2 else BLANCO)
            for col, valor in enumerate(valores, start=1):
                c = ws.cell(row=fila, column=col, value=valor)
                c.font = Font(name=FUENTE, size=10)
                c.fill = PatternFill("solid", fgColor=fondo)
                c.border = self.borde
                c.alignment = Alignment(horizontal="left" if col in (2, 10) else "center",
                                        vertical="center", wrap_text=True)
            ws.cell(row=fila, column=1).number_format = "dd/mm/yyyy hh:mm:ss"
            ws.cell(row=fila, column=3).number_format = "@"          # SN siempre como texto

            est = ws.cell(row=fila, column=7)
            if r["activa"]:
                est.font = Font(name=FUENTE, size=10, bold=True, color=ROJO_TEXTO)
            elif r["estado"] == "Restaurado":
                est.font = Font(name=FUENTE, size=10, bold=True, color="2E7D32")
                est.fill = PatternFill("solid", fgColor=VERDE_SUAVE)

            sit = ws.cell(row=fila, column=8)
            if r["situacion"] == "Nueva":
                sit.font = Font(name=FUENTE, size=10, bold=True, color=NARANJA)
            elif r["situacion"] == "Persistente":
                sit.font = Font(name=FUENTE, size=10, bold=True, color=AZUL_OSCURO)

            nombre = ws.cell(row=fila, column=6)
            if r["critica"]:
                nombre.font = Font(name=FUENTE, size=10, bold=True, color=ROJO_TEXTO)
            elif r["activa"]:                                   # advertencia
                nombre.fill = PatternFill("solid", fgColor=AMARILLO_SUAVE)

            if r["activa"] and r["delta"] and r["delta"].days >= UMBRAL_DURACION_ALTA_DIAS:
                dur = ws.cell(row=fila, column=9)
                dur.fill = PatternFill("solid", fgColor=AMARILLO_SUAVE)
                dur.font = Font(name=FUENTE, size=10, bold=True, color="7F6000")

            if r["sugerencia"] == SUGERENCIA_POR_DEFECTO:
                ws.cell(row=fila, column=10).font = Font(name=FUENTE, size=10, italic=True, color="7F7F7F")

            ws.row_dimensions[fila].height = self._alto_fila(valores)

        self._ultima_fila = hr + len(self.registros)
        tabla = Table(displayName=NOMBRE_TABLA, ref=f"A{hr}:{get_column_letter(n_cols)}{self._ultima_fila}")
        tabla.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=False)
        ws.add_table(tabla)                        # activa los filtros automáticos
        ws.freeze_panes = f"A{hr + 1}"

    @staticmethod
    def _alto_fila(valores) -> float:
        """Altura según el texto más largo respecto al ancho de su columna (Excel no autoajusta al abrir)."""
        lineas = 1
        for (_, ancho), v in zip(COLUMNAS, valores):
            texto = v.strftime("%d/%m/%Y %H:%M:%S") if isinstance(v, datetime) else str(v)
            lineas = max(lineas, -(-len(texto) // max(int(ancho * 1.05), 1)))
        return max(30, 14.5 * lineas + 8)

    # ------------------------------------------------------------------
    # Hoja de análisis
    # ------------------------------------------------------------------
    def _hoja_resumen(self):
        if not self.registros:
            return
        ws = self.wb.create_sheet(HOJA_RESUMEN)
        ws.sheet_view.showGridLines = False
        self._anchos(ws, [42, 14, 4, 40, 14, 4, 30, 14])

        ws.merge_cells("A1:H1")
        ws["A1"] = "SUNNY APP  |  RESUMEN EJECUTIVO DE ALARMAS"
        ws["A1"].font = Font(name=FUENTE, size=18, bold=True, color=NARANJA)
        ws["A1"].alignment = Alignment(vertical="center", indent=1)
        ws.row_dimensions[1].height = 32
        ws.merge_cells("A2:H2")
        ws["A2"] = f"Plataforma GoodWe  ·  {self.ahora:%d/%m/%Y %H:%M} ({ZONA_HORARIA})"
        ws["A2"].font = Font(name=FUENTE, size=10, color="595959")
        ws["A2"].alignment = Alignment(indent=1)

        activas = [r for r in self.registros if r["activa"]]
        por_planta = Counter(r["planta"] for r in activas).most_common(10)
        por_tipo = Counter(r["tipo"] for r in activas).most_common(10)
        por_estado = Counter(f"{r['estado']} · {r['situacion']}" for r in self.registros).most_common()
        antiguas = sorted((r for r in activas if r["delta"]), key=lambda r: r["delta"], reverse=True)[:5]

        fila_tablas = 4
        self._mini_tabla(ws, fila_tablas, 1, "Alarmas activas por planta (top 10)", "Planta", por_planta)
        self._mini_tabla(ws, fila_tablas, 4, "Distribución por tipo de alarma", "Tipo", por_tipo)
        self._mini_tabla(ws, fila_tablas, 7, "Distribución por estado", "Estado · situación", por_estado)

        alto_tabla = max(len(por_planta), len(por_tipo), len(por_estado)) + 2
        fila_graf = fila_tablas + alto_tabla + 1
        self._grafico(ws, "Alarmas activas por planta", fila_tablas, 1, len(por_planta), f"A{fila_graf}", AZUL)
        self._grafico(ws, "Alarmas por tipo", fila_tablas, 4, len(por_tipo), f"D{fila_graf}", NARANJA)

        fila_ant = fila_graf + 17
        ws.merge_cells(start_row=fila_ant, start_column=1, end_row=fila_ant, end_column=8)
        c = ws.cell(row=fila_ant, column=1, value="Alarmas activas más antiguas (top 5)")
        c.font = Font(name=FUENTE, size=12, bold=True, color=AZUL_OSCURO)
        for j, h in ((1, "Planta"), (2, "Duración"), (4, "Alarma"), (5, "Equipo"), (7, "SN")):
            self._celda_cabecera(ws, fila_ant + 1, j, h)
        for k, r in enumerate(antiguas, start=fila_ant + 2):
            for j, v in ((1, r["planta"]), (2, r["duracion"]), (4, r["nombre"]), (5, r["equipo"]), (7, r["sn"])):
                c = ws.cell(row=k, column=j, value=v)
                c.font = Font(name=FUENTE, size=10)
                c.border = self.borde
                c.alignment = Alignment(vertical="center", wrap_text=True, horizontal="left" if j == 1 else "center")
            ws.row_dimensions[k].height = 30
        fila_fin = self._nota_lectura(ws, fila_ant + 3 + len(antiguas))
        self._configurar_impresion(ws, f"A1:H{fila_fin}")

    # Texto de ayuda al final de la hoja de resumen (editable aquí)
    NOTA_TITULO = "Cómo leer este resumen"
    NOTA_PUNTOS = [
        "Es una foto de las alarmas que estaban activas en la plataforma GoodWe en el momento de la lectura.",
        "Por planta: muestra dónde se concentran las alarmas. Una planta con varias alarmas debe revisarse primero.",
        "Por tipo: los \"Eventos de protección\" suelen requerir atención más rápida. La \"Información de condición "
        "de operación\" son avisos del equipo (por ejemplo, pérdida de PE o de TC) que el equipo técnico debe "
        "validar en sitio; no confirman por sí solos un daño físico.",
        "Nueva / Persistente: \"Nueva\" significa que el sistema la detectó por primera vez hoy; \"Persistente\", que "
        "ya venía de lecturas anteriores. No indica cuándo empezó la alarma: eso lo dice la columna Duración.",
        "Alarmas más antiguas: llevan más tiempo sin resolverse; son la prioridad de seguimiento.",
        "Las alarmas resueltas salen automáticamente del reporte y no se muestran aquí.",
    ]

    def _nota_lectura(self, ws, fila) -> int:
        """Escribe la nota de ayuda y devuelve la última fila usada."""
        ws.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=8)
        t = ws.cell(row=fila, column=1, value=self.NOTA_TITULO)
        t.font = Font(name=FUENTE, size=12, bold=True, color=AZUL_OSCURO)
        for i, texto in enumerate(self.NOTA_PUNTOS, start=fila + 1):
            ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=8)
            c = ws.cell(row=i, column=1, value=f"•  {texto}")
            c.font = Font(name=FUENTE, size=10, color="404040")
            c.alignment = Alignment(wrap_text=True, vertical="center", indent=1)
            c.fill = PatternFill("solid", fgColor=GRIS_CLARO)
            ws.row_dimensions[i].height = 18 * max(1, -(-len(texto) // 150)) + 4
        return fila + len(self.NOTA_PUNTOS)

    def _celda_cabecera(self, ws, fila, col, texto):
        c = ws.cell(row=fila, column=col, value=texto)
        c.font = Font(name=FUENTE, size=10, bold=True, color=BLANCO)
        c.fill = PatternFill("solid", fgColor=AZUL_OSCURO)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = self.borde

    def _mini_tabla(self, ws, fila, col, titulo, encabezado, datos):
        ws.cell(row=fila, column=col, value=titulo).font = Font(name=FUENTE, size=11, bold=True, color=AZUL_OSCURO)
        self._celda_cabecera(ws, fila + 1, col, encabezado)
        self._celda_cabecera(ws, fila + 1, col + 1, "Cantidad")
        for i, (etiqueta, cantidad) in enumerate(datos, start=fila + 2):
            a = ws.cell(row=i, column=col, value=etiqueta)
            b = ws.cell(row=i, column=col + 1, value=cantidad)
            for c in (a, b):
                c.font = Font(name=FUENTE, size=10)
                c.border = self.borde
                c.fill = PatternFill("solid", fgColor=GRIS_CLARO if i % 2 else BLANCO)
            a.alignment = Alignment(wrap_text=True, vertical="center")
            b.alignment = Alignment(horizontal="center", vertical="center")

    @staticmethod
    def _grafico(ws, titulo, fila_tabla, col, n, ancla, color):
        if n == 0:
            return
        g = BarChart()
        g.type = "bar"                                   # barras horizontales
        g.title = titulo
        g.style = 10
        g.legend = None
        g.height, g.width = 7.5, 11.5
        datos = Reference(ws, min_col=col + 1, min_row=fila_tabla + 1, max_row=fila_tabla + 1 + n)
        cats = Reference(ws, min_col=col, min_row=fila_tabla + 2, max_row=fila_tabla + 1 + n)
        g.add_data(datos, titles_from_data=True)
        g.set_categories(cats)
        g.series[0].graphicalProperties.solidFill = color
        g.dataLabels = DataLabelList()
        g.dataLabels.showVal = True
        g.dataLabels.showCatName = g.dataLabels.showSerName = g.dataLabels.showLegendKey = False
        g.dataLabels.showPercent = False
        g.y_axis.majorGridlines = None
        g.y_axis.delete = False
        g.x_axis.delete = False
        g.x_axis.scaling.orientation = "maxMin"          # la mayor arriba
        ws.add_chart(g, ancla)

    # ------------------------------------------------------------------
    # Presentación / impresión
    # ------------------------------------------------------------------
    @staticmethod
    def _anchos(ws, anchos):
        for i, w in enumerate(anchos, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

    @staticmethod
    def _configurar_impresion(ws, area, repetir=None):
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_LETTER
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0                    # varias páginas de alto
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        ws.print_area = area
        if repetir:
            ws.print_title_rows = repetir
        ws.page_margins.left = ws.page_margins.right = 0.4
        ws.page_margins.top, ws.page_margins.bottom = 0.5, 0.6
        ws.print_options.horizontalCentered = True
        ws.oddFooter.left.text = "SunnyApp · Reporte diario de alarmas"
        ws.oddFooter.right.text = "Página &P de &N"
        for parte in (ws.oddFooter.left, ws.oddFooter.right):
            parte.size, parte.font = 9, FUENTE