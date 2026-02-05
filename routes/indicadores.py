from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from functools import wraps
from datetime import date, datetime

from models import (db, Category, IndicadorCategoria, Indicador,
                    IndicadorRegistro, agora_brasil)

indicadores_bp = Blueprint('indicadores', __name__, url_prefix='/indicadores')


def indicadores_required(f):
    """Verifica acesso ao módulo de indicadores"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.is_admin():
            return f(*args, **kwargs)
        categoria = Category.query.filter_by(nome='Indicadores Diretoria').first()
        if not categoria or categoria not in current_user.categorias.all():
            flash('Acesso restrito ao módulo de indicadores.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin():
            flash('Acesso restrito a administradores.', 'danger')
            return redirect(url_for('indicadores.painel'))
        return f(*args, **kwargs)
    return decorated_function


# ============================================
# PAINEL PRINCIPAL
# ============================================

@indicadores_bp.route('/')
@login_required
@indicadores_required
def painel():
    # Período selecionado (default: mês atual)
    periodo = request.args.get('periodo', '')
    if periodo:
        try:
            ano, mes = periodo.split('-')
            mes_ref = date(int(ano), int(mes), 1)
        except (ValueError, IndexError):
            mes_ref = date(agora_brasil().year, agora_brasil().month, 1)
    else:
        mes_ref = date(agora_brasil().year, agora_brasil().month, 1)

    # Buscar categorias e indicadores ativos
    categorias = IndicadorCategoria.query.filter_by(ativo=True)\
        .order_by(IndicadorCategoria.ordem, IndicadorCategoria.nome).all()

    # Buscar registros do mês
    registros = IndicadorRegistro.query.filter_by(mes_referencia=mes_ref).all()
    registros_map = {r.indicador_id: r for r in registros}

    # Estatísticas
    total_indicadores = Indicador.query.filter_by(ativo=True).count()
    preenchidos = sum(1 for r in registros if r.status in ('preenchido', 'conferido'))
    conferidos = sum(1 for r in registros if r.status == 'conferido')

    return render_template('indicadores/index.html',
                           categorias=categorias,
                           registros_map=registros_map,
                           mes_ref=mes_ref,
                           total=total_indicadores,
                           preenchidos=preenchidos,
                           conferidos=conferidos)


# ============================================
# GERENCIAR CATEGORIAS E INDICADORES (ADMIN)
# ============================================

@indicadores_bp.route('/gerenciar')
@login_required
@indicadores_required
@admin_required
def gerenciar():
    categorias = IndicadorCategoria.query\
        .order_by(IndicadorCategoria.ordem, IndicadorCategoria.nome).all()
    return render_template('indicadores/gerenciar.html', categorias=categorias)


@indicadores_bp.route('/categorias/criar', methods=['POST'])
@login_required
@indicadores_required
@admin_required
def criar_categoria():
    nome = request.form.get('nome', '').strip()
    if not nome:
        flash('Nome da categoria é obrigatório.', 'danger')
        return redirect(url_for('indicadores.gerenciar'))

    if IndicadorCategoria.query.filter_by(nome=nome).first():
        flash('Já existe uma categoria com este nome.', 'danger')
        return redirect(url_for('indicadores.gerenciar'))

    ordem = IndicadorCategoria.query.count()
    cat = IndicadorCategoria(nome=nome, ordem=ordem)
    db.session.add(cat)
    db.session.commit()

    flash(f'Categoria "{nome}" criada!', 'success')
    return redirect(url_for('indicadores.gerenciar'))


@indicadores_bp.route('/categorias/<int:id>/toggle', methods=['POST'])
@login_required
@indicadores_required
@admin_required
def toggle_categoria(id):
    cat = IndicadorCategoria.query.get_or_404(id)
    cat.ativo = not cat.ativo
    db.session.commit()

    status = 'ativada' if cat.ativo else 'desativada'
    flash(f'Categoria "{cat.nome}" {status}.', 'success')
    return redirect(url_for('indicadores.gerenciar'))


# ============================================
# CRUD INDICADORES
# ============================================

@indicadores_bp.route('/criar', methods=['GET', 'POST'])
@login_required
@indicadores_required
@admin_required
def criar():
    categorias = IndicadorCategoria.query.filter_by(ativo=True)\
        .order_by(IndicadorCategoria.ordem).all()

    if request.method == 'POST':
        categoria_id = request.form.get('categoria_id', type=int)
        nome = request.form.get('nome', '').strip()
        descricao = request.form.get('descricao', '').strip()
        responsavel_geracao = request.form.get('responsavel_geracao', '').strip()
        responsavel_conferencia = request.form.get('responsavel_conferencia', '').strip()

        if not nome or not categoria_id:
            flash('Categoria e nome são obrigatórios.', 'danger')
            return render_template('indicadores/form.html',
                                   indicador=None, categorias=categorias)

        ordem = Indicador.query.filter_by(categoria_id=categoria_id).count()
        indicador = Indicador(
            categoria_id=categoria_id,
            nome=nome,
            descricao=descricao,
            responsavel_geracao=responsavel_geracao,
            responsavel_conferencia=responsavel_conferencia,
            ordem=ordem
        )
        db.session.add(indicador)
        db.session.commit()

        flash(f'Indicador "{nome}" criado!', 'success')
        return redirect(url_for('indicadores.gerenciar'))

    return render_template('indicadores/form.html',
                           indicador=None, categorias=categorias)


@indicadores_bp.route('/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@indicadores_required
@admin_required
def editar(id):
    indicador = Indicador.query.get_or_404(id)
    categorias = IndicadorCategoria.query.filter_by(ativo=True)\
        .order_by(IndicadorCategoria.ordem).all()

    if request.method == 'POST':
        indicador.categoria_id = request.form.get('categoria_id', type=int)
        indicador.nome = request.form.get('nome', '').strip()
        indicador.descricao = request.form.get('descricao', '').strip()
        indicador.responsavel_geracao = request.form.get('responsavel_geracao', '').strip()
        indicador.responsavel_conferencia = request.form.get('responsavel_conferencia', '').strip()

        if not indicador.nome or not indicador.categoria_id:
            flash('Categoria e nome são obrigatórios.', 'danger')
            return render_template('indicadores/form.html',
                                   indicador=indicador, categorias=categorias)

        db.session.commit()
        flash(f'Indicador "{indicador.nome}" atualizado!', 'success')
        return redirect(url_for('indicadores.gerenciar'))

    return render_template('indicadores/form.html',
                           indicador=indicador, categorias=categorias)


@indicadores_bp.route('/<int:id>/toggle', methods=['POST'])
@login_required
@indicadores_required
@admin_required
def toggle(id):
    indicador = Indicador.query.get_or_404(id)
    indicador.ativo = not indicador.ativo
    db.session.commit()

    status = 'ativado' if indicador.ativo else 'desativado'
    flash(f'Indicador "{indicador.nome}" {status}.', 'success')
    return redirect(url_for('indicadores.gerenciar'))


# ============================================
# REGISTRO MENSAL
# ============================================

@indicadores_bp.route('/registro/<int:indicador_id>/<periodo>', methods=['GET', 'POST'])
@login_required
@indicadores_required
def registro(indicador_id, periodo):
    indicador = Indicador.query.get_or_404(indicador_id)

    try:
        ano, mes = periodo.split('-')
        mes_ref = date(int(ano), int(mes), 1)
    except (ValueError, IndexError):
        flash('Período inválido.', 'danger')
        return redirect(url_for('indicadores.painel'))

    # Buscar ou criar registro
    reg = IndicadorRegistro.query.filter_by(
        indicador_id=indicador_id,
        mes_referencia=mes_ref
    ).first()

    if request.method == 'POST':
        valor_texto = request.form.get('valor_texto', '').strip()
        observacoes = request.form.get('observacoes', '').strip()

        if not reg:
            reg = IndicadorRegistro(
                indicador_id=indicador_id,
                mes_referencia=mes_ref
            )
            db.session.add(reg)

        reg.valor_texto = valor_texto
        reg.observacoes = observacoes
        reg.status = 'preenchido'
        reg.preenchido_por_id = current_user.id
        reg.data_preenchimento = agora_brasil()

        db.session.commit()
        flash(f'Dados do indicador "{indicador.nome}" salvos!', 'success')
        return redirect(url_for('indicadores.painel', periodo=periodo))

    # Histórico recente
    historico = indicador.registros.limit(12).all()

    return render_template('indicadores/registro.html',
                           indicador=indicador,
                           registro=reg,
                           mes_ref=mes_ref,
                           periodo=periodo,
                           historico=historico)


@indicadores_bp.route('/registro/<int:id>/conferir', methods=['POST'])
@login_required
@indicadores_required
def conferir(id):
    reg = IndicadorRegistro.query.get_or_404(id)

    if reg.status != 'preenchido':
        flash('Este registro não está pronto para conferência.', 'warning')
        return redirect(url_for('indicadores.painel'))

    reg.status = 'conferido'
    reg.conferido_por_id = current_user.id
    reg.data_conferencia = agora_brasil()
    db.session.commit()

    flash(f'Indicador "{reg.indicador.nome}" conferido!', 'success')

    periodo = reg.mes_referencia.strftime('%Y-%m')
    return redirect(url_for('indicadores.painel', periodo=periodo))
