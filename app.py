import os
from dotenv import load_dotenv

# Carrega variáveis do arquivo .env (caminho explícito para funcionar no WSGI)
basedir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(basedir, '.env'))

from flask import Flask, redirect, url_for
from flask_login import LoginManager
from config import Config
from models import db, User, Category, SLAConfig

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Criar pasta de uploads
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # Inicializar extensões
    db.init_app(app)

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Por favor, faça login para acessar esta página.'
    login_manager.login_message_category = 'warning'

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Registrar blueprints
    from routes.auth import auth_bp
    from routes.tickets import tickets_bp
    from routes.users import users_bp
    from routes.dashboard import dashboard_bp
    from routes.reports import reports_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(reports_bp)

    # Rota raiz
    @app.route('/')
    def index():
        return redirect(url_for('dashboard.index'))

    # Context processor para templates
    @app.context_processor
    def utility_processor():
        from datetime import datetime
        return {
            'now': datetime.utcnow()
        }

    # Criar tabelas e dados iniciais
    with app.app_context():
        db.create_all()
        init_data()

    return app


def init_data():
    """Cria dados iniciais se não existirem"""
    # Criar admin se não existir
    if not User.query.filter_by(email='admin@helpdesk.com').first():
        admin = User(
            nome='Administrador',
            email='admin@helpdesk.com',
            tipo='admin'
        )
        admin.set_senha('admin123')
        db.session.add(admin)
        print('Usuário admin criado: admin@helpdesk.com / admin123')

    # Criar configurações de SLA
    sla_defaults = [
        ('critica', 1, 4),
        ('alta', 2, 8),
        ('media', 4, 24),
        ('baixa', 8, 48)
    ]
    for prioridade, resposta, resolucao in sla_defaults:
        if not SLAConfig.query.filter_by(prioridade=prioridade).first():
            sla = SLAConfig(
                prioridade=prioridade,
                tempo_resposta_horas=resposta,
                tempo_resolucao_horas=resolucao
            )
            db.session.add(sla)

    # Criar categorias padrão
    categorias_padrao = [
        ('Solicitação Geral', 'Solicitações gerais e dúvidas'),
        ('Atendimento', 'Atendimento ao cliente'),
        ('Serviços', 'Solicitações de serviços'),
        ('Informações', 'Solicitações de informações'),
        ('Sugestões', 'Sugestões e melhorias'),
        ('Outros', 'Outros assuntos')
    ]
    for nome, descricao in categorias_padrao:
        if not Category.query.filter_by(nome=nome).first():
            cat = Category(nome=nome, descricao=descricao)
            db.session.add(cat)

    db.session.commit()


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)
