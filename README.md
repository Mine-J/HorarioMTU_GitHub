## Actualización v11: diagnóstico del acceso y estado de Telegram

Sustituye el contenido del repositorio con este paquete, incluida `.github/workflows/horario.yml` y todos los archivos de `scripts/`. No hace falta repetir Configurar Telegram ni cambiar los secretos si ya están guardados.

Inicia una ejecución NUEVA en Actions → Horario MTU → Run workflow, con `enviar_telegram` marcado. No uses Re-run jobs de una ejecución antigua, que conserva la revisión anterior. Selecciona la rama donde hayas guardado estos cambios.

El registro comienza por `Lanzador GitHub + Telegram v11`. Si está activado, muestra `Telegram activado` y luego `Telegram ha aceptado el aviso inicial`. Si aparece `Telegram DESACTIVADO`, la casilla estaba desmarcada. Si falta ENVIAR_TELEGRAM, el lanzador pide actualizar la tarea.

La aceptación del mensaje por Telegram no garantiza que iOS muestre una notificación: comprueba el chat del bot y sus ajustes de notificaciones. Si no ves nada en ese chat, revisa que TELEGRAM_CHAT_ID sea el que recibiste de Configurar Telegram.

El acceso a Microsoft ahora reintenta los cambios de documento durante el autorrelleno y conserva la cuenta ya elegida. Si el navegador falla en otro paso, se envía por Telegram un diagnóstico que indica el paso y cualquier código AADSTS visible, sin incluir contraseñas ni el texto completo de la página. Si falla antes de aparecer el reto MFA, todavía no habrá un número que enviar.

Pruebas locales con simulaciones y procesos reales de prueba; pendiente comprobar el acceso de MTU en tu ejecución de GitHub.

---

# Horario MTU con Telegram y GitHub Actions

Esta versión envía a tu chat privado el número de Authenticator durante el inicio de sesión y el horario semanal con horario.ics al terminar. Tu PC puede estar apagado. La aprobación sigue realizándose en Microsoft Authenticator. El correo continúa disponible como opción adicional.

## Actualizar el repositorio

Descomprime este paquete y sustituye los archivos de tu repositorio con el contenido, conservando las rutas. Incluye **las dos tareas** `.github/workflows/horario.yml` y `.github/workflows/telegram.yml`, así como los tres archivos de `scripts/`. Guarda los cambios en la rama predeterminada, normalmente `main`.

No subas solamente el ZIP. Si el cargador web omite `.github`, utiliza **Add file → Create new file** y escribe la ruta exacta de cada YAML antes de pegar su contenido. Si ya existe, edítalo y reemplaza su contenido.

## Conectar tu bot, una vez

1. En **Settings → Secrets and variables → Actions → New repository secret**, crea **TELEGRAM_BOT_TOKEN** y pega únicamente el token recibido de BotFather, sin comillas. Conserva tu secreto **MTU_ENV** con el contenido del `.env`.
2. Abre **tu propio bot** en Telegram, pulsa **Iniciar** y envíale `/vincular ` seguido de un código que elijas de 8 a 64 letras, números, guiones o guiones bajos. Por ejemplo: `/vincular horario-7318-km`. Hazlo en el chat privado con tu bot, no en BotFather. El código sirve para identificar tu mensaje; no es el token de BotFather.
3. En GitHub, abre **Actions → Configurar Telegram → Run workflow**. En **codigo**, escribe solo el código que elegiste (en el ejemplo, `horario-7318-km`) y ejecuta. No necesitas abrir los registros.
4. El bot te enviará un mensaje con tu **TELEGRAM_CHAT_ID**. También aparecerá en el resumen de la ejecución terminada. Crea otro **Repository secret** llamado **TELEGRAM_CHAT_ID** y pega solo ese número, sin comillas.

Deben quedar tres secretos: `MTU_ENV`, `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`. El programa del horario no se inicia durante la configuración de Telegram.

La tarea busca un único chat privado que haya enviado ese código durante las últimas 24 horas. No elige simplemente al primer usuario del bot. Si no lo encuentra, envía un mensaje nuevo con otro código y vuelve a ejecutar la configuración. Usa un bot recién creado para este proyecto: un bot con un webhook o un proceso que ya consuma sus mensajes puede impedir esta búsqueda; el programa no modifica esas conexiones.

## Cada vez que quieras el horario

1. Desde Safari abre **Actions → Horario MTU → Run workflow**.
2. Deja marcada **enviar_telegram** y deja desmarcada **enviar_correo** si no has configurado SMTP.
3. Espera el mensaje del bot que anuncia el inicio de sesión. Puede tardar mientras GitHub prepara Python y Chromium.
4. Si Microsoft pide aprobación, llegará otro mensaje con el **número para Authenticator**. Cambia a Microsoft Authenticator e introduce el número. Tienes hasta tres minutos de espera en el programa; el reto puede caducar antes. Las notificaciones pueden retrasarse según Telegram, tu conexión y los ajustes del iPhone.
5. Al terminar recibirás el resumen semanal y el calendario `horario.ics`. Los mensajes largos se dividen en varias partes. Si ya había acceso y Microsoft no pide MFA, no se envía un número.

El bot debe tener las notificaciones activadas para que puedas ver el aviso sin abrir Telegram. No responde a `/horario`: la ejecución se inicia con **Run workflow** en GitHub.

Si falla Telegram antes del acceso, la tarea se detiene antes de iniciar sesión en MTU. Si falla una notificación después, conserva el número en los registros y los archivos del horario como artefactos; la tarea marca un error. Los envíos no se reintentan automáticamente para evitar duplicados.

## Comprobaciones realizadas

Se comprueban localmente la selección del chat, el envío simulado de mensajes y calendario, y la lectura del número mientras el proceso sigue ejecutándose. No se ha enviado ningún mensaje real ni se ha probado el acceso de tu cuenta desde GitHub. El bloqueo por políticas de MTU sigue siendo posible y esta integración no lo evita.

[Documentación de Telegram Bot API](https://core.telegram.org/bots/api).

---

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
