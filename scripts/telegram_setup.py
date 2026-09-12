"""Encuentra solo el chat privado que envió el código de vinculación elegido."""
import os
from pathlib import Path
import re
import sys
import time

from telegram_notify import Telegram, TelegramError


def find_chat(updates, code, now):
    ids = set()
    for update in updates:
        message = update.get('message', {})
        chat = message.get('chat', {})
        sender = message.get('from', {})
        if (message.get('text', '').strip() == '/vincular ' + code
                and chat.get('type') == 'private' and sender.get('id') == chat.get('id')
                and not sender.get('is_bot', False)
                and 0 <= now - message.get('date', 0) < 86400
                and isinstance(chat.get('id'), int) and chat['id'] > 0):
            ids.add(chat['id'])
    if len(ids) != 1:
        raise TelegramError('No se encuentra un único chat privado con ese código reciente. Envía /vincular seguido del código a tu propio bot y ejecuta otra vez Configurar Telegram. Usa un código nuevo si hay varias coincidencias.')
    return str(ids.pop())


def main():
    code = os.environ.get('CODIGO_VINCULACION', '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', code):
        raise TelegramError('El código debe tener entre 8 y 64 letras, números, guiones o guiones bajos.')
    bot = Telegram(os.environ.get('TELEGRAM_BOT_TOKEN', ''))
    updates = bot.request('getUpdates', {'timeout': 0, 'limit': 100, 'allowed_updates': ['message']})
    bot.chat_id = find_chat(updates, code, time.time())
    bot.verify_private_chat()
    bot.send('Chat encontrado para Horario MTU.\n\nGuarda en GitHub un secreto llamado TELEGRAM_CHAT_ID con este valor:\n' + bot.chat_id)
    output = '## Telegram preparado\n\nCrea el secreto **TELEGRAM_CHAT_ID** con este valor:\n\n`' + bot.chat_id + '`\n'
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(output, encoding='utf-8')
    print('Identificador enviado a tu chat y al resumen de esta ejecución.')


if __name__ == '__main__':
    try:
        main()
    except TelegramError as exc:
        print(str(exc), file=sys.stderr)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            Path(os.environ['GITHUB_STEP_SUMMARY']).write_text('## Configuración pendiente\n\n' + str(exc), encoding='utf-8')
        sys.exit(1)
