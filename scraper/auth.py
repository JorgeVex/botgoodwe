import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

from config.settings import settings


class GoodweAuth:
    """Encapsula el acceso automatizado (RPA) a la plataforma Goodwe (SEMS+)."""

    def __init__(self):
        self.driver = None
        self.wait = None

    def _pausa(self):
        time.sleep(settings.click_delay)

    def _validar_credenciales_configuradas(self):
        if not settings.goodwe_user or not settings.goodwe_password:
            raise RuntimeError(
                "GOODWE_USER o GOODWE_PASSWORD no se cargaron desde config/.env. "
                "Verifica que el archivo exista, tenga esas dos variables escritas "
                "(sin comillas, sin espacios alrededor del '=') y que no esté vacío."
            )

    def iniciar_navegador(self):
        """Inicializa el navegador Chrome controlado por Selenium."""
        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        if settings.headless:
            options.add_argument("--headless=new")

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        self.wait = WebDriverWait(self.driver, settings.timeout)
        return self.driver

    def _aceptar_cookies(self):
        """Cierra el banner de cookies, si aparece."""
        try:
            boton = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, settings.xpaths["boton_cookies"]))
            )
            self.driver.execute_script("arguments[0].click();", boton)
            self._pausa()
        except TimeoutException:
            pass  # no siempre aparece si ya se aceptaron antes en esa sesión

    def _completar_credenciales(self):
        """Ubica los campos de usuario/contraseña y escribe las credenciales."""
        campo_usuario = self.wait.until(
            EC.presence_of_element_located((By.XPATH, settings.xpaths["campo_usuario"]))
        )
        self.driver.execute_script("arguments[0].click();", campo_usuario)
        campo_usuario.send_keys(settings.goodwe_user)
        self._pausa()

        campo_clave = self.wait.until(
            EC.presence_of_element_located((By.XPATH, settings.xpaths["campo_password"]))
        )
        self.driver.execute_script("arguments[0].click();", campo_clave)
        campo_clave.send_keys(settings.goodwe_password)
        self._pausa()

    def _aceptar_terminos(self):
        """Marca el checkbox de aceptación del Service Agreement, requerido para poder iniciar sesión."""
        checkbox = self.wait.until(
            EC.presence_of_element_located((By.XPATH, settings.xpaths["checkbox_terminos"]))
        )
        self.driver.execute_script("arguments[0].click();", checkbox)
        self._pausa()

    def _clic_login(self):
        boton = self.wait.until(
            EC.element_to_be_clickable((By.XPATH, settings.xpaths["boton_login"]))
        )
        self.driver.execute_script("arguments[0].click();", boton)
        self._pausa()

    def _validar_login(self) -> bool:
        try:
            WebDriverWait(self.driver, settings.timeout).until(
                lambda d: "login" not in d.current_url.lower()
            )
            return True
        except TimeoutException:
            return False

    def login(self):
        """Ejecuta el flujo completo de acceso y devuelve el driver ya autenticado."""
        self._validar_credenciales_configuradas()

        if self.driver is None:
            self.iniciar_navegador()

        self.driver.get(settings.goodwe_login_url)
        self._pausa()
        self._aceptar_cookies()
        self._completar_credenciales()
        self._aceptar_terminos()
        self._clic_login()
        time.sleep(3)

        if not self._validar_login():
            raise RuntimeError("No se pudo validar el login en SEMS+. Verifica los XPath, las credenciales o si el checkbox de términos quedó marcado.")

        return self.driver

    def cerrar(self):
        if self.driver:
            self.driver.quit()