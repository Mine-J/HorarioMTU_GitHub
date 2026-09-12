# Horario MTU desde GitHub y el iPhone

GitHub ejecuta el programa y el navegador en un ordenador temporal. Tu PC puede estar apagado. El móvil sirve para lanzar la tarea, leer el número de Authenticator y aprobar el acceso. Al terminar puedes leer el horario en GitHub, descargarlo y, si configuras el correo, recibirlo por email.

## Preparación, una vez

1. Crea un repositorio privado en GitHub, por ejemplo `horario-mtu`.
2. Descomprime el paquete y sube su contenido a la raíz de la rama principal (`main`). No subas el ZIP como único archivo. Deben quedar `horario_mtu.py`, `requirements.txt`, `scripts/run_github.py` y **`.github/workflows/horario.yml`** en sus rutas exactas. Si el cargador web omite `.github`, usa **Add file → Create new file**, escribe `.github/workflows/horario.yml` como nombre y pega el contenido del archivo incluido.
3. En el repositorio entra en **Settings → Secrets and variables → Actions → New repository secret**.
4. Nombre: **MTU_ENV**. Valor: pega el contenido de tu `.env` que ya funciona. Debe incluir las líneas `MTU_USERNAME='...'` y `MTU_PASSWORD='...'`. Guarda las credenciales en este secreto, no en un archivo del repositorio.

## Ejecutarlo desde Safari en el iPhone

1. Abre el repositorio en GitHub.
2. Ve a **Actions → Horario MTU → Run workflow**.
3. En la primera prueba deja desmarcado **Enviar también por correo** y pulsa **Run workflow**.
4. Abre la ejecución, luego **Consultar horario**, y despliega **Abrir este paso para ver el numero de Authenticator**. Primero se instalan Python y Chromium; espera a que termine esa instalación.
5. Cuando Microsoft lo pida, aparecerá **NÚMERO PARA MICROSOFT AUTHENTICATOR: …**. Recuerda el número, cambia a Authenticator, aprueba la solicitud que acabas de iniciar y vuelve a Safari. El programa espera hasta tres minutos; el reto de Microsoft puede caducar antes.
6. Al terminar, verás el resumen en la página de la ejecución. En **Artifacts → horario-semanal** puedes descargar `horario.txt`, `horario.ics`, `clases.csv` y `clases.json`.

De lunes a viernes muestra la semana actual completa. En sábado o domingo muestra la siguiente, según la fecha de Irlanda. Las semanas incluyen lunes a domingo y el resumen muestra los días con clases. Si la semana no tiene clases, lo indica.

## Envío por correo

El envío está implementado para proveedores que permitan SMTP con contraseña de aplicación. Falta elegir y configurar tu cuenta remitente; no necesitas que coincida con la cuenta MTU ni con la cuenta destinataria.

Cuando conozcas los datos de tu proveedor, añade estas líneas al mismo secreto **MTU_ENV**, conservando las credenciales MTU:

```dotenv
SMTP_HOST='servidor-smtp-del-proveedor'
SMTP_PORT='465'
SMTP_SECURITY='ssl'
SMTP_USERNAME='cuenta-remitente'
SMTP_PASSWORD='contraseña-de-aplicación'
MAIL_TO='direccion-destinataria'
```

`MAIL_FROM` es opcional y por defecto coincide con `SMTP_USERNAME`. Si tu proveedor indica STARTTLS, usa `SMTP_SECURITY='starttls'` y normalmente `SMTP_PORT='587'`. Esta versión no implementa OAuth de Outlook/Microsoft 365. No presupone que la contraseña de la universidad permita enviar correo por SMTP.

Después marca **Enviar también por correo** al lanzar la tarea. En la misma ejecución se inicia sesión, se obtiene el horario y se envía el resumen con `horario.ics` adjunto. Si el correo falla después de exportar, los archivos siguen disponibles como artefactos. Comprueba tu bandeja antes de repetir para evitar duplicados.

## Límites y diagnóstico

- Cada ejecución empieza con un navegador nuevo. Es probable que Microsoft pida autenticación. Esta configuración se lanza manualmente para que puedas aprobarla; no permite aprobar MFA automáticamente.
- GitHub muestra el registro de texto, no la ventana del navegador virtual. Si Microsoft cambia la pantalla y no se puede leer el número, o pide otro paso que requiera hacer clic, habrá que adaptar el programa.
- No se ha probado el acceso real a tu cuenta desde GitHub. Si MTU bloquea los servidores de GitHub por acceso condicional (por ejemplo `AADSTS53003`), esta configuración no evita la restricción: habría que consultarlo con TI de MTU.
- Los runners consumen los minutos de GitHub Actions de tu cuenta. Puedes comprobar la disponibilidad y el consumo en la facturación de GitHub; el trabajo tiene un máximo de 15 minutos.
- Se conserva durante siete días el artefacto con el horario. No se suben como artefactos el `.env`, el perfil del navegador ni el archivo fuente del semestre completo.
- Si **Run workflow** no aparece, comprueba que `.github/workflows/horario.yml` está en la rama predeterminada y que Actions está habilitado en el repositorio.

## Fuentes

- [Ejecución manual de workflows](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).
- [Playwright en integración continua y GitHub Actions](https://playwright.dev/python/docs/ci).
- [Secretos de GitHub Actions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).
