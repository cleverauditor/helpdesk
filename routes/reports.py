import csv
import io
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, Response
from flask_login import login_required, current_user
from sqlalchemy import func
from models import db, Ticket, User, Category, TicketHistory

reports_bp = Blueprint('reports', __name__, url_prefix='/relatorios')


def aplicar_filtro_permissao(query):
    """Aplica filtro de permissão baseado no tipo de usuário"""
    if current_user.is_admin():
        # Admin vê tudo
        return query
    elif current_user.tipo == 'atendente':
        # Atendente vê apenas chamados das suas categorias
        categorias_ids = current_user.get_categorias_ids()
        if categorias_ids:
            query = query.filter(
                db.or_(
                    Ticket.categoria_id.in_(categorias_ids),
                    Ticket.categoria_id.is_(None)
                )
            )
        return query
    else:
        # Cliente vê apenas seus próprios chamados
        return query.filter(Ticket.cliente_id == current_user.id)


@reports_bp.route('/')
@login_required
def index():
    # Filtros disponíveis baseados no tipo de usuário
    if current_user.is_admin():
        atendentes = User.query.filter(User.tipo.in_(['admin', 'atendente'])).all()
        categorias = Category.query.filter_by(ativo=True).all()
        empresas = db.session.query(User.empresa).filter(
            User.tipo == 'cliente_externo',
            User.empresa.isnot(None),
            User.empresa != ''
        ).distinct().order_by(User.empresa).all()
        empresas = [e[0] for e in empresas]
    elif current_user.tipo == 'atendente':
        atendentes = [current_user]  # Só ele mesmo
        # Apenas suas categorias
        categorias_ids = current_user.get_categorias_ids()
        if categorias_ids:
            categorias = Category.query.filter(Category.id.in_(categorias_ids), Category.ativo == True).all()
        else:
            categorias = Category.query.filter_by(ativo=True).all()
        empresas = []  # Atendente não filtra por empresa
    else:
        # Cliente não tem filtros avançados
        atendentes = []
        categorias = []
        empresas = []

    return render_template('reports/index.html',
                          atendentes=atendentes,
                          categorias=categorias,
                          empresas=empresas)


@reports_bp.route('/gerar', methods=['POST'])
@login_required
def gerar():
    # Filtros do formulário
    data_inicio = request.form.get('data_inicio')
    data_fim = request.form.get('data_fim')
    status = request.form.get('status')
    prioridade = request.form.get('prioridade')
    atendente_id = request.form.get('atendente_id', type=int)
    categoria_id = request.form.get('categoria_id', type=int)
    empresa = request.form.get('empresa', '').strip()

    query = Ticket.query

    # Aplicar filtro de permissão primeiro
    query = aplicar_filtro_permissao(query)

    # Filtros adicionais do formulário
    if data_inicio:
        query = query.filter(Ticket.criado_em >= datetime.strptime(data_inicio, '%Y-%m-%d'))
    if data_fim:
        query = query.filter(Ticket.criado_em <= datetime.strptime(data_fim, '%Y-%m-%d') + timedelta(days=1))
    if status:
        query = query.filter(Ticket.status == status)
    if prioridade:
        query = query.filter(Ticket.prioridade == prioridade)
    if atendente_id and current_user.is_admin():
        query = query.filter(Ticket.atendente_id == atendente_id)
    if categoria_id:
        # Verificar se atendente pode ver esta categoria
        if current_user.tipo == 'atendente':
            if current_user.pode_ver_categoria(categoria_id):
                query = query.filter(Ticket.categoria_id == categoria_id)
        else:
            query = query.filter(Ticket.categoria_id == categoria_id)
    if empresa and current_user.is_admin():
        clientes_empresa = User.query.filter(User.empresa == empresa).all()
        cliente_ids = [c.id for c in clientes_empresa]
        query = query.filter(Ticket.cliente_id.in_(cliente_ids))

    tickets = query.order_by(Ticket.criado_em.desc()).all()

    # Calcular métricas
    metricas = calcular_metricas(tickets)

    # Filtros disponíveis baseados no tipo de usuário
    if current_user.is_admin():
        atendentes = User.query.filter(User.tipo.in_(['admin', 'atendente'])).all()
        categorias = Category.query.filter_by(ativo=True).all()
        empresas = db.session.query(User.empresa).filter(
            User.tipo == 'cliente_externo',
            User.empresa.isnot(None),
            User.empresa != ''
        ).distinct().order_by(User.empresa).all()
        empresas = [e[0] for e in empresas]
    elif current_user.tipo == 'atendente':
        atendentes = [current_user]
        categorias_ids = current_user.get_categorias_ids()
        if categorias_ids:
            categorias = Category.query.filter(Category.id.in_(categorias_ids), Category.ativo == True).all()
        else:
            categorias = Category.query.filter_by(ativo=True).all()
        empresas = []
    else:
        atendentes = []
        categorias = []
        empresas = []

    return render_template('reports/index.html',
                          tickets=tickets,
                          metricas=metricas,
                          atendentes=atendentes,
                          categorias=categorias,
                          empresas=empresas,
                          filtros={
                              'data_inicio': data_inicio,
                              'data_fim': data_fim,
                              'status': status,
                              'prioridade': prioridade,
                              'atendente_id': atendente_id,
                              'categoria_id': categoria_id,
                              'empresa': empresa
                          })


def calcular_metricas(tickets):
    if not tickets:
        return {
            'total': 0,
            'tempo_medio_resposta': 0,
            'tempo_medio_resolucao': 0,
            'taxa_sla': 0,
            'por_status': {},
            'por_prioridade': {}
        }

    total = len(tickets)

    # Tempo médio de resposta (em horas)
    tempos_resposta = []
    for t in tickets:
        if t.primeira_resposta_em and t.criado_em:
            delta = (t.primeira_resposta_em - t.criado_em).total_seconds() / 3600
            tempos_resposta.append(delta)

    tempo_medio_resposta = sum(tempos_resposta) / len(tempos_resposta) if tempos_resposta else 0

    # Tempo médio de resolução (em horas)
    tempos_resolucao = []
    for t in tickets:
        if t.fechado_em and t.criado_em:
            delta = (t.fechado_em - t.criado_em).total_seconds() / 3600
            tempos_resolucao.append(delta)

    tempo_medio_resolucao = sum(tempos_resolucao) / len(tempos_resolucao) if tempos_resolucao else 0

    # Taxa de cumprimento de SLA
    tickets_com_resolucao = [t for t in tickets if t.fechado_em and t.sla_resolucao_limite]
    dentro_sla = sum(1 for t in tickets_com_resolucao if t.fechado_em <= t.sla_resolucao_limite)
    taxa_sla = (dentro_sla / len(tickets_com_resolucao) * 100) if tickets_com_resolucao else 0

    # Por status
    por_status = {}
    for t in tickets:
        por_status[t.status] = por_status.get(t.status, 0) + 1

    # Por prioridade
    por_prioridade = {}
    for t in tickets:
        por_prioridade[t.prioridade] = por_prioridade.get(t.prioridade, 0) + 1

    return {
        'total': total,
        'tempo_medio_resposta': round(tempo_medio_resposta, 2),
        'tempo_medio_resolucao': round(tempo_medio_resolucao, 2),
        'taxa_sla': round(taxa_sla, 1),
        'por_status': por_status,
        'por_prioridade': por_prioridade
    }


@reports_bp.route('/exportar/csv', methods=['POST'])
@login_required
def exportar_csv():
    # Filtros
    data_inicio = request.form.get('data_inicio')
    data_fim = request.form.get('data_fim')
    status = request.form.get('status')
    prioridade = request.form.get('prioridade')
    atendente_id = request.form.get('atendente_id', type=int)
    categoria_id = request.form.get('categoria_id', type=int)
    empresa = request.form.get('empresa', '').strip()

    query = Ticket.query

    # Aplicar filtro de permissão primeiro
    query = aplicar_filtro_permissao(query)

    if data_inicio:
        query = query.filter(Ticket.criado_em >= datetime.strptime(data_inicio, '%Y-%m-%d'))
    if data_fim:
        query = query.filter(Ticket.criado_em <= datetime.strptime(data_fim, '%Y-%m-%d') + timedelta(days=1))
    if status:
        query = query.filter(Ticket.status == status)
    if prioridade:
        query = query.filter(Ticket.prioridade == prioridade)
    if atendente_id and current_user.is_admin():
        query = query.filter(Ticket.atendente_id == atendente_id)
    if categoria_id:
        if current_user.tipo == 'atendente':
            if current_user.pode_ver_categoria(categoria_id):
                query = query.filter(Ticket.categoria_id == categoria_id)
        else:
            query = query.filter(Ticket.categoria_id == categoria_id)
    if empresa and current_user.is_admin():
        clientes_empresa = User.query.filter(User.empresa == empresa).all()
        cliente_ids = [c.id for c in clientes_empresa]
        query = query.filter(Ticket.cliente_id.in_(cliente_ids))

    tickets = query.order_by(Ticket.criado_em.desc()).all()

    # Criar CSV
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        'ID', 'Título', 'Status', 'Prioridade', 'Categoria',
        'Cliente', 'Empresa', 'Atendente', 'Criado em', 'Fechado em',
        'SLA Limite', 'SLA Status', 'Tempo Atendimento (min)'
    ])

    # Dados
    for ticket in tickets:
        writer.writerow([
            ticket.id,
            ticket.titulo,
            ticket.status,
            ticket.prioridade,
            ticket.categoria.nome if ticket.categoria else '',
            ticket.cliente.nome,
            ticket.cliente.empresa if ticket.cliente.empresa else '',
            ticket.atendente.nome if ticket.atendente else '',
            ticket.criado_em.strftime('%d/%m/%Y %H:%M') if ticket.criado_em else '',
            ticket.fechado_em.strftime('%d/%m/%Y %H:%M') if ticket.fechado_em else '',
            ticket.sla_resolucao_limite.strftime('%d/%m/%Y %H:%M') if ticket.sla_resolucao_limite else '',
            ticket.sla_resolucao_status(),
            ticket.tempo_total_atendimento()
        ])

    output.seek(0)

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment;filename=relatorio_chamados_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'}
    )
