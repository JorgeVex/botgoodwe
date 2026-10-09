import logging

from config.settings import settings
from scraper.auth import GoodweAuth
from scraper.alarms import GoodweAlarmScraper
from database.db import Database, AlarmaRepository
from reports.excel_report import ExcelReportGenerator
from reports.pdf_report import PDFReportGenerator
from notifications.mailer import ReportMailer


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
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
        )

    def ejecutar_ciclo(self):
        """Un ciclo completo del bot: se llama una vez por ejecución programada."""
        self.logger.info("Iniciando ciclo del bot.")
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

            self.logger.info(f"Resumen del ciclo: {resumen}")

            if fichas:
                self._generar_y_enviar_reportes(fichas)
            else:
                self.logger.info("Sin novedades. No se generan reportes en este ciclo.")

        except Exception as e:
            self.logger.exception(f"Error durante el ciclo del bot: {e}")
            raise

        finally:
            self.auth.cerrar()
            self.logger.info("Ciclo finalizado. Navegador cerrado.")

        return resumen

    def _generar_y_enviar_reportes(self, fichas: list[dict]):
        activas = self.repo.obtener_activas()
        ruta_excel = ExcelReportGenerator(activas).generar()
        self.logger.info(f"Reporte Excel generado: {ruta_excel}")

        # UN solo PDF con resumen + una página por alarma
        ruta_pdf = PDFReportGenerator(fichas).generar()
        self.logger.info(f"PDF consolidado generado ({len(fichas)} alarmas): {ruta_pdf}")

        try:
            mailer = ReportMailer()
            mailer.enviar_reporte(
                asunto="[Sunny App] Reporte diario de alarmas — Goodwe",
                cuerpo=(
                    f"Adjunto el reporte diario de alarmas (Excel) y el informe consolidado en PDF "
                    f"con el detalle de {len(fichas)} alarma(s), generados automáticamente por el bot."
                ),
                adjuntos=[ruta_excel, ruta_pdf],
            )
            self.logger.info(f"Reporte enviado por correo a {settings.email_to}")
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
            "tipo_alarma": alarma.tipo_alarma,
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