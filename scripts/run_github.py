"""Lanza el programa en un runner nuevo usando el secreto MTU_ENV."""
import io
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile

from dotenv import dotenv_values
from telegram_notify import Telegram, TelegramError


def stream_process(command, root, bot):
    notified = set()
    failed_notice = False
    with subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding='utf-8', errors='replace', bufsize=1) as process:
        for line in process.stdout:
            print(line, end='', flush=True)
            if bot and line.startswith('DIAGNÓSTICO DE ACCESO: '):
                try:
                    bot.send('Horario MTU: problema durante el inicio de sesión.\n\n' + line.strip())
                except TelegramError as exc:
                    print(str(exc), flush=True)
                    failed_notice = True
            number = re.fullmatch(r'NÚMERO PARA MICROSOFT AUTHENTICATOR: ([0-9]{1,3})\s*', line)
            if bot and number and number[1] not in notified:
                notified.add(number[1])
                try:
                    bot.send('Número para Microsoft Authenticator: ' + number[1]
                             + '\n\nAbre Authenticator e introduce este número para aprobar la ejecución de Horario MTU que acabas de iniciar. El reto caduca pronto.')
                except TelegramError as exc:
                    print(str(exc), flush=True)
                    failed_notice = True
        return process.wait(), failed_notice


def mask(value):
    # Escapado del protocolo de comandos de GitHub Actions.
    value = value.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    print('::add-mask::' + value, flush=True)


def main():
    print('Lanzador GitHub + Telegram v11', flush=True)
    contents = os.environ.pop('MTU_ENV', '')
    if not contents.strip():
        print('Falta el secreto MTU_ENV. Consulta README.md.', flush=True)
        return 1
    values = dotenv_values(stream=io.StringIO(contents), interpolate=False)
    for value in values.values():
        if value:
            mask(value)
    if not all(values.get(key) for key in ('MTU_USERNAME', 'MTU_PASSWORD')):
        print('MTU_ENV debe contener MTU_USERNAME y MTU_PASSWORD.', flush=True)
        return 1
    root = Path(__file__).resolve().parents[1]
    out = root / 'salida'
    bot = None
    telegram_flag = os.environ.get('ENVIAR_TELEGRAM', '').strip().lower()
    if telegram_flag not in ('true', 'false'):
        print('Falta ENVIAR_TELEGRAM en la tarea. Actualiza .github/workflows/horario.yml con el paquete v11.', flush=True)
        return 1
    if telegram_flag == 'true':
        print('Telegram activado: comprobando el bot y el chat...', flush=True)
        try:
            bot = Telegram(os.environ.pop('TELEGRAM_BOT_TOKEN', ''), os.environ.pop('TELEGRAM_CHAT_ID', ''))
            bot.verify_private_chat()
            bot.send('Horario MTU: voy a iniciar sesión. Si Microsoft pide aprobación, te enviaré aquí el número de Authenticator.')
            print('Telegram ha aceptado el aviso inicial. Revisa el chat del bot vinculado.', flush=True)
        except TelegramError as exc:
            print(str(exc), flush=True)
            return 1
    else:
        print('Telegram DESACTIVADO: marca enviar_telegram al pulsar Run workflow para recibir los avisos.', flush=True)
    # El proceso del navegador no necesita las credenciales de Telegram.
    os.environ.pop('TELEGRAM_BOT_TOKEN', None)
    os.environ.pop('TELEGRAM_CHAT_ID', None)
    with tempfile.TemporaryDirectory(prefix='horario-mtu-', dir=os.environ.get('RUNNER_TEMP')) as tmp:
        private = Path(tmp)
        env_file = private / '.env'
        env_file.write_text(contents, encoding='utf-8')
        env_file.chmod(0o600)
        command = ['xvfb-run', '-a', sys.executable, '-u', str(root / 'horario_mtu.py'),
                   'preview', '--interactive', '--env-file', str(env_file),
                   '--state-dir', str(private / 'state'), '--out', str(out)]
        if os.environ.get('ENVIAR_CORREO', 'false').lower() == 'true':
            command.append('--email')
        print('Abre este registro y espera a NÚMERO PARA MICROSOFT AUTHENTICATOR.', flush=True)
        print('Cuando aparezca, cambia a Authenticator y aprueba. El programa espera hasta 3 minutos.', flush=True)
        try:
            returncode, failed_notice = stream_process(command, root, bot)
            if bot:
                try:
                    if (out / 'horario.txt').exists():
                        bot.send((out / 'horario.txt').read_text(encoding='utf-8'))
                        bot.document(out / 'horario.ics')
                    if returncode:
                        bot.send('La ejecución terminó con un error. Si ya recibiste el horario, el error ocurrió después de exportarlo. Consulta el resumen o registro de GitHub para ver el detalle.')
                except TelegramError as exc:
                    print(str(exc), flush=True)
                    failed_notice = True
            return returncode or (1 if failed_notice else 0)
        finally:
            summary = out / 'horario.txt'
            destination = os.environ.get('GITHUB_STEP_SUMMARY')
            if destination and summary.exists():
                # Markdown de cita: conserva los saltos sin interpretar HTML del horario.
                import html
                lines = summary.read_text(encoding='utf-8').splitlines()
                with open(destination, 'a', encoding='utf-8') as handle:
                    handle.write('## Horario semanal\n\n')
                    handle.write('\n'.join('> ' + html.escape(line) + '  ' for line in lines))
                    handle.write('\n\nEl archivo de calendario está en el artefacto horario-semanal.\n')


if __name__ == '__main__':
    sys.exit(main())
