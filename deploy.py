"""
Deploy para PythonAnywhere - Git Pull + Reload
Uso: python deploy.py
"""

import os
import requests

# Carregar .env
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    with open(env_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ[key.strip()] = val.strip()

USERNAME = os.environ.get('PYTHONANYWHERE_USERNAME', 'rouxinol')
TOKEN = os.environ.get('PYTHONANYWHERE_TOKEN', '')
DOMAIN = os.environ.get('PYTHONANYWHERE_DOMAIN', f'{USERNAME}.pythonanywhere.com')
DEPLOY_SECRET = os.environ.get('DEPLOY_SECRET', '')

if not TOKEN:
    print('ERRO: Token nao configurado. Crie o arquivo .env com PYTHONANYWHERE_TOKEN=...')
    exit(1)

PA_API = f'https://www.pythonanywhere.com/api/v0/user/{USERNAME}'
PA_HEADERS = {'Authorization': f'Token {TOKEN}'}
APP_URL = f'https://{DOMAIN}'


def run():
    print('=' * 50)
    print('  Deploy PythonAnywhere')
    print('=' * 50)

    # 1. Git pull via endpoint do app
    print('\n[1/2] Executando git pull...')
    try:
        r = requests.post(
            f'{APP_URL}/deploy-hook',
            headers={'X-Deploy-Secret': DEPLOY_SECRET},
            timeout=30
        )
        if r.status_code == 200:
            data = r.json()
            if data.get('stdout'):
                print(f'  {data["stdout"]}')
            if data.get('stderr'):
                print(f'  {data["stderr"]}')
            if data.get('ok'):
                print('  Git pull OK!')
            else:
                print(f'  AVISO: {data.get("msg", "Falha no git pull")}')
        elif r.status_code == 403:
            print('  ERRO: Secret invalido')
            return
        else:
            print(f'  ERRO: {r.status_code} - {r.text[:200]}')
            return
    except requests.exceptions.ConnectionError:
        print('  ERRO: App nao acessivel. Verifique se esta rodando.')
        return
    except Exception as e:
        print(f'  ERRO: {e}')
        return

    # 2. Reload da webapp
    print('[2/2] Recarregando webapp...')
    r = requests.post(f'{PA_API}/webapps/{DOMAIN}/reload/', headers=PA_HEADERS)
    if r.status_code == 200:
        print(f'  {DOMAIN} recarregado com sucesso!')
    else:
        print(f'  ERRO ao recarregar: {r.status_code} - {r.text[:200]}')

    print('\n' + '=' * 50)
    print('  Deploy concluido!')
    print('=' * 50)


if __name__ == '__main__':
    run()
