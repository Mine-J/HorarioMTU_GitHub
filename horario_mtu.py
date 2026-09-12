"""MTU SWS -> vista previa semanal / iCalendar / Microsoft Graph. Python 3.11+.

preview exporta la semana actual; en sábado o domingo, la siguiente (Irlanda).
sync conserva la sincronización del semestre completo con Microsoft Graph.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import smtplib
import ssl
import sys
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SOURCE = "https://myclasstimetables.mtu.ie/SWS/default.aspx"
DEFAULT_ENV_PATH = Path(__file__).resolve().with_name(".env")
ZONE = "Europe/Dublin"
GRAPH = "https://graph.microsoft.com/v1.0"
PROPERTY = "String {ff509530-0553-469b-a7ae-c1ffb9051b01} Name HorarioMTU"
SCOPES = ["User.Read", "Calendars.ReadWrite"]
DEFAULTS = {
    "student_set": "CO.MSC-CYBERSEC-B", "department": "Computer Science",
    "semester": "SEMESTER 1", "academic_year_start": 2026,
    "calendar_name": "Horario MTU", "client_id": "", "tenant": "common",
    "expected_account": "", "reminder_minutes": 15,
}
DAYS = {name: i for i, name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])}
MONTHS = {"jan": 1, "ja": 1, "feb": 2, "fe": 2, "mar": 3, "ma": 3,
          "apr": 4, "ap": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
          "au": 8, "sep": 9, "sept": 9, "se": 9, "oct": 10, "oc": 10,
          "nov": 11, "no": 11, "dec": 12, "de": 12}
WEEK = r"wk([A-Za-z]+)(\d{1,2})"

# Solo lee la tabla visible: ni cookies, ni contraseñas, ni estado de autenticación.
GRID_JS = """tables => tables.filter(t => t.rows[0] &&
  Array.from(t.rows[0].cells).some(c => c.textContent.trim() === '8:15'))
  .map(t => ({rows: Array.from(t.rows).map(r => Array.from(r.cells).map(c => ({
    text: c.innerText.trim(), colspan: c.colSpan, rowspan: c.rowSpan,
    parts: Array.from(c.querySelectorAll('table')).map(p => Array.from(p.rows)
      .map(pr => Array.from(pr.cells).map(pc => pc.innerText.trim())))
  })))}))"""


class TimetableError(RuntimeError):
    pass


class LoginRequired(TimetableError):
    pass


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def load_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def state_directory() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "HorarioMTU"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/HorarioMTU"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "HorarioMTU"


def create_env_template(path: Path):
    """Crea una plantilla vacía sin sobrescribir credenciales existentes."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = (
        "# Credenciales de MTU. Este archivo contiene texto sin cifrar.\n"
        "# Edita los valores aquí; no pegues tu contraseña en PowerShell.\n"
        "# Entre comillas simples: duplica las barras inversas y usa \\' para una comilla simple.\n"
        "MTU_USERNAME=''\n"
        "MTU_PASSWORD=''\n"
    )
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f"Ya existe: {path.resolve()}. Puedes editarlo; se conserva su contenido.")
        return
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    print(f"Plantilla creada: {path.resolve()}")


def read_mtu_credentials(path: Path) -> dict:
    path = path.expanduser().resolve()
    if not path.exists():
        print(f"No se encuentra el archivo de credenciales: {path}")
        print("Crea .env junto al programa o indica su ruta con --env-file.")
        return {}
    from dotenv import dotenv_values
    # ${...} puede formar parte literal de una contraseña; no se expande.
    values = dotenv_values(path, encoding="utf-8-sig", interpolate=False)
    username = (values.get("MTU_USERNAME") or "").strip()
    password = values.get("MTU_PASSWORD") or ""
    if username and not re.fullmatch(r"[^@\s]+@(?:mymtu|mtu)\.ie", username, re.I):
        raise TimetableError("MTU_USERNAME debe ser tu correo completo de MTU.")
    missing = [key for key, value in (("MTU_USERNAME", username), ("MTU_PASSWORD", password)) if not value]
    if missing:
        print(f"En {path} faltan valores para: {', '.join(missing)}.")
    else:
        print(f"Credenciales cargadas desde: {path}")
    return {"username": username, "password": password}


def microsoft_login_origin(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.scheme == "https" and parsed.hostname == "login.microsoftonline.com"
            and parsed.port in (None, 443) and parsed.username is None and parsed.password is None)


def fill_mtu_login(page, credentials: dict, timeout_error) -> bool:
    """Reintenta solo cambios de documento, manteniendo la cuenta introducida."""
    for attempt in range(3):
        try:
            return _fill_mtu_login_once(page, credentials, timeout_error)
        except Exception as exc:
            if not is_navigation_interruption(exc) or attempt == 2:
                raise
            print("Microsoft ha cambiado de página durante el acceso; comprobando el siguiente paso...", flush=True)
            page.wait_for_timeout(250)
    return False


def _fill_mtu_login_once(page, credentials: dict, timeout_error) -> bool:
    """Autorrelleno local de un único intento. La MFA se completa manualmente."""
    username = credentials.get("username", "")
    password = credentials.get("password", "")
    if not username:
        return False
    if not microsoft_login_origin(page.url):
        try:
            page.wait_for_url(re.compile(r"^https://login\.microsoftonline\.com(?::443)?/"),
                              wait_until="domcontentloaded", timeout=15000)
        except timeout_error:
            print("No se ha encontrado el formulario de inicio de sesión de Microsoft.")
            return False
    if not microsoft_login_origin(page.url):
        return False
    changed = bool(credentials.get("_username_selected"))
    credentials["_login_stage"] = "detectar el formulario o selector de cuenta"
    email_field = page.locator('input[name="loginfmt"][type="email"]')
    password_field = page.locator('input[name="passwd"][type="password"]')
    visible = page.locator(":visible")
    other_account = page.get_by_text(re.compile(
        r"^\s*(Usar otra cuenta|Use another account)\s*$", re.I)).and_(visible)
    # Microsoft puede abrir el formulario o el selector de cuentas recordadas.
    # Esperamos cualquiera de las tres pantallas, también si tarda en cargar.
    try:
        email_field.or_(password_field).or_(other_account).and_(visible).first.wait_for(
            state="visible", timeout=15000)
    except timeout_error:
        print("No se ha encontrado el formulario ni el selector de cuentas de Microsoft.")
        return False
    if not microsoft_login_origin(page.url):
        return False
    if other_account.is_visible() and not email_field.is_visible() and not password_field.is_visible():
        credentials["_login_stage"] = "seleccionar la cuenta"
        saved_account = page.get_by_text(re.compile(
            r"^\s*" + re.escape(username) + r"\s*$", re.I)).and_(visible)
        if saved_account.count() == 1:
            credentials["_username_selected"] = True
            saved_account.click(timeout=15000)
            changed = True
            print("Cuenta del .env seleccionada automáticamente.")
        else:
            other_account.click(timeout=15000)
            print("Abriendo 'Usar otra cuenta' para introducir el usuario del .env.")
            try:
                email_field.wait_for(state="visible", timeout=15000)
            except timeout_error:
                print("No ha aparecido el campo de correo después de elegir otra cuenta.")
                return False
    if email_field.is_visible() and microsoft_login_origin(page.url):
        credentials["_login_stage"] = "introducir el correo y pulsar Siguiente"
        email_field.fill(username)
        credentials["_username_selected"] = True
        page.get_by_role("button", name=re.compile(r"^\s*(Next|Siguiente)\s*$", re.I)).click(timeout=15000)
        changed = True
        print("Usuario del .env enviado.")
    if not password:
        return changed
    credentials["_login_stage"] = "esperar el campo de contraseña"
    print("Esperando el campo de contraseña de Microsoft...", flush=True)
    try:
        password_field.wait_for(state="visible", timeout=15000)
    except timeout_error:
        print("No ha aparecido el campo de contraseña; puede haber otro paso pendiente.")
        return changed
    if not microsoft_login_origin(page.url):
        return changed
    credentials["_login_stage"] = "comprobar la cuenta del campo de contraseña"
    # Si acabamos de enviar el usuario, una etiqueta genérica es válida.
    # Si aparece otra cuenta explícita o no sabemos qué cuenta está seleccionada,
    # dejamos que el usuario la cambie antes de enviar la contraseña.
    account_label = password_field.get_attribute("aria-label") or ""
    accounts = {address.casefold() for address in re.findall(
        r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[A-Za-z]{2,}", account_label)}
    if (accounts and accounts != {username.casefold()}) or (not accounts and not changed):
        print("Selecciona la cuenta indicada en MTU_USERNAME en el navegador y vuelve a ejecutar login-mtu.")
        return changed
    credentials["_login_stage"] = "rellenar la contraseña"
    password_field.fill(password)
    credentials["_login_stage"] = "pulsar Iniciar sesión"
    print("Contraseña rellenada. Pulsando Iniciar sesión...", flush=True)
    # Un clic explícito activa el botón de Microsoft aunque Enter no envíe el formulario.
    page.get_by_role("button", name=re.compile(
        r"^\s*(Sign in|Iniciar sesi[oó]n)\s*$", re.I)).click(timeout=15000)
    print("Contraseña del .env enviada. Esperando la respuesta de Microsoft.")
    return True


def safe_login_diagnostic(page, exc, credentials):
    """Solo etiquetas propias, códigos AADSTS y estados; no valores ni errores crudos."""
    raw = str(exc).lower()
    if is_navigation_interruption(exc):
        reason = "cambio de documento durante la navegación"
    elif "strict mode violation" in raw:
        reason = "el selector coincide con varios elementos"
    elif "timeout" in raw:
        reason = "el elemento esperado no apareció o no se pudo pulsar a tiempo"
    else:
        reason = "error del navegador"
    stage = credentials.get("_login_stage", "autorrellenar el acceso")
    detail = f"No se pudo {stage}: {reason}."
    try:
        if microsoft_login_origin(page.url):
            body = page.locator("body").inner_text(timeout=2000)
            codes = sorted(set(re.findall(r"\bAADSTS\d{4,10}\b", body)))
            if codes:
                detail += " Microsoft muestra: " + ", ".join(codes) + "."
            number_visible = page.locator("#idRichContext_DisplaySign:visible").count() > 0
            detail += " Reto de número visible: " + ("sí." if number_visible else "no.")
        else:
            detail += " La página actual está fuera del formulario de Microsoft."
    except Exception:
        detail += " La página está cambiando; no se pudo consultar su estado."
    return detail


def is_navigation_interruption(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(fragment in message for fragment in (
        "execution context was destroyed",
        "cannot find context with specified id",
        "cannot find context with id",
        "unable to adopt element handle from a different document",
    ))


def wait_for_mtu_access(page, timeout_error, timeout_ms=180000) -> bool:
    """Espera la aprobación y muestra solo el número visible del reto MFA.

    El identificador HTML puede cambiar en Microsoft. Si no se reconoce, el
    acceso sigue esperando y se debe consultar el número en el navegador.
    """
    student_sets = page.get_by_role("link", name="Student Sets", exact=True)
    deadline = time.monotonic() + timeout_ms / 1000
    last_number = None
    while time.monotonic() < deadline:
        try:
            if urlparse(page.url).hostname == urlparse(SOURCE).hostname and student_sets.is_visible():
                return True
            if microsoft_login_origin(page.url):
                texts = page.locator("#idRichContext_DisplaySign:visible").all_inner_texts()
                numbers = [s.strip() for s in texts if re.fullmatch(r"[0-9]{1,3}", s.strip())]
                if len(numbers) == 1 and numbers[0] != last_number and microsoft_login_origin(page.url):
                    last_number = numbers[0]
                    print(f"NÚMERO PARA MICROSOFT AUTHENTICATOR: {last_number}", flush=True)
                    print("Introduce ese número en la solicitud de Authenticator del móvil y aprueba.", flush=True)
        except timeout_error:
            pass
        except Exception as exc:
            # Al aprobar MFA, Microsoft sustituye el documento al volver a MTU.
            # Una lectura iniciada justo antes puede perder su contexto JS.
            # Reintentar únicamente ese cambio de documento; los demás errores
            # (incluido cerrar el navegador) deben llegar al diagnóstico exterior.
            if not is_navigation_interruption(exc):
                raise
        remaining = (deadline - time.monotonic()) * 1000
        if remaining <= 0:
            break
        try:
            # Esta espera procesa las transiciones del navegador y evita un bucle activo.
            student_sets.wait_for(state="visible", timeout=min(1000, remaining))
        except timeout_error:
            continue
        except Exception as exc:
            if not is_navigation_interruption(exc):
                raise
            continue
        if urlparse(page.url).hostname == urlparse(SOURCE).hostname:
            return True
    return False


def week_date(label: str, academic_year: int) -> date:
    match = re.fullmatch(WEEK, label.strip(), re.I)
    if not match or match[1].lower() not in MONTHS:
        raise TimetableError(f"Semana desconocida: {label!r}")
    month = MONTHS[match[1].lower()]
    result = date(academic_year if month >= 8 else academic_year + 1, month, int(match[2]))
    if result.weekday() != 0:
        raise TimetableError(f"{label} no es lunes en el curso configurado. Revisa academic_year_start.")
    return result


def week_dates(text: str, academic_year: int) -> set[date]:
    dates = set()
    for part in text.replace("–", "-").replace("—", "-").split(","):
        ends = re.split(r"\s*-\s*", part.strip())
        if len(ends) not in (1, 2):
            raise TimetableError(f"Rango de semanas desconocido: {part}")
        start = week_date(ends[0], academic_year)
        end = week_date(ends[-1], academic_year)
        if end < start or (end - start).days > 371:
            raise TimetableError(f"Rango de semanas inválido: {part}")
        dates.update(start + timedelta(days=n) for n in range(0, (end-start).days+1, 7))
    return dates


def select_timetable_option(page, name: str, label: str, expect) -> str:
    """Selecciona un valor y espera su postback ASP.NET antes de leer otro campo."""
    field = page.locator(f'select[name="{name}"]')
    expect(field).to_be_visible()
    expect(field).to_be_enabled()
    option = field.locator("option").filter(has_text=re.compile(r"^\s*" + re.escape(label) + r"\s*$"))
    expect(option).to_have_count(1)
    value = option.get_attribute("value")
    if value is None:
        value = option.text_content().strip()
    selected = field.locator("option:checked").evaluate_all("els => els.map(e => e.value)")
    # Elegir otra vez el mismo valor puede no disparar ningún onchange.
    if selected != [value]:
        handler = field.get_attribute("onchange") or ""
        postback = re.search(r"__doPostBack|WebForm_DoPostBackWithOptions|\.submit\s*\(", handler, re.I)
        if postback:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                field.select_option(value=value)
        else:
            field.select_option(value=value)
    expect(field).to_have_value(value)
    return value


def fetch_timetable(cfg: dict, state: Path, interactive=False, login_only=False, env_file: Path | None = None) -> dict | None:
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout, Error as BrowserError, expect

    credentials = read_mtu_credentials(env_file if env_file is not None else DEFAULT_ENV_PATH)
    print("Abriendo el navegador de MTU. Mantén abierta su ventana hasta confirmar el acceso.", flush=True)
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                str(state / "mtu-profile"), headless=not interactive,
                viewport={"width": 1440, "height": 1000}, timezone_id=ZONE,
            )
        except BrowserError as exc:
            credentials.clear()
            if "Executable doesn't exist" in str(exc):
                raise TimetableError("Falta Chromium. Ejecuta: python -m playwright install chromium") from None
            raise TimetableError("No se ha podido abrir Chromium. Cierra otras ejecuciones de HorarioMTU y vuelve a intentarlo.") from None
        stage = "abrir la página de MTU"
        try:
            # El perfil persistente normalmente ya abre una pestaña.
            page = context.pages[0] if context.pages else context.new_page()
            page.bring_to_front()
            page.set_default_timeout(30000)
            page.goto(SOURCE, wait_until="domcontentloaded", timeout=60000)
            student_sets = page.get_by_role("link", name="Student Sets", exact=True)
            stage = "detectar la pantalla de acceso"
            # Student Sets solo aparece DESPUÉS del login: no esperarlo a solas
            # durante 15 segundos cuando ya estamos en Microsoft.
            page.wait_for_function("""() =>
                (location.protocol === 'https:' && location.hostname === 'login.microsoftonline.com') ||
                (location.hostname === 'myclasstimetables.mtu.ie' &&
                 [...document.querySelectorAll('a')].some(a =>
                    a.textContent.trim() === 'Student Sets' && a.getClientRects().length > 0))
                """, timeout=30000)
            if not student_sets.is_visible():
                stage = "rellenar el formulario de Microsoft"
                print("Pantalla de Microsoft detectada. Iniciando autorrelleno...", flush=True)
                try:
                    filled = fill_mtu_login(page, credentials, BrowserTimeout)
                except BrowserError as exc:
                    if page.is_closed() or "closed" in str(exc).lower() or "crashed" in str(exc).lower():
                        raise
                    # Los errores de Playwright pueden incluir los argumentos de
                    # fill(). No se muestran para no revelar las credenciales.
                    filled = False
                    print("DIAGNÓSTICO DE ACCESO: " + safe_login_diagnostic(page, exc, credentials), flush=True)
                    if not os.environ.get("GITHUB_ACTIONS"):
                        print("Puedes continuar manualmente en la ventana del navegador.", flush=True)
                finally:
                    credentials.clear()
                stage = "esperar la confirmación del acceso"
                if interactive:
                    print("Esperando el acceso, hasta 3 minutos. Si Microsoft pide aprobación, usa Authenticator en el móvil.", flush=True)
                    print("Si detecto el número de aprobación, aparecerá aquí. Si no aparece, consúltalo en la ventana del navegador.", flush=True)
                signed_in = wait_for_mtu_access(page, BrowserTimeout, timeout_ms=180000 if interactive else 15000)
                if not signed_in:
                    if not interactive:
                        raise LoginRequired("Hay un paso pendiente de acceso a MTU (por ejemplo, Authenticator). Ejecuta: python horario_mtu.py login-mtu")
                    raise LoginRequired("Han pasado 3 minutos sin confirmar el acceso. Ejecuta de nuevo el comando y completa el paso pendiente.")
                try:
                    student_sets.wait_for(state="visible", timeout=15000)
                except BrowserTimeout as exc:
                    raise LoginRequired("No se ha confirmado el acceso al horario de MTU.") from exc
            if urlparse(page.url).hostname != urlparse(SOURCE).hostname:
                raise LoginRequired("El navegador todavía no está en el sitio de horarios de MTU.")
            if login_only:
                print("Sesión MTU confirmada. Se reutilizará mientras la universidad la acepte.")
                return None
            stage = "consultar el horario"
            year_match = re.search(r"timetables\s+(\d{2})-(\d{2})", page.locator("body").inner_text(), re.I)
            if not year_match or 2000 + int(year_match[1]) != cfg["academic_year_start"]:
                raise TimetableError("No coincide el curso académico de la web con academic_year_start.")
            stage = "abrir Student Sets"
            print("Abriendo Student Sets...", flush=True)
            with page.expect_navigation(wait_until="domcontentloaded"):
                student_sets.click()
            stage = "seleccionar el departamento"
            print(f"Seleccionando departamento: {cfg['department']}", flush=True)
            select_timetable_option(page, "dlFilter3", cfg["department"], expect)
            stage = "seleccionar el grupo de ciberseguridad"
            print(f"Seleccionando grupo: {cfg['student_set']}", flush=True)
            group_value = select_timetable_option(page, "dlObject", cfg["student_set"], expect)
            groups = page.locator('select[name="dlObject"]')
            stage = "leer las semanas disponibles"
            weeks = page.locator('select[name="lbWeeks"]')
            expect(weeks).to_be_visible()
            expect(weeks.locator("option")).not_to_have_count(0)
            week_options = weeks.locator("option").evaluate_all(
                "els => els.map(e => ({label:e.textContent.trim(), value:e.value}))")
            semester = [o for o in week_options if o["label"] == cfg["semester"]]
            current = [o for o in week_options if re.search(WEEK, o["label"], re.I)]
            if len(semester) != 1 or len(current) != 1:
                raise TimetableError("No se pueden identificar las semanas del semestre.")
            stage = "seleccionar el semestre"
            print(f"Seleccionando semestre: {cfg['semester']}", flush=True)
            select_timetable_option(page, "lbWeeks", cfg["semester"], expect)
            stage = "seleccionar todos los días"
            print("Seleccionando All Days...", flush=True)
            select_timetable_option(page, "lbDays", "All Days", expect)
            stage = "seleccionar todas las horas"
            print("Seleccionando All Day (8-10)...", flush=True)
            select_timetable_option(page, "dlPeriod", "All Day (8-10)", expect)
            stage = "comprobar las opciones seleccionadas"
            expect(groups).to_have_value(group_value)
            expect(weeks).to_have_value(semester[0]["value"])
            stage = "mostrar View Timetable"
            print("Abriendo el horario...", flush=True)
            page.get_by_role("button", name="View Timetable", exact=True).click()
            page.get_by_role("link", name="Back To Selection", exact=True).wait_for()
            stage = "leer la tabla del horario"
            text = page.locator("body").inner_text()
            if f"Student Set {cfg['student_set']}" not in re.sub(r"\s+", " ", text):
                raise TimetableError("El informe no corresponde al grupo configurado.")
            grids = page.locator("table").evaluate_all(GRID_JS)
            return {
                "schema": 1, "student_set": cfg["student_set"],
                "academic_year_start": cfg["academic_year_start"], "semester": cfg["semester"],
                "semester_week_numbers": [int(n) for n in semester[0]["value"].split(";")],
                "reference_week": {"label": current[0]["label"], "number": int(current[0]["value"])},
                "source_url": SOURCE, "fetched_at": datetime.now(timezone.utc).isoformat(), "grids": grids,
            }
        except (BrowserError, AssertionError) as exc:
            # No imprimir el error completo: puede contener argumentos de fill().
            if "closed" in str(exc).lower() or "crashed" in str(exc).lower():
                raise LoginRequired(
                    f"El navegador o la pestaña se cerró o falló al {stage}. "
                    "Esto no indica una contraseña incorrecta. Ejecuta otra vez login-mtu "
                    "y mantén abierta la ventana que abre el programa. Si se cierra sola, "
                    "indica ese detalle junto con este mensaje."
                ) from None
            detail = ""
            if stage != "rellenar el formulario de Microsoft":
                # Estas etapas solo consultan elementos estáticos: no ejecutan
                # fill(). Mostrar la primera línea, sin URLs ni correos, permite
                # diagnosticar otros fallos sin volcar el registro de Playwright.
                first_line = str(exc).splitlines()[0] if str(exc) else ""
                first_line = re.sub(r"https?://\S+", "[URL]", first_line)
                first_line = re.sub(r"[^\s@]+@[^\s@]+", "[correo]", first_line)
                detail = f" Detalle: {first_line[:350]}" if first_line else ""
            message = f"Fallo del navegador al {stage} ({type(exc).__name__}).{detail}"
            if interactive and not page.is_closed():
                print(f"Error: {message}", flush=True)
                try:
                    input("He dejado el navegador abierto para revisar la pantalla. Pulsa Intro aquí para cerrarlo: ")
                except EOFError:
                    pass
            raise TimetableError(message) from None
        finally:
            credentials.clear()
            try:
                context.close()
            except BrowserError:
                pass  # No ocultar el error original si el navegador ya se cerró.


def parse_timetable(raw: dict) -> list[dict]:
    """Reconstruye colspan/rowspan y expande solamente las semanas indicadas."""
    if raw.get("schema") != 1 or len(raw.get("grids", [])) != 1:
        raise TimetableError("Se esperaba una única tabla de horario completa.")
    year = raw["academic_year_start"]
    reference = re.search(WEEK, raw["reference_week"]["label"], re.I)
    if not reference:
        raise TimetableError("No se ha podido fechar la semana de referencia.")
    week_one = week_date(reference[0], year) - timedelta(weeks=raw["reference_week"]["number"] - 1)
    allowed = {week_one + timedelta(weeks=n-1) for n in raw["semester_week_numbers"]}
    rows = raw["grids"][0]["rows"]
    occupied = {}
    placed = []
    for r, row in enumerate(rows):
        col = 0
        line = []
        for cell in row:
            while (r, col) in occupied:
                col += 1
            width, height = int(cell["colspan"]), int(cell["rowspan"])
            if width < 1 or height < 1:
                raise TimetableError("La tabla contiene una celda inválida.")
            for rr in range(r, r+height):
                for cc in range(col, col+width):
                    if (rr, cc) in occupied:
                        raise TimetableError("La estructura de la tabla se solapa.")
                    occupied[rr, cc] = cell
            line.append((col, cell))
            col += width
        placed.append(line)
    headings = {col: cell["text"] for col, cell in placed[0] if re.fullmatch(r"\d{1,2}:\d{2}", cell["text"])}
    if not headings or set(headings) != set(range(1, 57)):
        raise TimetableError("Falta el intervalo completo 08:00–22:00 del informe.")
    for col, label in headings.items():
        hours, minutes = map(int, label.split(":"))
        if hours*60+minutes != 480 + (col-1)*15:
            raise TimetableError("No se reconoce la escala horaria de la tabla.")
    zone = ZoneInfo(ZONE)
    occurrences = {}
    seen_days = set()
    for r, row in enumerate(placed[1:], start=1):
        day_name = occupied.get((r, 0), {}).get("text", "")
        if day_name not in DAYS:
            raise TimetableError("No se reconoce el día de una fila del horario.")
        seen_days.add(day_name)
        if any((r, c) not in occupied for c in range(57)):
            raise TimetableError("El horario recibido está incompleto.")
        for col, cell in row:
            if col == 0 or not cell["text"].strip():
                continue
            parts = cell.get("parts", [])
            if len(parts) != 3 or len(parts[0]) != 1 or len(parts[0][0]) != 2:
                raise TimetableError(f"Formato de clase no reconocido: {cell['text']}")
            room, kind = parts[0][0]
            if len(parts[1]) != 1 or len(parts[1][0]) != 1 or len(parts[2]) != 1 or len(parts[2][0]) != 1:
                raise TimetableError("La estructura interna de una clase ha cambiado.")
            subject, weeks_text = parts[1][0][0], parts[2][0][0]
            weeks = week_dates(weeks_text, year)
            if not weeks <= allowed:
                raise TimetableError("Una clase contiene semanas fuera del semestre seleccionado.")
            start_minutes = 480+(col-1)*15
            end_minutes = start_minutes+cell["colspan"]*15
            if not subject or not kind or not (480 <= start_minutes < end_minutes <= 1320):
                raise TimetableError("Clase sin título/tipo o con horas inválidas.")
            for monday in weeks:
                actual_day = monday+timedelta(days=DAYS[day_name])
                midnight = datetime.combine(actual_day, datetime.min.time(), tzinfo=zone)
                start = midnight+timedelta(minutes=start_minutes)
                end = midnight+timedelta(minutes=end_minutes)
                ident = (subject, kind, start.isoformat(), end.isoformat(), room)
                occurrences[ident] = {"subject": subject, "kind": kind, "room": room,
                                      "start": start.isoformat(), "end": end.isoformat()}
    if seen_days != set(DAYS) or not occurrences:
        raise TimetableError("El informe está vacío o no incluye los siete días. No se sincroniza.")
    events = sorted(occurrences.values(), key=lambda e: (e["start"], e["subject"], e["kind"], e["room"]))
    scope = f"mtu:{year}:{raw['semester']}:{raw['student_set']}"
    by_subject_day = defaultdict(list)
    for event in events:
        by_subject_day[event["subject"], event["kind"], event["start"][:10]].append(event)
    for (subject, kind, day), group in by_subject_day.items():
        for index, event in enumerate(group):
            seed = json.dumps([scope, subject, kind, day, index], ensure_ascii=False)
            event["key"] = hashlib.sha256(seed.encode()).hexdigest()
            event["scope"] = scope
    return events


def target_week(today: date | None = None) -> tuple[date, date]:
    """Lunes incluido y lunes siguiente excluido, según la fecha de Irlanda."""
    if today is None:
        today = datetime.now(ZoneInfo(ZONE)).date()
    monday = today - timedelta(days=today.weekday())
    if today.weekday() >= 5:
        monday += timedelta(days=7)
    return monday, monday + timedelta(days=7)


def events_in_week(events: list[dict], monday: date) -> list[dict]:
    end = monday + timedelta(days=7)
    zone = ZoneInfo(ZONE)
    return sorted(
        [event for event in events
         if monday <= datetime.fromisoformat(event["start"]).astimezone(zone).date() < end],
        key=lambda event: (datetime.fromisoformat(event["start"]), event["subject"]),
    )


def format_week_message(events: list[dict], monday: date) -> str:
    """Resumen reutilizable para consola, archivo de texto o envío de mensajes."""
    sunday = monday + timedelta(days=6)
    lines = ["HORARIO MTU", f"Semana del {monday:%d/%m/%Y} al {sunday:%d/%m/%Y}",
             "Horas de Irlanda (Europe/Dublin)."]
    if not events:
        lines.extend(["", "No hay clases programadas para esta semana en el horario consultado."])
    names = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    previous_day = None
    for event in sorted(events, key=lambda e: datetime.fromisoformat(e["start"])):
        start = datetime.fromisoformat(event["start"]).astimezone(ZoneInfo(ZONE))
        end = datetime.fromisoformat(event["end"]).astimezone(ZoneInfo(ZONE))
        if start.date() != previous_day:
            lines.extend(["", f"{names[start.weekday()]} {start:%d/%m}"])
            previous_day = start.date()
        kind = f" ({event['kind']})" if event.get("kind") else ""
        room = f" · Aula: {event['room']}" if event.get("room") else ""
        lines.append(f"{start:%H:%M}–{end:%H:%M} · {event['subject']}{kind}{room}")
    return "\n".join(lines) + "\n"


def export_week_preview(events: list[dict], out: Path, today: date | None = None):
    monday, _ = target_week(today)
    selected = events_in_week(events, monday)
    export_preview(selected, out)
    message = format_week_message(selected, monday)
    (out / "horario.txt").write_text(message, encoding="utf-8")
    print("\n" + message)
    print(f"Resumen: {out.resolve() / 'horario.txt'}")
    print(f"Calendario de esta semana: {out.resolve() / 'horario.ics'}")


def export_preview(events: list[dict], out: Path):
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "clases.json", events)
    with (out / "clases.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Fecha", "Día", "Inicio (Irlanda)", "Fin (Irlanda)", "Asignatura", "Tipo", "Aula"])
        for e in events:
            dt = datetime.fromisoformat(e["start"])
            writer.writerow([dt.date(), ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][dt.weekday()],
                             e["start"][11:16], e["end"][11:16], e["subject"], e["kind"], e["room"]])
    def escape(text):
        return text.replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")
    def utc(text):
        return datetime.fromisoformat(text).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//HorarioMTU//ES", "CALSCALE:GREGORIAN"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for e in events:
        lines.extend(["BEGIN:VEVENT", f"UID:{e['key']}@horariomtu.local", f"DTSTAMP:{stamp}",
                      f"DTSTART:{utc(e['start'])}", f"DTEND:{utc(e['end'])}",
                      "SUMMARY:"+escape(f"{e['subject']} ({e['kind']})"),
                      "LOCATION:"+escape(e["room"]), "DESCRIPTION:"+escape(e["scope"]+"\n"+SOURCE), "END:VEVENT"])
    lines.append("END:VCALENDAR")
    # iCalendar limita cada línea a 75 octetos, sin cortar caracteres UTF-8.
    folded = []
    for line in lines:
        current = ""
        for char in line:
            if len((current+char).encode()) > 75:
                folded.append(current)
                current = " "
            current += char
        folded.append(current)
    (out / "horario.ics").write_bytes(("\r\n".join(folded)+"\r\n").encode())
    print(f"{len(events)} clases exportadas.")
    print(f"Vista previa: {out.resolve() / 'clases.csv'}")


def read_email_settings(env_file: Path) -> dict:
    """Configuración independiente de las credenciales MTU; nunca las reutiliza.

    Añadir al .env (datos del proveedor de correo):
    SMTP_HOST=''
    SMTP_PORT='465'
    SMTP_SECURITY='ssl'  # ssl o starttls
    SMTP_USERNAME=''
    SMTP_PASSWORD=''  # contraseña de aplicación si el proveedor la admite
    MAIL_FROM=''  # opcional: por defecto SMTP_USERNAME
    MAIL_TO=''  # una dirección, destinatario del horario

    Solo proveedores que permitan SMTP con usuario/contraseña de aplicación.
    No implementa OAuth de Outlook/Microsoft 365.
    """
    from dotenv import dotenv_values
    values = dotenv_values(env_file, interpolate=False) if env_file.exists() else {}
    def setting(name, default=""):
        return values.get(name) or os.environ.get(name) or default
    cfg = {"host": setting("SMTP_HOST").strip(),
           "username": setting("SMTP_USERNAME").strip(),
           "password": setting("SMTP_PASSWORD"),
           "to": setting("MAIL_TO").strip(),
           "security": setting("SMTP_SECURITY", "ssl").strip().lower()}
    required = {"host": "SMTP_HOST", "username": "SMTP_USERNAME",
                "password": "SMTP_PASSWORD", "to": "MAIL_TO"}
    missing = [name for key, name in required.items() if not cfg[key]]
    if missing:
        raise TimetableError("Falta configurar el correo en .env: " + ", ".join(missing)
                             + ". El acceso a MTU aún no se ha iniciado.")
    cfg["from"] = setting("MAIL_FROM", cfg["username"]).strip()
    if cfg["security"] not in ("ssl", "starttls"):
        raise TimetableError("SMTP_SECURITY debe ser ssl (normalmente 465) o starttls (normalmente 587).")
    try:
        cfg["port"] = int(setting("SMTP_PORT", "465" if cfg["security"] == "ssl" else "587"))
        if not 1 <= cfg["port"] <= 65535:
            raise ValueError
    except ValueError:
        raise TimetableError("SMTP_PORT debe ser un puerto válido.") from None
    for key in ("from", "to"):
        address = cfg[key]
        if (any(c in address for c in "\r\n,;") or parseaddr(address)[1] != address
                or address.count("@") != 1 or any(c.isspace() for c in address)
                or not all(address.split("@"))):
            raise TimetableError(f"{'MAIL_FROM' if key == 'from' else 'MAIL_TO'} debe contener una sola dirección de correo.")
    return cfg


def send_week_email(cfg: dict, out: Path):
    """Envía exactamente el resumen y el ICS recién exportados, sin reintentos."""
    summary = (out / "horario.txt").read_text(encoding="utf-8")
    message = EmailMessage()
    message["From"] = cfg["from"]
    message["To"] = cfg["to"]
    message["Subject"] = "Horario MTU — " + summary.splitlines()[1]
    message.set_content(summary)
    message.add_attachment((out / "horario.ics").read_bytes(), maintype="text",
                           subtype="calendar", filename="horario.ics")
    tls = ssl.create_default_context()
    try:
        if cfg["security"] == "ssl":
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30, context=tls)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30)
        try:
            if cfg["security"] == "starttls":
                server.ehlo()
                server.starttls(context=tls)
                server.ehlo()
            server.login(cfg["username"], cfg["password"])
            server.send_message(message, from_addr=cfg["from"], to_addrs=[cfg["to"]])
        finally:
            # Un fallo al cerrar no invalida la aceptación previa del mensaje.
            server.close()
    except smtplib.SMTPAuthenticationError:
        raise TimetableError("El servidor rechazó el acceso al correo. Comprueba la configuración SMTP y la contraseña de aplicación; la contraseña MTU no se usa aquí. Los archivos siguen en salida.") from None
    except (smtplib.SMTPException, OSError) as exc:
        raise TimetableError(f"No se pudo confirmar el envío ({type(exc).__name__}). Los archivos siguen en {out.resolve()}. Comprueba tu bandeja antes de repetir el envío.") from None
    print("El servidor de correo ha aceptado el mensaje con el horario y el calendario adjunto.")


class GraphClient:
    def __init__(self, cfg: dict, state: Path, interactive=False, allow_interactive=False):
        import msal
        from msal_extensions import PersistedTokenCache, build_encrypted_persistence
        if not cfg.get("client_id"):
            raise TimetableError("Falta client_id en config.json. Consulta LEEME.md; puedes usar preview sin él.")
        try:
            uuid.UUID(cfg["client_id"])
        except ValueError as exc:
            raise TimetableError("client_id debe ser el identificador de una app de Microsoft Entra.") from exc
        tenant = cfg.get("tenant", "common")
        if not re.fullmatch(r"[a-zA-Z0-9.-]+", tenant):
            raise TimetableError("tenant inválido.")
        try:
            cache = PersistedTokenCache(build_encrypted_persistence(str(state / "outlook-cache.bin")))
        except Exception as exc:
            raise TimetableError("No se ha podido abrir el almacén cifrado de Microsoft. En Linux necesitas libsecret y un llavero desbloqueado.") from exc
        self.app = msal.PublicClientApplication(cfg["client_id"], authority=f"https://login.microsoftonline.com/{tenant}", token_cache=cache)
        self.binding_path = state / "outlook-account.json"
        binding = load_json(self.binding_path, {})
        result = None
        signed_in_interactively = False
        if interactive:
            result = self.app.acquire_token_interactive(scopes=SCOPES, prompt="select_account")
            signed_in_interactively = True
        else:
            matches = [a for a in self.app.get_accounts() if a.get("home_account_id") == binding.get("home_account_id")]
            if len(matches) == 1:
                result = self.app.acquire_token_silent(SCOPES, account=matches[0])
            if (not result or "access_token" not in result) and allow_interactive:
                print("Outlook necesita autorización. Se abrirá Microsoft en el navegador; completa el acceso y continuaré automáticamente.", flush=True)
                options = {"scopes": SCOPES, "prompt": "select_account"}
                hint = binding.get("address") or cfg.get("expected_account")
                if hint:
                    options["login_hint"] = hint
                result = self.app.acquire_token_interactive(**options)
                signed_in_interactively = True
        if not result or "access_token" not in result:
            raise LoginRequired("No se ha obtenido el permiso de Outlook. Ejecuta sync --interactive --apply para autorizar y sincronizar en la misma ejecución. Si Microsoft pide aprobación del administrador, debe darla el administrador de esa cuenta.")
        self.token = result["access_token"]
        self.me = self.request("GET", "/me", params={"$select": "id,displayName,mail,userPrincipalName"})
        self.address = self.me.get("mail") or self.me.get("userPrincipalName") or self.me["id"]
        expected = cfg.get("expected_account", "").strip().casefold()
        valid_names = {str(self.me.get(k) or "").casefold() for k in ("mail", "userPrincipalName")}
        if expected and expected not in valid_names:
            raise TimetableError(f"La cuenta elegida ({self.address}) no coincide con expected_account.")
        if not interactive and binding.get("mailbox_id") and self.me["id"] != binding["mailbox_id"]:
            raise TimetableError("La cuenta de destino ha cambiado. Vuelve a ejecutar login-outlook.")
        if signed_in_interactively:
            claims = result.get("id_token_claims", {})
            matches = [a for a in self.app.get_accounts() if a.get("local_account_id") == claims.get("oid") and a.get("realm") == claims.get("tid")]
            if len(matches) != 1:
                matches = [a for a in self.app.get_accounts() if str(a.get("username", "")).casefold() in valid_names]
            if len(matches) != 1:
                raise TimetableError("No se ha podido fijar una única cuenta en la caché. Revisa la selección de cuenta.")
            write_json(self.binding_path, {"home_account_id": matches[0]["home_account_id"],
                                           "mailbox_id": self.me["id"], "address": self.address})

    def request(self, method, path, body=None, params=None, etag=None):
        url = path if path.startswith("https://") else GRAPH+path
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "graph.microsoft.com" or not parsed.path.startswith("/v1.0/"):
            raise TimetableError("Microsoft devolvió un destino inesperado.")
        if params:
            url += ("&" if "?" in url else "?")+urlencode(params)
        headers = {"Authorization": "Bearer "+self.token, "Accept": "application/json", "Prefer": 'outlook.timezone="UTC"'}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if etag:
            headers["If-Match"] = etag
        for attempt in range(4):
            try:
                with urlopen(Request(url, data=data, headers=headers, method=method), timeout=35) as response:
                    content = response.read()
                    return json.loads(content) if content else None
            except HTTPError as exc:
                if exc.code == 401:
                    raise LoginRequired("Microsoft ha solicitado renovar el acceso. Ejecuta login-outlook.") from exc
                if exc.code == 429 and attempt < 3:
                    wait = exc.headers.get("Retry-After", "5")
                    time.sleep(min(int(wait) if wait.isdigit() else 5, 30))
                    continue
                if exc.code in (409, 412):
                    raise TimetableError("El calendario cambió durante la sincronización. Ejecuta sync otra vez para revisar el plan.") from exc
                raise TimetableError(f"Microsoft Graph respondió HTTP {exc.code}. No se han continuado las operaciones pendientes.") from exc
            except (URLError, TimeoutError) as exc:
                raise TimetableError("No se ha confirmado la respuesta de Microsoft. Repite sync para reconciliar el estado antes de volver a aplicar.") from exc
        raise TimetableError("Microsoft ha limitado las peticiones. Inténtalo más tarde.")

    def pages(self, path, params=None):
        result = []
        while path:
            response = self.request("GET", path, params=params)
            if not isinstance(response.get("value"), list):
                raise TimetableError("Respuesta de calendario incompleta.")
            result.extend(response["value"])
            path = response.get("@odata.nextLink")
            params = None
        return result


def payload(event: dict, cfg: dict) -> dict:
    def graph_time(value):
        dt = datetime.fromisoformat(value).astimezone(timezone.utc)
        return {"dateTime": dt.replace(tzinfo=None).isoformat(timespec="seconds"), "timeZone": "UTC"}
    description = f"{event['subject']} — {event['kind']}\nGrupo: {cfg['student_set']}\nHorario local: Irlanda\nFuente: {SOURCE}"
    return {"subject": f"{event['subject']} ({event['kind']})", "start": graph_time(event["start"]),
            "end": graph_time(event["end"]), "location": {"displayName": event["room"]},
            "body": {"contentType": "text", "content": description}, "showAs": "busy",
            "isReminderOn": cfg["reminder_minutes"] >= 0,
            "reminderMinutesBeforeStart": max(0, cfg["reminder_minutes"])}


def event_meta(event):
    props = [p["value"] for p in event.get("singleValueExtendedProperties", []) if p.get("id") == PROPERTY]
    if len(props) != 1:
        return None
    try:
        value = json.loads(props[0])
        if value.get("version") == 1 and all(isinstance(value.get(k), str) for k in ("key", "scope", "fingerprint")):
            return value
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def graph_utc(value):
    if value.get("timeZone") != "UTC":
        raise TimetableError("Microsoft no devolvió las horas en UTC.")
    dt = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def sync_plan(events: list[dict], existing: list[dict], cfg: dict, today: date) -> list[dict]:
    scope = events[0]["scope"]
    desired = {e["key"]: e for e in events if date.fromisoformat(e["start"][:10]) >= today}
    managed = {}
    for old in existing:
        meta = event_meta(old)
        if not meta or meta["scope"] != scope:
            continue
        if old.get("attendees") or old.get("type", "singleInstance") != "singleInstance":
            raise TimetableError("Una clase gestionada se ha convertido en reunión o serie recurrente. Revísala en Outlook.")
        if meta["key"] in managed:
            raise TimetableError("Hay dos eventos gestionados con la misma identidad. Revisa los duplicados en Outlook.")
        managed[meta["key"]] = old
    plan = []
    for key, e in desired.items():
        body = payload(e, cfg)
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        meta = {"version": 1, "key": key, "scope": scope, "fingerprint": fingerprint}
        body["singleValueExtendedProperties"] = [{"id": PROPERTY, "value": json.dumps(meta, sort_keys=True)}]
        old = managed.get(key)
        if old is None:
            body["transactionId"] = str(uuid.uuid5(uuid.NAMESPACE_URL, scope+":"+key))
            plan.append({"action": "create", "key": key, "body": body})
        else:
            same = (event_meta(old)["fingerprint"] == fingerprint and not old.get("isCancelled")
                    and old.get("subject") == body["subject"]
                    and old.get("location", {}).get("displayName", "") == e["room"]
                    and graph_utc(old["start"]) == graph_utc(body["start"])
                    and graph_utc(old["end"]) == graph_utc(body["end"]))
            if not same:
                plan.append({"action": "update", "key": key, "id": old["id"],
                             "etag": old.get("@odata.etag"), "body": body})
    for key, old in managed.items():
        if key not in desired and graph_utc(old["start"]).astimezone(ZoneInfo(ZONE)).date() >= today:
            plan.append({"action": "remove", "key": key, "id": old["id"],
                         "etag": old.get("@odata.etag"), "subject": old.get("subject"), "start": old["start"]})
    return plan


def synchronize(events: list[dict], cfg: dict, state: Path, out: Path, apply=False, remove_missing=False, interactive=False):
    client = GraphClient(cfg, state, allow_interactive=interactive)
    calendars = client.pages("/me/calendars", {"$select": "id,name,canEdit", "$top": "100"})
    matches = [c for c in calendars if c["name"] == cfg["calendar_name"]]
    if len(matches) > 1:
        raise TimetableError("Hay varios calendarios con ese nombre. Usa un nombre único en config.json.")
    calendar = matches[0] if matches else None
    if calendar and not calendar.get("canEdit", False):
        raise TimetableError("La cuenta no puede editar el calendario elegido.")
    existing = []
    if calendar:
        existing = client.pages(f"/me/calendars/{quote(calendar['id'], safe='')}/events", {
            "$expand": f"singleValueExtendedProperties($filter=id eq '{PROPERTY}')", "$top": "100",
            "$select": "id,subject,start,end,location,attendees,type,isCancelled",
        })
    today = datetime.now(ZoneInfo(ZONE)).date()
    plan = sync_plan(events, existing, cfg, today)
    write_json(out / "plan_outlook.json", {"account": client.address, "calendar": cfg["calendar_name"],
                                          "create_calendar": calendar is None, "operations": plan})
    counts = {kind: sum(p["action"] == kind for p in plan) for kind in ("create", "update", "remove")}
    print(f"Destino: {client.address} / {cfg['calendar_name']}")
    print(f"Plan: {counts['create']} nuevas, {counts['update']} cambios, {counts['remove']} ausentes en la web.")
    if not apply:
        print("Vista previa terminada. Usa sync --apply para añadir/actualizar; --remove-missing habilita retirar las ausentes.")
        return
    actionable = [p for p in plan if p["action"] != "remove" or remove_missing]
    if not actionable:
        print("No hay cambios que aplicar con las opciones elegidas.")
        return
    if calendar is None:
        calendar = client.request("POST", "/me/calendars", {"name": cfg["calendar_name"]})
    route = f"/me/calendars/{quote(calendar['id'], safe='')}/events"
    # Primero altas y cambios; una lectura/fallo previo impide llegar a los borrados.
    for op in actionable:
        if op["action"] == "create":
            client.request("POST", route, op["body"])
        elif op["action"] == "update":
            client.request("PATCH", route+"/"+quote(op["id"], safe=""), op["body"], etag=op.get("etag"))
        else:
            client.request("DELETE", route+"/"+quote(op["id"], safe=""), etag=op.get("etag"))
    print("Sincronización completada.")


def main():
    print(f"HorarioMTU v11 | Archivo: {Path(__file__).resolve()}")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "crear-env", "login-mtu", "preview", "login-outlook", "sync"])
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--state-dir", type=Path, default=state_directory())
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH,
                        help="Archivo de credenciales MTU. Por defecto, .env junto a horario_mtu.py.")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("salida"))
    parser.add_argument("--interactive", action="store_true", help="Permite completar el acceso a MTU y Outlook durante la misma ejecución.")
    parser.add_argument("--email", action="store_true", help="preview: envía el resumen semanal y horario.ics usando SMTP_* y MAIL_TO del .env.")
    parser.add_argument("--fixture", type=Path, help="Solo preview: comprueba el parser con una muestra guardada.")
    parser.add_argument("--apply", action="store_true", help="sync: aplica las altas y modificaciones.")
    parser.add_argument("--remove-missing", action="store_true", help="sync --apply: retira clases futuras gestionadas que ya no estén en la web.")
    args = parser.parse_args()
    env_file = args.env_file
    if args.email and (args.command != "preview" or args.fixture):
        raise TimetableError("--email solo se admite con preview consultando la web, sin --fixture.")
    email_cfg = read_email_settings(env_file) if args.email else None
    if args.command == "crear-env":
        create_env_template(env_file)
        return
    if args.command == "init":
        if args.config.exists():
            raise TimetableError("Ya existe config.json; no se ha sobrescrito.")
        write_json(args.config, DEFAULTS)
        print(f"Configuración creada: {args.config.resolve()}")
        return
    cfg = DEFAULTS | load_json(args.config, {})
    if args.fixture and args.command != "preview":
        raise TimetableError("Las muestras guardadas solo se admiten para preview. sync siempre consulta la web.")
    if args.apply and args.command != "sync":
        raise TimetableError("--apply solo se admite con sync.")
    if args.remove_missing and not args.apply:
        raise TimetableError("--remove-missing necesita sync --apply.")
    if args.fixture:
        export_week_preview(parse_timetable(load_json(args.fixture)), args.out)
        return
    if args.command == "sync" and not cfg.get("client_id"):
        raise TimetableError("Falta client_id en config.json para conectar con Outlook. Consulta LEEME.md. Esa configuración se realiza una sola vez; después usa sync --interactive --apply.")
    from filelock import FileLock, Timeout as LockTimeout
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with FileLock(str(args.state_dir / "program.lock"), timeout=0):
            if args.command == "login-mtu":
                fetch_timetable(cfg, args.state_dir, interactive=True, login_only=True, env_file=env_file)
            elif args.command == "login-outlook":
                client = GraphClient(cfg, args.state_dir, interactive=True)
                print(f"Cuenta de Outlook fijada: {client.address}")
            else:
                raw = fetch_timetable(cfg, args.state_dir, interactive=args.interactive, env_file=env_file)
                events = parse_timetable(raw)
                export_week_preview(events, args.out)
                write_json(args.out / "horario_fuente.json", raw)
                if email_cfg:
                    send_week_email(email_cfg, args.out)
                if args.command == "sync":
                    # No pasar solo una semana: --remove-missing podría borrar
                    # clases de otras semanas del calendario ya sincronizado.
                    print("Outlook: se sincroniza el semestre completo. Los archivos de vista previa son semanales.")
                    synchronize(events, cfg, args.state_dir, args.out, args.apply, args.remove_missing,
                                interactive=args.interactive)
    except LockTimeout as exc:
        raise TimetableError("Ya hay otra ejecución de HorarioMTU en curso.") from exc


if __name__ == "__main__":
    try:
        main()
    except LoginRequired as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
    except (TimetableError, ValueError, KeyError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ImportError as exc:
        print(f"Falta una dependencia ({exc.name}). Ejecuta: python -m pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)
