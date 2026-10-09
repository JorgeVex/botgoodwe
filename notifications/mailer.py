import smtplib
from email.message import EmailMessage
from pathlib import Path

from config.settings import settings


class ReportMailer:
    """Envía los reportes generados por correo electrónico (SMTP)."""

    MIME_TYPES = {
        ".xlsx": ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ".pdf": ("application", "pdf"),
        ".png": ("image", "png"),
    }

    def __init__(self):
        self._validar_configuracion()

    @staticmethod
    def _validar_configuracion():
        if not settings.email_from or not settings.email_app_password or not settings.email_to:
            raise RuntimeError(
                "Faltan variables de correo en config/.env "
                "(EMAIL_FROM, EMAIL_APP_PASSWORD, EMAIL_TO)."
            )

    def enviar_reporte(self, asunto: str, cuerpo: str, adjuntos: list[str] = None):
        """Envía un correo con los reportes indicados como archivos adjuntos."""
        msg = EmailMessage()
        msg["Subject"] = asunto
        msg["From"] = settings.email_from
        msg["To"] = settings.email_to
        msg.set_content(cuerpo)

        for ruta_str in (adjuntos or []):
            self._adjuntar_archivo(msg, Path(ruta_str))

        with smtplib.SMTP(settings.smtp_server, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.email_from, settings.email_app_password)
            server.send_message(msg)

    def _adjuntar_archivo(self, msg: EmailMessage, ruta: Path):
        if not ruta.exists():
            return
        maintype, subtype = self.MIME_TYPES.get(ruta.suffix.lower(), ("application", "octet-stream"))
        msg.add_attachment(ruta.read_bytes(), maintype=maintype, subtype=subtype, filename=ruta.name)