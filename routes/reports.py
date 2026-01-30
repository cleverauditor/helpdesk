import csv
import io
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, Response
from flask_login import login_required, current_user
from sqlalchemy import func
from models import db, Ticket, User, Category, TicketHistory

reports_bp = Blueprint('reports', __name__, url_prefix='/relatorios')


def atendente_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.is_cliente():
            from flask import flash, redirect, url_for
            flash('Acesso restrito.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function


@reports_bp.route('/')
@login_required
@atendente_required
def index():
    atendentes = User.query.filter(User.tipo.in_(['admin', 'atendente'])).all()
    categorias = Category.query.filter_by(ativo=True).all()
    # Lista de empresas únicas dos clientes externos
    empresas = db.session.query(User.empresa).filter(
        User.tipo == 'cliente_externo',
        User.empresa.isnot(None),
        User.empresa != ''
    ).distinct().order_by(User.empresa).all()
    empresas = [e[0] for e in empresas]

    return render_template('reports/index.html',
                          atendentes=atendentes,
                          categorias=categorias,
                          empresas=empresas)


@reports_bp.route('/gerar', methods=['POST'])
@login_required
@atendente_required
def gerar():
    # Filtros
    data_inicio = request.form.get('data_inicio')
    data_fim = request.form.get('data_fim')
    status = request.form.get('status')
    prioridade = request.form.get('prioridade')
    atendente_id = request.form.get('atendente_id', type=int)
    categoria_id = request.form.get('categoria_id', type=int)
    empresa = request.form.get('empresa', '').strip()

    query = Ticket.query

    if data_inicio:
        query = query.filter(Ticket.criado_em >= datetime.strptime(data_inicio, '%Y-%m-%d'))
    if data_fim:
        query = query.filter(Ticket.criado_em <= datetime.strptime(data_fim, '%Y-%m-%d') + timedelta(days=1))
    if status:
        query = query.filter(Ticket.status == status)
    if prioridade:
        query = query.filter(Ticket.prioridade == prioridade)
    if atendente_id:
        query = query.filter(Ticket.atendente_id == atendente_id)
    if categoria_id:
        query = query.filter(Ticket.categoria_id == categoria_id)
    if empresa:
        # Filtrar por empresa do cliente
        clientes_empresa = User.query.filter(User.empresa == empresa).all()
        cliente_ids = [c.id for c in clientes_empresa]
        query = query.filter(Ticket.cliente_id.in_(cliente_ids))

    tickets = query.order_by(Ticket.criado_em.desc()).all()

    # Calcular métricas
    metricas = calcular_metricas(tickets)

    atendentes = User.query.filter(User.tipo.in_(['admin', 'atendente'])).all()
    categorias = Category.query.filter_by(ativo=True).all()
    # Lista de empresas
    empresas = db.session.query(User.empresa).filter(
        User.tipo == 'cliente_externo',
        User.empresa.isnot(None),
        User.empresa != ''
    ).distinct().order_by(User.empresa).all()
    empresas = [e[0] for e in empresas]

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
        if t.resolvido_em and t.criado_em:
            delta = (t.resolvido_em - t.criado_em).total_seconds() / 3600
            tempos_resolucao.append(delta)

    tempo_medio_resolucao = sum(tempos_resolucao) / len(tempos_resolucao) if tempos_resolucao else 0

    # Taxa de cumprimento de SLA
    tickets_com_resolucao = [t for t in tickets if t.resolvido_em and t.sla_resolucao_limite]
    dentro_sla = sum(1 for t in tickets_com_resolucao if t.resolvido_em <= t.sla_resolucao_limite)
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
@atendente_required
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

    if data_inicio:
        query = query.filter(Ticket.criado_em >= datetime.strptime(data_inicio, '%Y-%m-%d'))
    if data_fim:
        query = query.filter(Ticket.criado_em <= datetime.strptime(data_fim, '%Y-%m-%d') + timedelta(days=1))
    if status:
        query = query.filter(Ticket.status == status)
    if prioridade:
        query = query.filter(Ticket.prioridade == prioridade)
    if atendente_id:
        query = query.filter(Ticket.atendente_id == atendente_id)
    if categoria_id:
        query = query.filter(Ticket.categoria_id == categoria_id)
    if empresa:
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
        'Cliente', 'Empresa', 'Atendente', 'Criado em', 'Resolvido em',
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
            ticket.resolvido_em.strftime('%d/%m/%Y %H:%M') if ticket.resolvido_em else '',
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
