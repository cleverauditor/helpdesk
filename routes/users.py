from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from functools import wraps
from models import db, User, Category, SLAConfig

users_bp = Blueprint('users', __name__, url_prefix='/usuarios')


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin():
            flash('Acesso restrito a administradores.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function


@users_bp.route('/')
@login_required
@admin_required
def lista():
    page = request.args.get('page', 1, type=int)
    per_page = 20

    query = User.query

    # Filtros
    tipo = request.args.get('tipo')
    ativo = request.args.get('ativo')
    busca = request.args.get('busca', '').strip()

    if tipo:
        query = query.filter(User.tipo == tipo)
    if ativo is not None and ativo != '':
        query = query.filter(User.ativo == (ativo == '1'))
    if busca:
        query = query.filter(
            db.or_(
                User.nome.ilike(f'%{busca}%'),
                User.email.ilike(f'%{busca}%')
            )
        )

    users = query.order_by(User.nome).paginate(page=page, per_page=per_page, error_out=False)

    return render_template('users/list.html', users=users)


@users_bp.route('/criar', methods=['GET', 'POST'])
@login_required
@admin_required
def criar():
    categorias = Category.query.filter_by(ativo=True).order_by(Category.nome).all()

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')
        tipo = request.form.get('tipo', 'cliente_externo')
        empresa = request.form.get('empresa', '').strip()
        departamento = request.form.get('departamento', '').strip()
        telefone = request.form.get('telefone', '').strip()

        # Validações
        errors = []
        if not nome or len(nome) < 3:
            errors.append('Nome deve ter pelo menos 3 caracteres.')
        if not email or '@' not in email:
            errors.append('Email inválido.')
        if len(senha) < 6:
            errors.append('Senha deve ter pelo menos 6 caracteres.')
        if User.query.filter_by(email=email).first():
            errors.append('Este email já está cadastrado.')

        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('users/form.html', user=None, categorias=categorias)

        user = User(
            nome=nome,
            email=email,
            tipo=tipo,
            empresa=empresa if tipo == 'cliente_externo' else None,
            departamento=departamento,
            telefone=telefone
        )
        user.set_senha(senha)

        db.session.add(user)
        db.session.flush()

        # Adicionar categorias do atendente
        if tipo in ['admin', 'atendente']:
            categorias_ids = request.form.getlist('categorias', type=int)
            for cat_id in categorias_ids:
                categoria = Category.query.get(cat_id)
                if categoria:
                    user.categorias.append(categoria)

        db.session.commit()

        flash(f'Usuário {nome} criado com sucesso!', 'success')
        return redirect(url_for('users.lista'))

    return render_template('users/form.html', user=None, categorias=categorias)


@users_bp.route('/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def editar(id):
    user = User.query.get_or_404(id)
    categorias = Category.query.filter_by(ativo=True).order_by(Category.nome).all()

    if request.method == 'POST':
        user.nome = request.form.get('nome', '').strip()
        user.tipo = request.form.get('tipo', user.tipo)
        user.empresa = request.form.get('empresa', '').strip() if user.tipo == 'cliente_externo' else None
        user.departamento = request.form.get('departamento', '').strip()
        user.telefone = request.form.get('telefone', '').strip()
        user.ativo = request.form.get('ativo') == '1'

        nova_senha = request.form.get('senha', '')
        if nova_senha and len(nova_senha) >= 6:
            user.set_senha(nova_senha)

        # Atualizar categorias do atendente
        if user.tipo in ['admin', 'atendente']:
            categorias_ids = request.form.getlist('categorias', type=int)
            # Limpar categorias atuais
            user.categorias = []
            # Adicionar novas
            for cat_id in categorias_ids:
                categoria = Category.query.get(cat_id)
                if categoria:
                    user.categorias.append(categoria)

        db.session.commit()

        flash(f'Usuário {user.nome} atualizado!', 'success')
        return redirect(url_for('users.lista'))

    return render_template('users/form.html', user=user, categorias=categorias)


@users_bp.route('/<int:id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle_ativo(id):
    user = User.query.get_or_404(id)

    if user.id == current_user.id:
        flash('Você não pode desativar sua própria conta.', 'danger')
        return redirect(url_for('users.lista'))

    user.ativo = not user.ativo
    db.session.commit()

    status = 'ativado' if user.ativo else 'desativado'
    flash(f'Usuário {user.nome} {status}.', 'success')

    return redirect(url_for('users.lista'))


# Rotas de Categorias
@users_bp.route('/categorias')
@login_required
@admin_required
def categorias():
    categorias = Category.query.order_by(Category.nome).all()
    return render_template('users/categorias.html', categorias=categorias)


@users_bp.route('/categorias/criar', methods=['POST'])
@login_required
@admin_required
def criar_categoria():
    nome = request.form.get('nome', '').strip()
    descricao = request.form.get('descricao', '').strip()

    if not nome:
        flash('Nome da categoria é obrigatório.', 'danger')
        return redirect(url_for('users.categorias'))

    if Category.query.filter_by(nome=nome).first():
        flash('Já existe uma categoria com este nome.', 'danger')
        return redirect(url_for('users.categorias'))

    categoria = Category(nome=nome, descricao=descricao)
    db.session.add(categoria)
    db.session.commit()

    flash(f'Categoria {nome} criada!', 'success')
    return redirect(url_for('users.categorias'))


@users_bp.route('/categorias/<int:id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle_categoria(id):
    categoria = Category.query.get_or_404(id)
    categoria.ativo = not categoria.ativo
    db.session.commit()

    status = 'ativada' if categoria.ativo else 'desativada'
    flash(f'Categoria {categoria.nome} {status}.', 'success')

    return redirect(url_for('users.categorias'))


# Rotas de Configuração de SLA
@users_bp.route('/sla')
@login_required
@admin_required
def sla_config():
    # Garantir que todas as prioridades existam
    prioridades = ['critica', 'alta', 'media', 'baixa']
    defaults = {
        'critica': (1, 4),
        'alta': (2, 8),
        'media': (4, 24),
        'baixa': (8, 48)
    }

    for prioridade in prioridades:
        if not SLAConfig.query.filter_by(prioridade=prioridade).first():
            resp, resol = defaults[prioridade]
            sla = SLAConfig(
                prioridade=prioridade,
                tempo_resposta_horas=resp,
                tempo_resolucao_horas=resol
            )
            db.session.add(sla)
    db.session.commit()

    slas = SLAConfig.query.order_by(
        db.case(
            (SLAConfig.prioridade == 'critica', 1),
            (SLAConfig.prioridade == 'alta', 2),
            (SLAConfig.prioridade == 'media', 3),
            (SLAConfig.prioridade == 'baixa', 4),
        )
    ).all()

    return render_template('users/sla.html', slas=slas)


@users_bp.route('/sla/atualizar', methods=['POST'])
@login_required
@admin_required
def atualizar_sla():
    prioridades = ['critica', 'alta', 'media', 'baixa']

    for prioridade in prioridades:
        sla = SLAConfig.query.filter_by(prioridade=prioridade).first()
        if sla:
            resp = request.form.get(f'resposta_{prioridade}', type=int)
            resol = request.form.get(f'resolucao_{prioridade}', type=int)

            if resp and resp > 0:
                sla.tempo_resposta_horas = resp
            if resol and resol > 0:
                sla.tempo_resolucao_horas = resol

    db.session.commit()
    flash('Configurações de SLA atualizadas com sucesso!', 'success')

    return redirect(url_for('users.sla_config'))
