import os
from dotenv import load_dotenv

# Carrega variáveis do arquivo .env (caminho explícito para funcionar no WSGI)
basedir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(basedir, '.env'))

from flask import Flask, redirect, url_for, render_template
from flask_login import LoginManager, login_required, current_user
from config import Config
from models import db, User, Category, SLAConfig, IndicadorCategoria, Indicador

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
    from routes.auditoria import auditoria_bp
    from routes.clientes import clientes_bp
    from routes.indicadores import indicadores_bp
    from routes.roteirizador import roteirizador_bp
    from routes.veiculos import veiculos_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(auditoria_bp)
    app.register_blueprint(clientes_bp)
    app.register_blueprint(indicadores_bp)
    app.register_blueprint(roteirizador_bp)
    app.register_blueprint(veiculos_bp)

    # Rota raiz - Página de módulos
    @app.route('/')
    @login_required
    def index():
        modulos = []

        # Atendimento - todos os usuários
        modulos.append({
            'nome': 'Atendimento',
            'descricao': 'Central de chamados, tickets e suporte ao cliente.',
            'icone': 'bi-headset',
            'cor': '#00a8e8',
            'url': url_for('dashboard.index')
        })

        # Relatórios - todos os usuários
        modulos.append({
            'nome': 'Relatórios',
            'descricao': 'Relatórios gerenciais e exportação de dados.',
            'icone': 'bi-file-earmark-bar-graph',
            'cor': '#198754',
            'url': url_for('reports.index')
        })

        # Auditoria de Rotas - admin + categoria Auditoria
        tem_auditoria = current_user.is_admin() or \
            current_user.categorias.filter_by(nome='Auditoria').first()

        # Combustível - admin + categoria Análise de Combustível
        tem_combustivel = current_user.is_admin() or \
            current_user.categorias.filter_by(nome='Análise de Combustível').first()

        if tem_auditoria:
            modulos.append({
                'nome': 'Auditoria de Rotas',
                'descricao': 'Auditoria de rotas planejadas vs. executadas com análise KML.',
                'icone': 'bi-signpost-2',
                'cor': '#6f42c1',
                'url': url_for('auditoria.lista_rotas')
            })

        if tem_combustivel:
            modulos.append({
                'nome': 'Combustível',
                'descricao': 'Análise de consumo de combustível e detecção de anomalias.',
                'icone': 'bi-fuel-pump',
                'cor': '#fd7e14',
                'url': url_for('auditoria.combustivel')
            })

        # Clientes - admin + auditoria ou combustível
        if current_user.is_admin() or tem_auditoria or tem_combustivel:
            modulos.append({
                'nome': 'Clientes',
                'descricao': 'Cadastro e gerenciamento de empresas clientes.',
                'icone': 'bi-building',
                'cor': '#0d6efd',
                'url': url_for('clientes.lista')
            })

        # Indicadores Diretoria
        tem_indicadores = current_user.is_admin() or \
            current_user.categorias.filter_by(nome='Indicadores Diretoria').first()

        if tem_indicadores:
            modulos.append({
                'nome': 'Indicadores Diretoria',
                'descricao': 'Indicadores gerenciais e acompanhamento mensal pela diretoria.',
                'icone': 'bi-graph-up-arrow',
                'cor': '#dc3545',
                'url': url_for('indicadores.painel')
            })

        # Roteirizador Inteligente
        tem_roteirizador = current_user.is_admin() or \
            current_user.categorias.filter_by(nome='Roteirizador').first()

        if tem_roteirizador:
            modulos.append({
                'nome': 'Roteirizador Inteligente',
                'descricao': 'Planejamento inteligente de rotas de fretamento com otimização de paradas.',
                'icone': 'bi-map',
                'cor': '#20c997',
                'url': url_for('roteirizador.lista')
            })

        # Administração - admin e gestor
        if current_user.is_admin() or current_user.is_gestor():
            modulos.append({
                'nome': 'Gestão de Usuários',
                'descricao': 'Gerenciamento de usuários' + (', categorias e configurações do sistema.' if current_user.is_admin() else ' das suas categorias.'),
                'icone': 'bi-people',
                'cor': '#58595b',
                'url': url_for('users.lista')
            })

        return render_template('modulos.html', modulos=modulos)

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
        ('Outros', 'Outros assuntos'),
        ('Auditoria', 'Acesso ao módulo de auditoria de rotas'),
        ('Análise de Combustível', 'Acesso ao módulo de análise de combustível'),
        ('Indicadores Diretoria', 'Acesso ao módulo de indicadores gerenciais'),
        ('Roteirizador', 'Acesso ao módulo de roteirização inteligente')
    ]
    for nome, descricao in categorias_padrao:
        if not Category.query.filter_by(nome=nome).first():
            cat = Category(nome=nome, descricao=descricao)
            db.session.add(cat)

    # Criar indicadores padrão
    if not IndicadorCategoria.query.first():
        indicadores_padrao = [
            ('CONSUMO', [
                ('Pneus', 'Km 1ª Vida e Reformas das medidas 275 e 215, 295, Custo Por Km para as unidades (BH, Anglo e Lafaiete)', 'Victor Maffia', 'Christiane Henriques'),
                ('Combustível', 'Média Geral de Consumo - ROUXINOL e SUDOESTINO', 'Victor Maffia', ''),
            ]),
            ('MECÂNICA', [
                ('Socorros e Atrasos por Companhia', 'Quantidade de socorros e de atrasos ocorridos por companhia (responsável: Rouxinol ou não) - Estratificar motivos', 'Leandro Oliveira', ''),
            ]),
            ('MANUTENÇÃO VISUAL', [
                ('Reparos identificados', 'Quantidade de reparos total X Quantidade de reparos com identificação do responsável', 'Oziel Carvalho', ''),
                ('Reforma geral', 'Quantidade de veículos reformados no mês', 'Oziel Carvalho', ''),
            ]),
            ('EFICIÊNCIA NA ESCALA', [
                ('Redução de KM Improdutivo', 'Kms improdutivos reduzidos no mês', 'Renata / Wesley', ''),
                ('Implantação de Duplas', 'Implantação de duplas de motoristas', 'Renata / Wesley', ''),
                ('Jornada de descanso 11 horas', 'Quantidade de colaboradores dentro do padrão de 11 horas de descanso', 'Renata / Wesley', ''),
                ('Horas Extras', 'Redução das horas extras realizadas', 'Renata / Wesley', ''),
            ]),
            ('RECURSOS HUMANOS', [
                ('Absenteísmo', 'Quantidade por setor', 'Jessica Custódio', 'Liwshanna Oliveira'),
                ('Turnover', 'Número real de colaboradores do setor/contratados/demitidos', 'Jessica Custódio', ''),
            ]),
            ('DEPTO PESSOAL', [
                ('Atestados', 'Número total / número por setor', 'Jorgeane Reis', ''),
            ]),
            ('COMPRAS E ESTOQUE', [
                ('Eficiência na compra', 'Evolução dos custos de aquisição, preço médio, curva ABC, quantidades, etc', 'Adeilson Martins', 'Simone Thais'),
                ('Compras com cercas limites e através de autorizações', 'Analisar compras fora do limite (alçada)', 'Adeilson Martins', ''),
            ]),
        ]
        for ordem_cat, (cat_nome, indicadores) in enumerate(indicadores_padrao):
            cat = IndicadorCategoria(nome=cat_nome, ordem=ordem_cat)
            db.session.add(cat)
            db.session.flush()
            for ordem_ind, (ind_nome, ind_desc, resp_ger, resp_conf) in enumerate(indicadores):
                ind = Indicador(
                    categoria_id=cat.id,
                    nome=ind_nome,
                    descricao=ind_desc,
                    responsavel_geracao=resp_ger,
                    responsavel_conferencia=resp_conf,
                    ordem=ordem_ind
                )
                db.session.add(ind)

    db.session.commit()


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)
