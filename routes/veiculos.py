from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from functools import wraps
from models import db, TipoVeiculo

veiculos_bp = Blueprint('veiculos', __name__, url_prefix='/veiculos')


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin():
            flash('Acesso restrito a administradores.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


@veiculos_bp.route('/')
@login_required
@admin_required
def lista():
    veiculos = TipoVeiculo.query.order_by(TipoVeiculo.capacidade).all()
    return render_template('veiculos/list.html', veiculos=veiculos)


@veiculos_bp.route('/criar', methods=['GET', 'POST'])
@login_required
@admin_required
def criar():
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        capacidade = request.form.get('capacidade', '0').strip()
        descricao = request.form.get('descricao', '').strip()

        if not nome:
            flash('Nome é obrigatório.', 'danger')
            return render_template('veiculos/form.html', veiculo=None)

        try:
            capacidade = int(capacidade)
            if capacidade <= 0:
                raise ValueError
        except ValueError:
            flash('Capacidade deve ser um número inteiro positivo.', 'danger')
            return render_template('veiculos/form.html', veiculo=None)

        veiculo = TipoVeiculo(
            nome=nome,
            capacidade=capacidade,
            descricao=descricao
        )
        db.session.add(veiculo)
        db.session.commit()

        flash(f'Tipo de veículo "{nome}" criado com sucesso!', 'success')
        return redirect(url_for('veiculos.lista'))

    return render_template('veiculos/form.html', veiculo=None)


@veiculos_bp.route('/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def editar(id):
    veiculo = TipoVeiculo.query.get_or_404(id)

    if request.method == 'POST':
        veiculo.nome = request.form.get('nome', '').strip()
        descricao = request.form.get('descricao', '').strip()
        capacidade = request.form.get('capacidade', '0').strip()
        veiculo.ativo = request.form.get('ativo') == '1'

        if not veiculo.nome:
            flash('Nome é obrigatório.', 'danger')
            return render_template('veiculos/form.html', veiculo=veiculo)

        try:
            capacidade = int(capacidade)
            if capacidade <= 0:
                raise ValueError
        except ValueError:
            flash('Capacidade deve ser um número inteiro positivo.', 'danger')
            return render_template('veiculos/form.html', veiculo=veiculo)

        veiculo.capacidade = capacidade
        veiculo.descricao = descricao
        db.session.commit()

        flash(f'Tipo de veículo "{veiculo.nome}" atualizado!', 'success')
        return redirect(url_for('veiculos.lista'))

    return render_template('veiculos/form.html', veiculo=veiculo)


@veiculos_bp.route('/<int:id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle(id):
    veiculo = TipoVeiculo.query.get_or_404(id)
    veiculo.ativo = not veiculo.ativo
    db.session.commit()

    status = 'ativado' if veiculo.ativo else 'desativado'
    flash(f'Tipo de veículo "{veiculo.nome}" {status}.', 'success')

    return redirect(url_for('veiculos.lista'))
