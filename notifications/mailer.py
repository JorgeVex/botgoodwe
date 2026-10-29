import logging
import mimetypes
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

from config.settings import settings

log = logging.getLogger(__name__)


class ReportMailer:
    """Envía los reportes (Excel + PDF) por correo vía SMTP (configurable en el .env)."""

    REINTENTOS = 2

    def _construir_mensaje(self, asunto: str, cuerpo: str, adjuntos) -> EmailMessage:
        if not settings.email_to:
            raise RuntimeError("EMAIL_TO no está configurado en config/.env")
        msg = EmailMessage()
        msg["Subject"] = asunto
        msg["From"] = settings.email_from
        msg["To"] = ", ".join(settings.email_to)
        msg.set_content(cuerpo)

        for ruta in adjuntos or []:
            ruta = Path(ruta)
            tipo, _ = mimetypes.guess_type(ruta.name)
            principal, secundario = (tipo or "application/octet-stream").split("/", 1)
            msg.add_attachment(ruta.read_bytes(), maintype=principal, subtype=secundario,
                               filename=ruta.name)
        return msg

    def _enviar_una_vez(self, msg: EmailMessage):
        host, puerto = settings.smtp_server, settings.smtp_port
        if puerto == 465:   # SSL implícito
            servidor = smtplib.SMTP_SSL(host, puerto, timeout=settings.smtp_timeout)
        else:               # STARTTLS (587, 2525, ...)
            servidor = smtplib.SMTP(host, puerto, timeout=settings.smtp_timeout)
        with servidor as server:
            if puerto != 465:
                server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg, to_addrs=settings.email_to)

    def enviar_reporte(self, asunto: str, cuerpo: str, adjuntos=None):
        msg = self._construir_mensaje(asunto, cuerpo, adjuntos)
        ultimo_error = None
        for intento in range(1, self.REINTENTOS + 1):
            try:
                self._enviar_una_vez(msg)
                return
            except Exception as e:  # noqa: BLE001
                ultimo_error = e
                log.warning("Envío de correo falló (intento %d/%d) por %s:%s -> %s",
                            intento, self.REINTENTOS, settings.smtp_server, settings.smtp_port, e)
                time.sleep(3)
        raise ultimo_error