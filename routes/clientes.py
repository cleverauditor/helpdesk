from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from functools import wraps
from models import db, Cliente

clientes_bp = Blueprint('clientes', __name__, url_prefix='/clientes')


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin():
            flash('Acesso restrito a administradores.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


@clientes_bp.route('/')
@login_required
@admin_required
def lista():
    page = request.args.get('page', 1, type=int)
    per_page = 20

    query = Cliente.query

    # Filtros
    busca = request.args.get('busca', '').strip()
    ativo = request.args.get('ativo')

    if busca:
        query = query.filter(
            db.or_(
                Cliente.nome.ilike(f'%{busca}%'),
                Cliente.cnpj.ilike(f'%{busca}%'),
                Cliente.cidade.ilike(f'%{busca}%')
            )
        )
    if ativo is not None and ativo != '':
        query = query.filter(Cliente.ativo == (ativo == '1'))

    clientes = query.order_by(Cliente.nome).paginate(page=page, per_page=per_page, error_out=False)

    return render_template('clientes/list.html', clientes=clientes)


@clientes_bp.route('/criar', methods=['GET', 'POST'])
@login_required
@admin_required
def criar():
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        razao_social = request.form.get('razao_social', '').strip()
        cnpj = request.form.get('cnpj', '').strip()
        endereco = request.form.get('endereco', '').strip()
        cidade = request.form.get('cidade', '').strip()
        estado = request.form.get('estado', '').strip().upper()
        cep = request.form.get('cep', '').strip()
        telefone = request.form.get('telefone', '').strip()
        email = request.form.get('email', '').strip()
        contato = request.form.get('contato', '').strip()
        observacoes = request.form.get('observacoes', '').strip()

        # Validações
        if not nome:
            flash('Nome é obrigatório.', 'danger')
            return render_template('clientes/form.html', cliente=None)

        if cnpj and Cliente.query.filter_by(cnpj=cnpj).first():
            flash('CNPJ já cadastrado.', 'danger')
            return render_template('clientes/form.html', cliente=None)

        cliente = Cliente(
            nome=nome,
            razao_social=razao_social,
            cnpj=cnpj or None,
            endereco=endereco,
            cidade=cidade,
            estado=estado,
            cep=cep,
            telefone=telefone,
            email=email,
            contato=contato,
            observacoes=observacoes
        )
        db.session.add(cliente)
        db.session.commit()

        flash(f'Cliente {nome} criado com sucesso!', 'success')
        return redirect(url_for('clientes.lista'))

    return render_template('clientes/form.html', cliente=None)


@clientes_bp.route('/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def editar(id):
    cliente = Cliente.query.get_or_404(id)

    if request.method == 'POST':
        cliente.nome = request.form.get('nome', '').strip()
        cliente.razao_social = request.form.get('razao_social', '').strip()
        novo_cnpj = request.form.get('cnpj', '').strip()
        cliente.endereco = request.form.get('endereco', '').strip()
        cliente.cidade = request.form.get('cidade', '').strip()
        cliente.estado = request.form.get('estado', '').strip().upper()
        cliente.cep = request.form.get('cep', '').strip()
        cliente.telefone = request.form.get('telefone', '').strip()
        cliente.email = request.form.get('email', '').strip()
        cliente.contato = request.form.get('contato', '').strip()
        cliente.observacoes = request.form.get('observacoes', '').strip()
        cliente.ativo = request.form.get('ativo') == '1'

        # Validar CNPJ único
        if novo_cnpj and novo_cnpj != cliente.cnpj:
            if Cliente.query.filter(Cliente.cnpj == novo_cnpj, Cliente.id != cliente.id).first():
                flash('CNPJ já cadastrado para outro cliente.', 'danger')
                return render_template('clientes/form.html', cliente=cliente)
        cliente.cnpj = novo_cnpj or None

        if not cliente.nome:
            flash('Nome é obrigatório.', 'danger')
            return render_template('clientes/form.html', cliente=cliente)

        db.session.commit()
        flash(f'Cliente {cliente.nome} atualizado!', 'success')
        return redirect(url_for('clientes.lista'))

    return render_template('clientes/form.html', cliente=cliente)


@clientes_bp.route('/<int:id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle(id):
    cliente = Cliente.query.get_or_404(id)
    cliente.ativo = not cliente.ativo
    db.session.commit()

    status = 'ativado' if cliente.ativo else 'desativado'
    flash(f'Cliente {cliente.nome} {status}.', 'success')

    return redirect(url_for('clientes.lista'))
