from datetime import datetime, timedelta
from flask import Blueprint, render_template, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func
from models import db, Ticket, User, Category, TicketHistory

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
@login_required
def index():
    # Estatísticas gerais
    if current_user.is_cliente():
        base_query = Ticket.query.filter_by(cliente_id=current_user.id)
    else:
        base_query = Ticket.query

    stats = {
        'total': base_query.count(),
        'abertos': base_query.filter_by(status='aberto').count(),
        'em_andamento': base_query.filter_by(status='em_andamento').count(),
        'fechados': base_query.filter_by(status='fechado').count()
    }

    # SLA Stats
    now = datetime.utcnow()
    tickets_ativos = base_query.filter(Ticket.status.in_(['aberto', 'em_andamento'])).all()

    sla_ok = 0
    sla_risco = 0
    sla_violado = 0

    for ticket in tickets_ativos:
        if ticket.sla_resolucao_limite:
            if now > ticket.sla_resolucao_limite:
                sla_violado += 1
            elif now > ticket.sla_resolucao_limite - timedelta(hours=2):
                sla_risco += 1
            else:
                sla_ok += 1

    stats['sla_ok'] = sla_ok
    stats['sla_risco'] = sla_risco
    stats['sla_violado'] = sla_violado

    # Últimos chamados
    ultimos_tickets = base_query.order_by(Ticket.criado_em.desc()).limit(10).all()

    # Chamados próximos do SLA (para atendentes)
    chamados_urgentes = []
    if current_user.is_atendente():
        chamados_urgentes = Ticket.query.filter(
            Ticket.status.in_(['aberto', 'em_andamento']),
            Ticket.sla_resolucao_limite < now + timedelta(hours=4)
        ).order_by(Ticket.sla_resolucao_limite).limit(5).all()

    return render_template('dashboard.html',
                          stats=stats,
                          ultimos_tickets=ultimos_tickets,
                          chamados_urgentes=chamados_urgentes)


@dashboard_bp.route('/api/stats/por-status')
@login_required
def stats_por_status():
    if current_user.is_cliente():
        base_query = Ticket.query.filter_by(cliente_id=current_user.id)
    else:
        base_query = Ticket.query

    resultado = db.session.query(
        Ticket.status,
        func.count(Ticket.id)
    ).group_by(Ticket.status).all()

    labels = []
    data = []
    colors = {
        'aberto': '#f7941d',
        'em_andamento': '#ffc107',
        'fechado': '#198754'
    }

    for status, count in resultado:
        labels.append(status.replace('_', ' ').title())
        data.append(count)

    return jsonify({
        'labels': labels,
        'data': data,
        'colors': [colors.get(r[0], '#000') for r in resultado]
    })


@dashboard_bp.route('/api/stats/por-categoria')
@login_required
def stats_por_categoria():
    resultado = db.session.query(
        Category.nome,
        func.count(Ticket.id)
    ).join(Ticket, Ticket.categoria_id == Category.id)\
     .group_by(Category.nome).all()

    return jsonify({
        'labels': [r[0] for r in resultado],
        'data': [r[1] for r in resultado]
    })


@dashboard_bp.route('/api/stats/timeline')
@login_required
def stats_timeline():
    # Últimos 30 dias
    hoje = datetime.utcnow().date()
    inicio = hoje - timedelta(days=30)

    # Chamados criados por dia
    criados = db.session.query(
        func.date(Ticket.criado_em),
        func.count(Ticket.id)
    ).filter(Ticket.criado_em >= inicio)\
     .group_by(func.date(Ticket.criado_em)).all()

    # Chamados fechados por dia
    fechados = db.session.query(
        func.date(Ticket.fechado_em),
        func.count(Ticket.id)
    ).filter(Ticket.fechado_em >= inicio)\
     .group_by(func.date(Ticket.fechado_em)).all()

    # Criar dicionários para lookup
    criados_dict = {str(d): c for d, c in criados}
    fechados_dict = {str(d): c for d, c in fechados}

    # Gerar labels e dados para os últimos 30 dias
    labels = []
    data_criados = []
    data_fechados = []

    for i in range(30):
        dia = inicio + timedelta(days=i)
        labels.append(dia.strftime('%d/%m'))
        data_criados.append(criados_dict.get(str(dia), 0))
        data_fechados.append(fechados_dict.get(str(dia), 0))

    return jsonify({
        'labels': labels,
        'criados': data_criados,
        'fechados': data_fechados
    })


@dashboard_bp.route('/api/stats/top-atendentes')
@login_required
def stats_top_atendentes():
    # Top 5 atendentes por tickets fechados no mês
    inicio_mes = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    resultado = db.session.query(
        User.nome,
        func.count(Ticket.id)
    ).join(Ticket, Ticket.atendente_id == User.id)\
     .filter(Ticket.fechado_em >= inicio_mes)\
     .group_by(User.nome)\
     .order_by(func.count(Ticket.id).desc())\
     .limit(5).all()

    return jsonify({
        'labels': [r[0] for r in resultado],
        'data': [r[1] for r in resultado]
    })


@dashboard_bp.route('/api/stats/sla')
@login_required
def stats_sla():
    # Taxa de cumprimento de SLA
    tickets_fechados = Ticket.query.filter(Ticket.fechado_em.isnot(None)).all()

    dentro_sla = 0
    fora_sla = 0

    for ticket in tickets_fechados:
        if ticket.sla_resolucao_limite and ticket.fechado_em:
            if ticket.fechado_em <= ticket.sla_resolucao_limite:
                dentro_sla += 1
            else:
                fora_sla += 1

    return jsonify({
        'dentro_sla': dentro_sla,
        'fora_sla': fora_sla,
        'taxa': round(dentro_sla / (dentro_sla + fora_sla) * 100, 1) if (dentro_sla + fora_sla) > 0 else 0
    })
