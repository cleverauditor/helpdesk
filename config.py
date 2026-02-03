import os
from datetime import timedelta

class Config:
    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'

    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(os.path.dirname(os.path.abspath(__file__)), 'helpdesk.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Reconexão automática para MySQL (PythonAnywhere)
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_recycle': 280,  # Recicla conexões antes do timeout do MySQL (300s)
        'pool_pre_ping': True  # Verifica conexão antes de usar
    }

    # Upload
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB max file size
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt', 'zip'}

    # Extensões permitidas para arquivos KML (módulo de auditoria)
    ALLOWED_KML_EXTENSIONS = {'kml', 'kmz'}

    # Email (configure with your SMTP server)
    MAIL_SERVER = os.environ.get('MAIL_SERVER') or 'smtp.gmail.com'
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587)
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER') or 'helpdesk@empresa.com'

    # SLA Defaults (em horas úteis)
    SLA_DEFAULTS = {
        'critica': {'resposta': 1, 'resolucao': 4},
        'alta': {'resposta': 2, 'resolucao': 8},
        'media': {'resposta': 4, 'resolucao': 24},
        'baixa': {'resposta': 8, 'resolucao': 48}
    }

    # Horário Administrativo (para cálculo de SLA)
    HORARIO_INICIO = '08:00'
    HORARIO_FIM = '17:00'

    # Session
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
