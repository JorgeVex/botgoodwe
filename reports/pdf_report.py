import logging
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from config.settings import settings
from reports.logo import buscar_logo

log = logging.getLogger(__name__)


class PDFReportGenerator:
    """
    Genera UN solo PDF por ciclo: una página de resumen y, a continuación,
    una ficha por alarma (datos, razón, sugerencia y curva de potencia activa).
    """

    HEX_ROJO = "#C0392B"
    HEX_AMBAR = "#B7791F"
    NARANJA = colors.HexColor("#E8722C")
    GRIS_OSC = colors.HexColor("#3A3A3A")
    GRIS_CLARO = colors.HexColor("#F2F2F2")

    def __init__(self, alarmas: list[dict], logo_path=None):
        """
        alarmas: lista de diccionarios con los datos de cada alarma. Claves esperadas:
        nombre_alarma, nombre_alarma_es, nombre_planta, sn, equipo, tipo_alarma, nivel,
        estado, hora_alarma, duracion, recovery_time, confirm_status, razon, sugerencia,
        curva_path, clasificacion ('nueva' o 'persistente').
        """
        self.alarmas = self._ordenar(alarmas)
        self.logo_path = buscar_logo(logo_path)   # IMG/sunnyapp.jpg (ver reports/logo.py); None si no existe
        self.fecha = datetime.now()
        self._crear_estilos()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def generar(self) -> str:
        ruta = settings.pdf_dir / f"reporte_alarmas_{self.fecha.strftime('%Y%m%d_%H%M')}.pdf"

        doc = SimpleDocTemplate(
            str(ruta), pagesize=letter,
            topMargin=3 * cm, bottomMargin=1.8 * cm, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
            title="Reporte de alarmas - Goodwe", author="Sunny App",
        )

        elementos = self._resumen()
        for idx, alarma in enumerate(self.alarmas, start=1):
            elementos.append(PageBreak())
            elementos += self._ficha(idx, alarma)

        doc.build(elementos, onFirstPage=self._decorar_pagina, onLaterPages=self._decorar_pagina)
        return str(ruta)

    # ------------------------------------------------------------------
    # Orden y estilos
    # ------------------------------------------------------------------
    @staticmethod
    def _ordenar(alarmas):
        """Fallos primero, luego las nuevas antes que las persistentes, luego por planta."""
        return sorted(
            alarmas,
            key=lambda a: (a["nivel"] != "Fallo", a.get("clasificacion") != "nueva", a["nombre_planta"]),
        )

    def _crear_estilos(self):
        base = getSampleStyleSheet()["Normal"]
        self.s_h1 = ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=16,
                                    textColor=self.NARANJA, spaceAfter=10)
        self.s_titulo = ParagraphStyle("titulo", parent=base, fontName="Helvetica-Bold", fontSize=15,
                                        leading=19, textColor=self.GRIS_OSC, spaceAfter=2)
        self.s_sub = ParagraphStyle("sub", parent=base, fontSize=8.5, textColor=colors.grey, spaceAfter=4)
        self.s_label = ParagraphStyle("label", parent=base, fontName="Helvetica-Bold", fontSize=8.5,
                                       textColor=self.NARANJA)
        self.s_valor = ParagraphStyle("valor", parent=base, fontSize=9, leading=12)
        self.s_texto = ParagraphStyle("texto", parent=base, fontSize=10, leading=14, spaceAfter=4)
        self.s_bloque_header = ParagraphStyle("bloque_header", parent=base, fontName="Helvetica-Bold",
                                               fontSize=10, leading=16, textColor=colors.white,
                                               backColor=self.GRIS_OSC, borderPadding=(2, 4, 2, 4),
                                               spaceBefore=10, spaceAfter=6)
        self.s_celda = ParagraphStyle("celda", parent=base, fontSize=8, leading=10)
        self.s_celda_head = ParagraphStyle("celda_head", parent=self.s_celda, fontName="Helvetica-Bold",
                                            textColor=colors.white)

    # ------------------------------------------------------------------
    # Encabezado / pie de cada página
    # ------------------------------------------------------------------
    def _decorar_pagina(self, canvas, doc):
        ancho, alto = letter
        canvas.saveState()

        if self.logo_path is not None:
            try:  # el logo cabe en una caja de 1.9 x 1.9 cm, conservando sus proporciones
                canvas.drawImage(str(self.logo_path), 1.5 * cm, alto - 2.5 * cm, width=1.9 * cm,
                                 height=1.9 * cm, mask="auto", preserveAspectRatio=True, anchor="sw")
            except Exception:  # noqa: BLE001  imagen corrupta: el PDF sale sin logo
                log.exception("No se pudo dibujar el logo %s", self.logo_path)
                self.logo_path = None

        canvas.setFillColor(self.NARANJA)
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(3.7 * cm, alto - 1.5 * cm, "SUNNY APP")

        canvas.setFillColor(self.GRIS_OSC)
        canvas.setFont("Helvetica", 9)
        canvas.drawString(3.7 * cm, alto - 2.0 * cm, "Reporte de alarmas - Plataforma Goodwe")
        canvas.drawRightString(ancho - 1.5 * cm, alto - 1.5 * cm, self.fecha.strftime("%d/%m/%Y %H:%M"))

        canvas.setStrokeColor(self.NARANJA)
        canvas.setLineWidth(1)
        canvas.line(1.5 * cm, alto - 2.6 * cm, ancho - 1.5 * cm, alto - 2.6 * cm)

        canvas.setFillColor(colors.grey)
        canvas.setFont("Helvetica", 8)
        canvas.drawCentredString(ancho / 2, 1 * cm, f"Pagina {doc.page}")

        canvas.restoreState()

    # ------------------------------------------------------------------
    # Página de resumen
    # ------------------------------------------------------------------
    def _resumen(self):
        total = len(self.alarmas)
        nuevas = sum(1 for a in self.alarmas if a.get("clasificacion") == "nueva")
        persistentes = sum(1 for a in self.alarmas if a.get("clasificacion") == "persistente")
        fallos = sum(1 for a in self.alarmas if a["nivel"] == "Fallo")

        elementos = [Paragraph("Resumen del reporte", self.s_h1)]

        kpis = Table(
            [[str(total), str(nuevas), str(persistentes), str(fallos)],
             ["Alarmas activas", "Nuevas", "Persistentes", "Fallos críticos"]],
            colWidths=[4.6 * cm] * 4,
        )
        kpis.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 22),
            ("LEADING", (0, 0), (-1, 0), 28),
            ("TEXTCOLOR", (0, 0), (-1, 0), self.NARANJA),
            ("FONTSIZE", (0, 1), (-1, 1), 9),
            ("TEXTCOLOR", (0, 1), (-1, 1), self.GRIS_OSC),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
        ]))
        elementos += [kpis, Spacer(1, 16), Paragraph("Alarmas incluidas en este reporte", self.s_h1)]

        encabezados = ["#", "Planta", "Alarma", "Nivel", "Estado", "Duración"]
        filas = [[Paragraph(h, self.s_celda_head) for h in encabezados]]
        for idx, a in enumerate(self.alarmas, start=1):
            nombre_en = a.get("nombre_alarma") or "-"
            nombre_es = a.get("nombre_alarma_es")
            if nombre_es:
                alarma_txt = f'{escape(nombre_es)}<br/><font size="7" color="grey">{escape(nombre_en)}</font>'
            else:
                alarma_txt = escape(nombre_en)
            color_nivel = self.HEX_ROJO if a["nivel"] == "Fallo" else self.HEX_AMBAR
            filas.append([
                Paragraph(str(idx), self.s_celda),
                Paragraph(escape(a["nombre_planta"]), self.s_celda),
                Paragraph(alarma_txt, self.s_celda),
                Paragraph(f'<font color="{color_nivel}"><b>{escape(a["nivel"])}</b></font>', self.s_celda),
                Paragraph(escape((a.get("clasificacion") or "-").capitalize()), self.s_celda),
                Paragraph(escape(a.get("duracion") or "-"), self.s_celda),
            ])

        tabla = Table(filas, colWidths=[0.8 * cm, 5.3 * cm, 5.2 * cm, 1.8 * cm, 2.4 * cm, 3.0 * cm],
                      repeatRows=1)
        tabla.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), self.GRIS_OSC),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, self.GRIS_CLARO]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
        ]))
        elementos.append(tabla)
        return elementos

    # ------------------------------------------------------------------
    # Ficha por alarma (una página cada una)
    # ------------------------------------------------------------------
    def _ficha(self, idx, a):
        nombre_en = a.get("nombre_alarma") or "-"
        nombre_es = a.get("nombre_alarma_es")
        color_nivel = self.HEX_ROJO if a["nivel"] == "Fallo" else self.HEX_AMBAR
        clasificacion = (a.get("clasificacion") or "-").capitalize()

        elementos = [Paragraph(f"{idx}. {escape(nombre_es or nombre_en)}", self.s_titulo)]
        if nombre_es:
            elementos.append(Paragraph(f"Nombre original en la plataforma: {escape(nombre_en)}", self.s_sub))
        elementos.append(Paragraph(
            f'<font color="{color_nivel}"><b>{escape(a["nivel"])}</b></font>'
            f'  ·  {escape(a["estado"])}  ·  <b>{escape(clasificacion)}</b>',
            self.s_texto,
        ))

        def lab(txt):
            return Paragraph(txt, self.s_label)

        def val(txt):
            return Paragraph(escape(str(txt)) if txt else "-", self.s_valor)

        datos = Table([
            [lab("Planta"), val(a["nombre_planta"]), lab("Hora de alarma"), val(a["hora_alarma"])],
            [lab("Equipo"), val(a.get("equipo")), lab("SN"), val(a["sn"])],
            [lab("Tipo de alarma"), val(a.get("tipo_alarma")), lab("Duración"), val(a.get("duracion"))],
            [lab("Confirmación"), val(a.get("confirm_status")), lab("Recuperación"), val(a.get("recovery_time"))],
        ], colWidths=[3.0 * cm, 6.2 * cm, 3.0 * cm, 6.2 * cm])
        datos.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
        ]))
        elementos.append(datos)

        elementos += self._bloque("Razón", a.get("razon"))
        elementos += self._bloque("Sugerencia", a.get("sugerencia"))
        elementos += self._curva(a.get("curva_path"))
        return elementos

    def _bloque(self, titulo, texto):
        return [
            Paragraph(titulo, self.s_bloque_header),
            Paragraph(escape(texto) if texto else "No disponible", self.s_texto),
        ]

    def _curva(self, ruta):
        encabezado = Paragraph("Curva - Potencia activa", self.s_bloque_header)
        if ruta and Path(ruta).exists():
            ancho_px, alto_px = ImageReader(str(ruta)).getSize()
            escala = min(15 * cm / ancho_px, 8.5 * cm / alto_px)
            imagen = Image(str(ruta), width=ancho_px * escala, height=alto_px * escala)
            imagen.hAlign = "CENTER"
            return [KeepTogether([encabezado, Spacer(1, 4), imagen])]
        return [encabezado, Paragraph("Captura de la curva no disponible en esta ejecución.", self.s_texto)]