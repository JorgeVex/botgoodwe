import os
from pathlib import Path
from dotenv import load_dotenv


class Settings:
    """Configuración centralizada del bot: rutas, credenciales, XPaths y parámetros."""

    def __init__(self):
        self.base_dir = Path(__file__).resolve().parent.parent
        self.env_path = self.base_dir / "config" / ".env"
        load_dotenv(dotenv_path=self.env_path)

        # Credenciales
        self.goodwe_user = os.getenv("GOODWE_USER")
        self.goodwe_password = os.getenv("GOODWE_PASSWORD")

        # URL de la plataforma (SEMS+, vigente desde la discontinuación de semsportal.com)
        self.goodwe_login_url = "https://semsplus.goodwe.com/#/login"

        # XPaths documentados durante el reconocimiento de la plataforma
        self.xpaths = {
            # Login (SEMS+)
            "campo_usuario": '//*[@id="account"]',
            "campo_password": '//*[@id="pwd"]',
            "checkbox_terminos": '//*[@id="semsV2"]/div/div/div[3]/div[3]/div[1]/div[2]/div[3]/label/span[1]/input',
            "boton_login": '//*[@id="semsV2"]/div/div/div[3]/div[3]/div[1]/div[2]/div[4]/button',
            "boton_cookies": '//*[@id="semsV2"]/div/div/div[3]/div[3]/div[3]/div/div/button',

            # Post-login (confirmados sobre SEMS+)
            "icono_alarmas": '//*[@id="modalWrap"]/div[1]/div/div[2]/div[3]/div/span/div',
            "tabla_alarmas": '//*[@id="modalWrap"]/div[2]/div/div[2]/div/div/div/div/div[3]/div/div[1]/div/div/div',
            "panel_detalle_alarma": '//*[@id="modalWrap"]/div[7]/div[3]/div',
            "bloque_curva": '//*[@id="modalWrap"]/div[7]/div[3]/div/div[2]/div/div[6]/div',
            "panel_detalle_alarma": "//div[@id='modalWrap']//div[contains(@class,'ant-drawer-body')]",
            "tabla_alarmas_body": "//div[contains(@class,'ant-table-body')]",  
            "bloque_curva_relativo": "/div/div[6]",  # relativo a panel_detalle_alarma (ant-drawer-body)
        }

        # Directorio temporal para las capturas de curva (se usan y se borran por ciclo)
        self.curvas_dir = self.base_dir / "reports_output" / "curvas_tmp"
        self.curvas_dir.mkdir(parents=True, exist_ok=True)
        # Base de datos
        self.db_path = self.base_dir / "database" / "goodwe_bot.db"

        # Reportes
        self.reports_dir = self.base_dir / "reports_output"
        self.excel_dir = self.reports_dir / "excel"
        self.pdf_dir = self.reports_dir / "pdf"
        self._crear_directorios(self.reports_dir, self.excel_dir, self.pdf_dir)

        # Umbrales de negocio
        self.dias_alarma_cronica = 5  # días activa sin resolverse para el resumen semanal

        # Logs
        self.logs_dir = self.base_dir / "logs"
        self._crear_directorios(self.logs_dir)
        self.log_file = self.logs_dir / "bot.log"

        # Selenium
        self.timeout = 15
        self.headless = False
        self.click_delay = 1.0
        
        
                # Correo (envío de reportes)
        self.email_from = os.getenv("EMAIL_FROM")
        self.email_app_password = os.getenv("EMAIL_APP_PASSWORD")
        self.email_to = [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 587
        
        
    @staticmethod
    def _crear_directorios(*dirs):
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)


# Instancia única que el resto de módulos importará
settings = Settings()