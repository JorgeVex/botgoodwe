import logging
from datetime import datetime

from config.settings import settings
from scraper.auth import GoodweAuth
from scraper.alarms import GoodweAlarmScraper
from database.db import Database, AlarmaRepository
from reports.excel_report import ExcelReportGenerator
from reports.pdf_report import PDFReportGenerator
from notifications.mailer import ReportMailer
from scraper.alarm_translations import traducir_tipo


class GoodweBot:
    """Orquesta el ciclo completo: acceso, recolección, procesamiento y reportes."""

    def __init__(self):
        self._configurar_logging()
        self.logger = logging.getLogger("GoodweBot")
        self.auth = GoodweAuth()
        self.repo = AlarmaRepository(Database())

    @staticmethod
    def _configurar_logging():
        logging.basicConfig(
            filename=settings.log_file,
            encoding="utf-8",
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
        )

    def ejecutar_ciclo(self):
        """Un ciclo completo del bot: se llama una vez por ejecución programada."""
        self.logger.info("Iniciando ciclo del bot (headless=%s).", settings.headless)
        resumen = {"nueva": 0, "persistente": 0, "resuelta": 0, "sin_cambios": 0}
        fichas = []  # una entrada por alarma nueva/persistente; con ellas se arma UN solo PDF

        try:
            driver = self.auth.login()
            self.logger.info("Login exitoso en SEMS+.")

            scraper = GoodweAlarmScraper(driver)
            scraper.abrir_centro_alarmas()
            alarmas = scraper.listar_alarmas_activas()
            self.logger.info(f"Se leyeron {len(alarmas)} alarmas de la plataforma.")

            for fila_index, alarma in enumerate(alarmas, start=1):
                resultado = self.repo.procesar_lectura(alarma)
                resumen[resultado] += 1

                # Solo entramos al detalle (razón, sugerencia, curva) si es nueva o persistente.
                if resultado in ("nueva", "persistente"):
                    try:
                        alarma = scraper.completar_detalle_y_curva(alarma, fila_index)
                        self.repo.actualizar_detalle(alarma)  # razón/sugerencia a la BD (para el Excel)
                    except Exception as e:
                        # Una alarma con problemas no debe tumbar el ciclo completo:
                        # se reporta con los datos de la tabla, sin detalle ampliado.
                        self.logger.exception(f"Falló el detalle de '{alarma.nombre_alarma}' ({alarma.sn}): {e}")
                    fichas.append(self._alarma_a_ficha(alarma, resultado))
                    self.logger.info(
                        f"Detalle listo: '{alarma.nombre_alarma}' ({alarma.sn}) | "
                        f"razón={'sí' if getattr(alarma, 'razon', None) else 'NO'} | "
                        f"sugerencia={'sí' if getattr(alarma, 'sugerencia', None) else 'NO'} | "
                        f"curva={'sí' if getattr(alarma, 'ruta_curva', None) else 'NO'}"
                    )

            self._sincronizar_base_de_datos(alarmas, resumen)
            self.logger.info(f"Resumen del ciclo: {resumen}")

            resueltas = self._resueltas_para_informar()
            if fichas or resueltas:
                self._generar_y_enviar_reportes(fichas, resueltas)
            else:
                self.logger.info("Sin novedades. No se generan reportes en este ciclo.")

        except Exception as e:
            self.logger.exception(f"Error durante el ciclo del bot: {e}")
            raise

        finally:
            self.auth.cerrar()
            self.logger.info("Ciclo finalizado. Navegador cerrado.")

        return resumen

    def _sincronizar_base_de_datos(self, alarmas, resumen):
        """
        Cierra en la BD las alarmas que ya no están en la plataforma y limpia el historial viejo.
        Si la lectura vino vacía NO se sincroniza: sería indistinguible de una tabla que no cargó,
        y se marcarían como resueltas alarmas que siguen activas.
        """
        try:
            if not alarmas:
                self.logger.warning("Lectura vacía: no se sincroniza la BD en este ciclo.")
                return
            cerradas = self.repo.sincronizar_activas(alarmas)
            for r in cerradas:
                self.logger.info(f"Alarma cerrada (ya no aparece en Occurring): "
                                 f"'{r['nombre_alarma']}' ({r['sn']}) desde {r['hora_alarma']}")
            resumen["resuelta"] += len(cerradas)
            purgadas = self.repo.purgar_resueltas()
            if purgadas:
                self.logger.info(f"Historial: {purgadas} alarma(s) resuelta(s) antiguas eliminadas.")
        except Exception as e:
            self.logger.exception(f"No se pudo sincronizar la base de datos: {e}")

    def _resueltas_para_informar(self):
        """
        Alarmas resueltas que se informan HOY. Solo en los días de resumen (settings.dias_resumen_resueltas)
        y solo las que aún no se habían informado: cada una aparece una vez y no se queda en el reporte.
        """
        try:
            dias = settings.ventana_resueltas_dias
            self.repo.descartar_resueltas_viejas(dias)   # las muy antiguas nunca se muestran
            if datetime.now().weekday() not in settings.dias_resumen_resueltas:
                return []
            # Una fila por (equipo, alarma): las que reaparecen a diario salen una vez con "N veces"
            resueltas = self.repo.agrupar_resueltas(self.repo.obtener_resueltas_pendientes(dias))
            total = sum(r["apariciones"] for r in resueltas)
            self.logger.info(f"Hoy es día de resumen: {total} resolución(es) en {len(resueltas)} alarma(s) por informar.")
            return resueltas
        except Exception as e:
            self.logger.exception(f"No se pudieron consultar las alarmas resueltas: {e}")
            return []

    def _generar_y_enviar_reportes(self, fichas: list[dict], resueltas=None):
        resueltas = resueltas or []
        dia_resumen = datetime.now().weekday() in settings.dias_resumen_resueltas
        activas = self.repo.obtener_activas()

        # Una sola regla para "Nueva/Persistente" en Excel y PDF: la de la base de datos
        # (Nueva = primera detección HOY; Persistente = viene de días anteriores).
        situacion = {(r["sn"], r["nombre_alarma"], r["hora_alarma"]): (r["situacion"].lower(), r["apariciones"])
                     for r in activas}
        for f in fichas:
            f["clasificacion"], f["apariciones"] = situacion.get(
                (f["sn"], f["nombre_alarma"], f["hora_alarma"]), (f["clasificacion"], 1))

        ruta_excel = ExcelReportGenerator(activas, resueltas=resueltas if dia_resumen else None).generar()
        self.logger.info(f"Reporte Excel generado: {ruta_excel}")

        # UN solo PDF con resumen + una página por alarma
        ruta_pdf = PDFReportGenerator(fichas, resueltas=resueltas if dia_resumen else None).generar()
        self.logger.info(f"PDF consolidado generado ({len(fichas)} alarmas): {ruta_pdf}")

        try:
            mailer = ReportMailer()
            mailer.enviar_reporte(
                asunto="[Sunny App] Reporte diario de alarmas — Goodwe",
                cuerpo=(
                    f"Adjunto el reporte diario de alarmas (Excel) y el informe consolidado en PDF "
                    f"con el detalle de {len(fichas)} alarma(s), generados automáticamente por el bot."
                    + (f"\n\nResumen de seguimiento: {sum(r['apariciones'] for r in resueltas)} resolución(es) "
                       f"({len(resueltas)} alarma(s) distintas) se registraron desde el "
                       f"último aviso (ver la hoja 'Resueltas' del Excel y el final de la primera página del PDF)."
                       if dia_resumen and resueltas else "")
                ),
                adjuntos=[ruta_excel, ruta_pdf],
            )
            self.logger.info(f"Reporte enviado por correo a {settings.email_to}")
            # Solo si el correo salió: las resueltas ya informadas no vuelven a aparecer
            self.repo.marcar_resueltas_avisadas([i for r in resueltas for i in r["ids"]])
        except Exception as e:
            self.logger.exception(f"No se pudo enviar el correo: {e}")

    @staticmethod
    def _alarma_a_ficha(alarma, clasificacion: str) -> dict:
        """Diccionario con lo que PDFReportGenerator necesita para cada alarma."""
        return {
            "nombre_alarma": alarma.nombre_alarma,
            "nombre_alarma_es": getattr(alarma, "nombre_alarma_es", None),
            "nombre_planta": alarma.nombre_planta,
            "sn": alarma.sn,
            "equipo": alarma.equipo,
            "tipo_alarma": traducir_tipo(alarma.tipo_alarma),
            "nivel": alarma.nivel,
            "estado": alarma.estado,
            "hora_alarma": alarma.hora_alarma,
            "duracion": alarma.duracion,
            "recovery_time": alarma.recovery_time,
            "confirm_status": alarma.confirm_status,
            "razon": getattr(alarma, "razon", None),
            "sugerencia": getattr(alarma, "sugerencia", None),
            "curva_path": getattr(alarma, "ruta_curva", None),
            "clasificacion": clasificacion,
        }


if __name__ == "__main__":
    bot = GoodweBot()
    resultado = bot.ejecutar_ciclo()
    print(f"Ciclo completado: {resultado}")