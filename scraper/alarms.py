import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
from selenium.webdriver import ActionChains
from scraper.detalle import SEL_DRAWER

from config.settings import settings
from database.models import Alarma
from scraper.alarm_translations import traducir_alarma, traducir_texto
from scraper.detalle import DetalleAlarmaMixin


class GoodweAlarmScraper(DetalleAlarmaMixin):
    """Extrae la información de alarmas desde la plataforma Goodwe (SEMS+), usando un driver ya autenticado."""

    def __init__(self, driver):
        self.driver = driver
        self.wait = WebDriverWait(self.driver, settings.timeout)
        self.curvas_dir = settings.curvas_dir  # lo usa DetalleAlarmaMixin para guardar las capturas

    def abrir_centro_alarmas(self):
        """Navega al centro de alarmas general desde el ícono del encabezado."""
        icono = self.wait.until(
            EC.element_to_be_clickable((By.XPATH, settings.xpaths["icono_alarmas"]))
        )
        self.driver.execute_script("arguments[0].click();", icono)
        time.sleep(2)

    def _xpath_tabla_body(self) -> str:
        """
        Ruta confirmada al cuerpo real de la tabla. Ant Design separa el encabezado
        y el cuerpo en dos <table> distintos cuando hay columnas fijas:
          .../div/div[1]/table/thead  -> encabezado
          .../div/div[2]/table/tbody  -> cuerpo (filas de datos reales)
        """
        return f"{settings.xpaths['tabla_alarmas']}/div/div[2]"

    def _filas_tabla(self):
        return self.wait.until(
            EC.presence_of_all_elements_located(
                (By.XPATH, f"{self._xpath_tabla_body()}/table/tbody/tr")
            )
        )

    def listar_alarmas_activas(self) -> list[Alarma]:
        """
        Lee la tabla de la pestaña 'Occurring' del centro de alarmas y devuelve
        una lista de objetos Alarma con los datos visibles en la tabla
        (todas las columnas confirmadas, excepto 'Operation').
        """
        filas = self._filas_tabla()
        alarmas = []

        for celdas in [f.find_elements(By.TAG_NAME, "td") for f in filas]:
            if len(celdas) < 12:
                continue

            nombre_en = celdas[0].text.strip()
            alarmas.append(Alarma(
                nombre_alarma=nombre_en,
                nombre_alarma_es=traducir_alarma(nombre_en),
                equipo=celdas[1].text.strip(),
                estado="Ocurriendo" if "Occurring" in celdas[2].text else "Restaurado",
                nivel="Fallo" if "Fault" in celdas[3].text else "Alarma",
                hora_alarma=celdas[4].text.strip(),
                nombre_planta=celdas[5].text.strip(),
                sn=celdas[6].text.strip(),
                tipo_alarma=celdas[8].text.strip(),
                duracion=celdas[9].text.strip(),
                recovery_time=celdas[10].text.strip(),
                confirm_status=celdas[11].text.strip(),
            ))

        return alarmas

    def _abrir_detalle_fila(self, fila_index: int) -> bool:
        """
        Abre el drawer de detalle de la fila `fila_index` (1-based). El elemento que responde
        al clic suele ser el enlace/texto DENTRO de la celda, no el <td>; por eso se prueban,
        en orden: el elemento interno con el texto del nombre (clic real y luego JS), y por
        último la celda. Después de cada intento se verifica que el drawer realmente abrió.
        """
        xp_celda = f"{self._xpath_tabla_body()}/table/tbody/tr[{fila_index}]/td[1]"
        for _ in range(2):  # una repetición completa si la tabla se refrescó (stale)
            try:
                celda = self.wait.until(EC.presence_of_element_located((By.XPATH, xp_celda)))
                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", celda)
                time.sleep(0.5)
                internos = celda.find_elements(
                    By.XPATH, ".//a | .//*[normalize-space(text())!='' and not(*)]")
                candidatos = list(reversed(internos)) + [celda]   # el más profundo primero
                for el in candidatos:
                    for modo in ("real", "js"):
                        try:
                            if modo == "real":
                                ActionChains(self.driver).move_to_element(el).pause(0.3).click().perform()
                            else:
                                self.driver.execute_script("arguments[0].click();", el)
                        except Exception:  # noqa: BLE001
                            continue
                        if self._drawer_abierto_en(5):
                            return True
            except (StaleElementReferenceException, TimeoutException):
                time.sleep(1.5)
        return False

    def _drawer_abierto_en(self, segundos: float) -> bool:
        fin = time.time() + segundos
        while time.time() < fin:
            try:
                if any(e.is_displayed() for e in self.driver.find_elements(By.CSS_SELECTOR, SEL_DRAWER)):
                    return True
            except StaleElementReferenceException:
                pass
            time.sleep(0.4)
        return False

    def completar_detalle_y_curva(self, alarma: Alarma, fila_index: int) -> Alarma:
        """
        Hace clic en el nombre de la alarma (columna 'Alarm Name') de una fila específica,
        extrae razón/sugerencia y captura la curva (con esperas largas, ver scraper/detalle.py),
        y vuelve a abrir el centro de alarmas para dejar la tabla lista para la siguiente fila.

        fila_index: posición de la fila en la tabla actual (1-based).
        """
        try:
            if not self._abrir_detalle_fila(fila_index):
                raise TimeoutException(f"No se abrió el detalle de la fila {fila_index}")

            # Nombre de archivo de la curva: mismo formato que antes
            id_archivo = f"curva_{alarma.sn}_{alarma.hora_alarma}"
            detalle = self.extraer_detalle(id_archivo)

            # Razón y sugerencia llegan en inglés desde Goodwe: se traducen para el reporte
            alarma.razon = traducir_texto(detalle["razon"]) or None
            alarma.sugerencia = traducir_texto(detalle["sugerencia"]) or None
            alarma.ruta_curva = detalle["curva_path"]

        except TimeoutException:
            pass  # se guarda igual con los datos de la tabla general, sin detalle ampliado

        finally:
            # 1) Cerrar el drawer de detalle y confirmar que se cerró (si queda abierto, la
            #    siguiente alarma lee datos/imagen del drawer anterior o no carga bien).
            self.cerrar_detalle()
            # 2) Reabrir el centro de alarmas deja la tabla en un estado limpio y predecible
            # para procesar la siguiente fila, evitando problemas de elementos obsoletos (stale).
            self.abrir_centro_alarmas()

        return alarma