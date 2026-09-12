"""Telegram privado: texto y archivos, sin imprimir tokens ni respuestas privadas."""
import json
import re
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token, chat_id=None):
        token = token.strip()
        if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
            raise TelegramError('Falta TELEGRAM_BOT_TOKEN o su formato no es válido.')
        self._base = 'https://api.telegram.org/bot' + token + '/'
        self.chat_id = str(chat_id or '').strip()
        if chat_id is not None and not re.fullmatch(r'[1-9][0-9]*', self.chat_id):
            raise TelegramError('TELEGRAM_CHAT_ID debe ser el número positivo de tu chat privado.')

    def request(self, method, data=None, content_type='application/json'):
        body = json.dumps(data or {}).encode() if content_type == 'application/json' else data
        request = Request(self._base + method, data=body, headers={'Content-Type': content_type})
        try:
            with urlopen(request, timeout=10) as response:
                result = json.load(response)
        except HTTPError as exc:
            raise TelegramError(f'Telegram rechazó la petición (HTTP {exc.code}). Comprueba token, chat y que hayas pulsado Iniciar en tu bot.') from None
        except (URLError, OSError, ValueError):
            raise TelegramError('No se pudo confirmar la respuesta de Telegram; no se reintenta para evitar duplicados.') from None
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise TelegramError('Telegram no confirmó la operación.')
        return result.get('result')

    def verify_private_chat(self):
        chat = self.request('getChat', {'chat_id': self.chat_id})
        if not isinstance(chat, dict) or chat.get('type') != 'private' or str(chat.get('id')) != self.chat_id:
            raise TelegramError('El destino debe ser tu chat privado con el bot.')

    def send(self, text):
        # 1800 caracteres caben incluso si todos ocupan dos unidades UTF-16.
        for start in range(0, len(text), 1800):
            self.request('sendMessage', {'chat_id': self.chat_id, 'text': text[start:start+1800],
                                         'disable_notification': False})

    def document(self, path):
        boundary = 'MTU' + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n'
                f'{self.chat_id}\r\n--{boundary}\r\n'
                'Content-Disposition: form-data; name="document"; filename="horario.ics"\r\n'
                'Content-Type: text/calendar\r\n\r\n').encode()
        body += path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
        self.request('sendDocument', body, 'multipart/form-data; boundary=' + boundary)
