# Arquivo WSGI para PythonAnywhere
import sys
import os

# Adicionar o diretório do projeto ao path
project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Importar a aplicação
from app import create_app

application = create_app()
