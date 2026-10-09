"""
Extracción del detalle de una alarma (Razón, Sugerencia, Curva) desde el drawer
"Alarm Details" de SEMS+.

Se entrega como mixin / funciones independientes para pegarlas en GoodweAlarmScraper
(clase en scraper/alarms.py) sin tocar el resto de la clase.
"""
import logging
import re
import time
from pathlib import Path

from selenium.common.exceptions import (
    NoSuchElementException, StaleElementReferenceException, TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Selectores (van a config/settings.py en el proyecto real)
# Primero el selector EXACTO que copió Juan David del navegador; después una
# versión sin la clase "css-1goys6g" (hash que Ant Design regenera) como respaldo.
# ----------------------------------------------------------------------
_BASE_EXACTA = (
    "#modalWrap > div.ant-drawer.ant-drawer-right.css-1goys6g.ant-drawer-open > "
    "div.ant-drawer-content-wrapper > div > div.ant-drawer-body > div"
)
_BASE_ROBUSTA = "#modalWrap .ant-drawer-open .ant-drawer-body > div"

SEL_RAZON = [
    f"{_BASE_EXACTA} > div:nth-child(4)",
    f"{_BASE_ROBUSTA} > div:nth-child(4)",
]
SEL_SUGERENCIA = [
    f"{_BASE_EXACTA} > div.index-module_suggestion_d5192",
    f"{_BASE_ROBUSTA} > div[class*='suggestion']",
]
# La curva vive dentro del bloque de razón (index-module_chartContainer_xxxxx)
SEL_CURVA = [
    f"{_BASE_ROBUSTA} [class*='chartContainer']",
    f"{_BASE_ROBUSTA} > div:nth-child(4) canvas",
]
SEL_DRAWER = "#modalWrap .ant-drawer-open .ant-drawer-body"
# Botón de cerrar del drawer (XPath que copió Juan David) y respaldos más estables
XP_CERRAR = '//*[@id="modalWrap"]/div[5]/div[3]/div/div[1]/div/button'
SEL_CERRAR = "#modalWrap .ant-drawer-open .ant-drawer-close"

# Tiempos (segundos)
T_DRAWER = 20          # esperar a que abra el drawer
T_BLOQUE = 20          # esperar a que aparezca razón / sugerencia
T_CURVA = 30           # esperar a que la gráfica se dibuje
T_ANTES_CAPTURA = 5    # pausa fija con la gráfica ya visible, para que cargue completa antes de la foto
T_ESTABLE = 1.5        # pausa tras detectar la gráfica (animación de ECharts/AntV)
INTENTOS = 3

# JS: devuelve el texto de un bloque SIN el contenedor de la gráfica (los
# números de los ejes no deben contaminar la "razón").
_JS_TEXTO_SIN_GRAFICA = """
const el = arguments[0];
const clon = el.cloneNode(true);
clon.querySelectorAll("[class*='chartContainer'], canvas, svg, script, style")
    .forEach(n => n.remove());
return clon.textContent || "";
"""

# JS: ¿la gráfica ya se dibujó? (canvas o svg con tamaño real y contenido)
_JS_GRAFICA_LISTA = """
const c = arguments[0];
const lienzo = c.matches('canvas') ? c : c.querySelector('canvas');
if (lienzo) {
    if (lienzo.width < 50 || lienzo.height < 50) return false;
    try {  // un canvas "en blanco" no tiene ningún píxel con alpha > 0
        const ctx = lienzo.getContext('2d');
        const d = ctx.getImageData(0, 0, lienzo.width, lienzo.height).data;
        for (let i = 3; i < d.length; i += 4 * 17) { if (d[i] > 0) return true; }
        return false;
    } catch (e) { return true; }   // canvas WebGL / tainted: confiar en el tamaño
}
const svg = c.querySelector('svg');
if (svg) {
    const r = svg.getBoundingClientRect();
    return r.width > 50 && r.height > 50 && svg.querySelectorAll('path, polyline, line').length > 3;
}
return false;
"""


def _limpiar(texto: str, etiquetas: tuple[str, ...]) -> str:
    """Quita la etiqueta inicial ('Reason', 'Suggestion', ...) y normaliza espacios."""
    texto = re.sub(r"[ \t\xa0]+", " ", texto or "")
    texto = re.sub(r"\s*\n\s*", "\n", texto).strip()
    for et in etiquetas:
        texto = re.sub(rf"^{et}\s*[:：]?\s*", "", texto, flags=re.IGNORECASE)
    return texto.strip()


class DetalleAlarmaMixin:
    """Requiere self.driver (webdriver) y self.curvas_dir (Path)."""

    # -------------------------------------------------------------
    def _buscar(self, selectores: list[str], timeout: float, visible=True):
        """Devuelve el primer elemento que aparezca con cualquiera de los selectores."""
        fin = time.time() + timeout
        while time.time() < fin:
            for sel in selectores:
                try:
                    for el in self.driver.find_elements(By.CSS_SELECTOR, sel):
                        if not visible or el.is_displayed():
                            return el
                except StaleElementReferenceException:
                    pass
            time.sleep(0.5)
        return None

    def _texto_bloque(self, selectores, etiquetas, timeout) -> str:
        """Espera a que el bloque tenga TEXTO (no basta con que exista) y lo devuelve limpio."""
        fin = time.time() + timeout
        while time.time() < fin:
            el = self._buscar(selectores, 2)
            if el is not None:
                try:
                    texto = _limpiar(self.driver.execute_script(_JS_TEXTO_SIN_GRAFICA, el), etiquetas)
                    if texto:
                        return texto
                except StaleElementReferenceException:
                    pass
            time.sleep(0.7)
        return ""

    def _esperar_grafica(self, timeout: float):
        """Espera a que la curva exista, se dibuje y deje de moverse. None si no se logra."""
        fin = time.time() + timeout
        while time.time() < fin:
            el = self._buscar(SEL_CURVA, 2)
            if el is not None:
                try:
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center', inline:'nearest'});", el)
                    if self.driver.execute_script(_JS_GRAFICA_LISTA, el):
                        # estable: mismo tamaño en dos lecturas separadas
                        t1 = el.size
                        time.sleep(T_ESTABLE)
                        if el.size == t1:
                            time.sleep(T_ANTES_CAPTURA)
                            return el
                except StaleElementReferenceException:
                    pass
            time.sleep(0.8)
        return None

    # -------------------------------------------------------------
    def extraer_detalle(self, alarma_id: str) -> dict:
        """
        Con el drawer 'Alarm Details' YA abierto (tras hacer clic en la fila), devuelve:
            {"razon": str, "sugerencia": str, "curva_path": str | None}
        alarma_id: texto seguro para el nombre del archivo, p. ej. f"{sn}_{timestamp}".
        """
        resultado = {"razon": "", "sugerencia": "", "curva_path": None}

        try:
            WebDriverWait(self.driver, T_DRAWER).until(
                lambda d: any(e.is_displayed() for e in d.find_elements(By.CSS_SELECTOR, SEL_DRAWER)))
        except TimeoutException:
            log.error("El drawer de detalle no se abrió para %s", alarma_id)
            return resultado

        for intento in range(1, INTENTOS + 1):
            if not resultado["razon"]:
                resultado["razon"] = self._texto_bloque(SEL_RAZON, ("Reason", "Razón"), T_BLOQUE)
            if not resultado["sugerencia"]:
                resultado["sugerencia"] = self._texto_bloque(
                    SEL_SUGERENCIA, ("Suggestion", "Sugerencia"), T_BLOQUE)

            if not resultado["curva_path"]:
                grafica = self._esperar_grafica(T_CURVA)
                if grafica is not None:
                    resultado["curva_path"] = self._capturar(grafica, alarma_id)

            if resultado["razon"] and resultado["sugerencia"] and resultado["curva_path"]:
                break
            log.warning("Detalle incompleto de %s (intento %d/%d): razon=%s sugerencia=%s curva=%s",
                        alarma_id, intento, INTENTOS, bool(resultado["razon"]),
                        bool(resultado["sugerencia"]), bool(resultado["curva_path"]))
            time.sleep(2)

        # Último recurso: si no hubo gráfica, foto del drawer completo (evidencia para el informe)
        if not resultado["curva_path"]:
            drawer = self._buscar([SEL_DRAWER], 3)
            if drawer is not None:
                resultado["curva_path"] = self._capturar(drawer, f"{alarma_id}_drawer")
        return resultado

    def _drawer_abierto(self) -> bool:
        try:
            return any(e.is_displayed() for e in self.driver.find_elements(By.CSS_SELECTOR, SEL_DRAWER))
        except StaleElementReferenceException:
            return True

    def cerrar_detalle(self, timeout: float = 10) -> bool:
        """
        Cierra el drawer 'Alarm Details' y CONFIRMA que se cerró antes de seguir con
        la siguiente alarma. Orden: XPath del botón -> botón .ant-drawer-close -> tecla Esc.
        """
        if not self._drawer_abierto():
            return True
        intentos = (
            lambda: self.driver.find_element(By.XPATH, XP_CERRAR),
            lambda: self.driver.find_element(By.CSS_SELECTOR, SEL_CERRAR),
            lambda: None,  # Esc
        )
        for buscar in intentos:
            try:
                boton = buscar()
                if boton is not None:
                    self.driver.execute_script("arguments[0].click();", boton)
                else:
                    self.driver.switch_to.active_element.send_keys(Keys.ESCAPE)
            except Exception:  # noqa: BLE001  (botón no encontrado, obsoleto, etc.)
                continue
            fin = time.time() + timeout
            while time.time() < fin:
                if not self._drawer_abierto():
                    time.sleep(1)   # deja terminar la animación de cierre
                    return True
                time.sleep(0.4)
        log.warning("No se pudo cerrar el drawer de detalle")
        return False

    def _capturar(self, elemento, nombre: str) -> str | None:
        ruta = Path(self.curvas_dir) / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', nombre)}.png"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elemento)
            time.sleep(0.5)
            if elemento.screenshot(str(ruta)) and ruta.exists() and ruta.stat().st_size > 2000:
                return str(ruta)
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo capturar %s: %s", nombre, e)
        return None