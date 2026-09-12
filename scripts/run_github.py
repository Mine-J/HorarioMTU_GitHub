"""Lanza el programa en un runner nuevo usando el secreto MTU_ENV."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from dotenv import dotenv_values


def mask(value):
    # Escapado del protocolo de comandos de GitHub Actions.
    value = value.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    print('::add-mask::' + value, flush=True)


def main():
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
            result = subprocess.run(command, cwd=root, stdin=subprocess.DEVNULL, check=False)
            return result.returncode
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
