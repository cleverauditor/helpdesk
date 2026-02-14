from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from functools import wraps
from models import db, User, Category, SLAConfig, SLACliente, Cliente, atendente_categoria

users_bp = Blueprint('users', __name__, url_prefix='/usuarios')


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin():
            flash('Acesso restrito a administradores.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def admin_ou_gestor_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin() and not current_user.is_gestor():
            flash('Acesso restrito a administradores e gestores.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def _gestor_pode_gerenciar(user_alvo):
    """Verifica se o gestor atual pode gerenciar o usuário alvo."""
    # Gestor não pode gerenciar admins ou outros gestores
    if user_alvo.tipo in ['admin', 'gestor']:
        return False
    # Gestor só pode gerenciar usuários que compartilham pelo menos 1 categoria
    gestor_cat_ids = set(current_user.get_categorias_ids())
    user_cat_ids = set(user_alvo.get_categorias_ids())
    # Se o usuário não tem categorias, considerar como gerenciável (novo ou sem vínculo)
    if not user_cat_ids:
        return True
    return bool(gestor_cat_ids & user_cat_ids)


@users_bp.route('/')
@login_required
@admin_ou_gestor_required
def lista():
    page = request.args.get('page', 1, type=int)
    per_page = 20

    query = User.query

    # Filtros
    tipo = request.args.get('tipo')
    ativo = request.args.get('ativo')
    busca = request.args.get('busca', '').strip()

    # Gestor: filtrar apenas usuários das suas categorias (atendentes e clientes)
    if current_user.is_gestor():
        gestor_cat_ids = current_user.get_categorias_ids()
        # Buscar IDs de usuários que compartilham categorias com o gestor
        if gestor_cat_ids:
            users_com_categoria = db.session.query(atendente_categoria.c.user_id).filter(
                atendente_categoria.c.categoria_id.in_(gestor_cat_ids)
            ).distinct().subquery()
            query = query.filter(
                db.and_(
                    User.tipo.notin_(['admin', 'gestor']),
                    db.or_(
                        User.id.in_(db.session.query(users_com_categoria)),
                        # Incluir usuários sem categoria (para poder vincular)
                        ~User.id.in_(db.session.query(atendente_categoria.c.user_id).distinct())
                    )
                )
            )
        else:
            # Gestor sem categorias não vê ninguém
            query = query.filter(db.literal(False))

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

    return render_template('users/list.html', users=users, is_gestor=current_user.is_gestor())


@users_bp.route('/criar', methods=['GET', 'POST'])
@login_required
@admin_ou_gestor_required
def criar():
    # Gestor só vê suas próprias categorias
    if current_user.is_gestor():
        categorias = current_user.categorias.filter_by(ativo=True).order_by(Category.nome).all()
    else:
        categorias = Category.query.filter_by(ativo=True).order_by(Category.nome).all()
    clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
    is_gestor = current_user.is_gestor()

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')
        tipo = request.form.get('tipo', 'cliente_externo')
        cliente_id = request.form.get('cliente_id', type=int)
        departamento = request.form.get('departamento', '').strip()
        telefone = request.form.get('telefone', '').strip()

        # Gestor não pode criar admin ou gestor
        if is_gestor and tipo in ['admin', 'gestor']:
            flash('Você não tem permissão para criar este tipo de usuário.', 'danger')
            return render_template('users/form.html', user=None, categorias=categorias,
                                   clientes=clientes, is_gestor=is_gestor)

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
            return render_template('users/form.html', user=None, categorias=categorias,
                                   clientes=clientes, is_gestor=is_gestor)

        # Buscar nome da empresa pelo cliente selecionado
        empresa = None
        user_cliente_id = None
        if tipo == 'cliente_externo' and cliente_id:
            cliente = Cliente.query.get(cliente_id)
            if cliente:
                empresa = cliente.nome
                user_cliente_id = cliente.id

        user = User(
            nome=nome,
            email=email,
            tipo=tipo,
            empresa=empresa,
            cliente_id=user_cliente_id,
            departamento=departamento,
            telefone=telefone
        )
        user.set_senha(senha)

        db.session.add(user)
        db.session.flush()

        # Adicionar categorias (atendentes, gestores e clientes)
        if tipo != 'admin':
            categorias_ids = request.form.getlist('categorias', type=int)
            # Gestor só pode atribuir suas próprias categorias
            if is_gestor:
                gestor_cat_ids = set(current_user.get_categorias_ids())
                categorias_ids = [cid for cid in categorias_ids if cid in gestor_cat_ids]
            for cat_id in categorias_ids:
                categoria = Category.query.get(cat_id)
                if categoria:
                    user.categorias.append(categoria)

        db.session.commit()

        flash(f'Usuário {nome} criado com sucesso!', 'success')
        return redirect(url_for('users.lista'))

    return render_template('users/form.html', user=None, categorias=categorias,
                           clientes=clientes, is_gestor=is_gestor)


@users_bp.route('/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@admin_ou_gestor_required
def editar(id):
    user = User.query.get_or_404(id)
    is_gestor = current_user.is_gestor()

    # Gestor só pode editar atendentes e clientes das suas categorias
    if is_gestor and not _gestor_pode_gerenciar(user):
        flash('Você não tem permissão para editar este usuário.', 'danger')
        return redirect(url_for('users.lista'))

    # Gestor só vê suas próprias categorias
    if is_gestor:
        categorias = current_user.categorias.filter_by(ativo=True).order_by(Category.nome).all()
    else:
        categorias = Category.query.filter_by(ativo=True).order_by(Category.nome).all()
    clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()

    if request.method == 'POST':
        user.nome = request.form.get('nome', '').strip()
        novo_tipo = request.form.get('tipo', user.tipo)

        # Gestor não pode mudar tipo para admin ou gestor
        if is_gestor and novo_tipo in ['admin', 'gestor']:
            flash('Você não tem permissão para definir este tipo de usuário.', 'danger')
            return render_template('users/form.html', user=user, categorias=categorias,
                                   clientes=clientes, is_gestor=is_gestor)

        user.tipo = novo_tipo
        user.departamento = request.form.get('departamento', '').strip()
        user.telefone = request.form.get('telefone', '').strip()
        user.ativo = request.form.get('ativo') == '1'

        # Empresa via select de clientes
        if user.tipo == 'cliente_externo':
            cliente_id = request.form.get('cliente_id', type=int)
            if cliente_id:
                cliente = Cliente.query.get(cliente_id)
                user.empresa = cliente.nome if cliente else None
                user.cliente_id = cliente.id if cliente else None
            else:
                user.empresa = None
                user.cliente_id = None
        else:
            user.empresa = None
            user.cliente_id = None

        nova_senha = request.form.get('senha', '')
        if nova_senha and len(nova_senha) >= 6:
            user.set_senha(nova_senha)

        # Atualizar categorias (atendentes, gestores e clientes)
        if user.tipo != 'admin':
            categorias_ids = request.form.getlist('categorias', type=int)
            if is_gestor:
                # Gestor só pode atribuir suas próprias categorias
                # Manter categorias do usuário que não pertencem ao gestor
                gestor_cat_ids = set(current_user.get_categorias_ids())
                categorias_ids_validas = [cid for cid in categorias_ids if cid in gestor_cat_ids]
                # Categorias que o usuário tem mas não são do gestor (manter intocadas)
                user_cat_ids_fora = [c.id for c in user.categorias.all() if c.id not in gestor_cat_ids]
                user.categorias = []
                for cat_id in categorias_ids_validas + user_cat_ids_fora:
                    categoria = Category.query.get(cat_id)
                    if categoria:
                        user.categorias.append(categoria)
            else:
                # Admin: limpar e reatribuir
                user.categorias = []
                for cat_id in categorias_ids:
                    categoria = Category.query.get(cat_id)
                    if categoria:
                        user.categorias.append(categoria)
        else:
            # Admin não tem restrição de categorias
            user.categorias = []

        db.session.commit()

        flash(f'Usuário {user.nome} atualizado!', 'success')
        return redirect(url_for('users.lista'))

    return render_template('users/form.html', user=user, categorias=categorias,
                           clientes=clientes, is_gestor=is_gestor)


@users_bp.route('/<int:id>/toggle', methods=['POST'])
@login_required
@admin_ou_gestor_required
def toggle_ativo(id):
    user = User.query.get_or_404(id)

    if user.id == current_user.id:
        flash('Você não pode desativar sua própria conta.', 'danger')
        return redirect(url_for('users.lista'))

    # Gestor só pode ativar/desativar usuários das suas categorias
    if current_user.is_gestor() and not _gestor_pode_gerenciar(user):
        flash('Você não tem permissão para alterar este usuário.', 'danger')
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


# Rotas de SLA por Cliente
@users_bp.route('/sla-clientes')
@login_required
@admin_required
def sla_clientes():
    sla_list = SLACliente.query.join(Cliente).order_by(Cliente.nome).all()
    return render_template('users/sla_clientes.html', sla_list=sla_list)


@users_bp.route('/sla-clientes/criar', methods=['GET', 'POST'])
@login_required
@admin_required
def criar_sla_cliente():
    # Clientes que ainda não têm SLA personalizado
    clientes_com_sla = db.session.query(SLACliente.cliente_id).subquery()
    clientes = Cliente.query.filter(
        Cliente.ativo == True,
        ~Cliente.id.in_(clientes_com_sla)
    ).order_by(Cliente.nome).all()

    # SLA padrão para preencher os campos
    sla_padrao = {s.prioridade: s for s in SLAConfig.query.all()}

    if request.method == 'POST':
        cliente_id = request.form.get('cliente_id', type=int)
        if not cliente_id:
            flash('Selecione um cliente.', 'danger')
            return render_template('users/sla_cliente_form.html',
                                   sla=None, clientes=clientes, sla_padrao=sla_padrao)

        # Verificar se já existe
        if SLACliente.query.filter_by(cliente_id=cliente_id).first():
            flash('Este cliente já possui SLA personalizado.', 'danger')
            return redirect(url_for('users.sla_clientes'))

        sla_cliente = SLACliente(
            cliente_id=cliente_id,
            critica_resposta_horas=request.form.get('critica_resposta', 1, type=int),
            critica_resolucao_horas=request.form.get('critica_resolucao', 4, type=int),
            alta_resposta_horas=request.form.get('alta_resposta', 2, type=int),
            alta_resolucao_horas=request.form.get('alta_resolucao', 8, type=int),
            media_resposta_horas=request.form.get('media_resposta', 4, type=int),
            media_resolucao_horas=request.form.get('media_resolucao', 24, type=int),
            baixa_resposta_horas=request.form.get('baixa_resposta', 8, type=int),
            baixa_resolucao_horas=request.form.get('baixa_resolucao', 48, type=int),
        )
        db.session.add(sla_cliente)
        db.session.commit()

        flash(f'SLA personalizado criado para {sla_cliente.cliente.nome}!', 'success')
        return redirect(url_for('users.sla_clientes'))

    return render_template('users/sla_cliente_form.html',
                           sla=None, clientes=clientes, sla_padrao=sla_padrao)


@users_bp.route('/sla-clientes/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_sla_cliente(id):
    sla = SLACliente.query.get_or_404(id)

    if request.method == 'POST':
        sla.critica_resposta_horas = request.form.get('critica_resposta', 1, type=int)
        sla.critica_resolucao_horas = request.form.get('critica_resolucao', 4, type=int)
        sla.alta_resposta_horas = request.form.get('alta_resposta', 2, type=int)
        sla.alta_resolucao_horas = request.form.get('alta_resolucao', 8, type=int)
        sla.media_resposta_horas = request.form.get('media_resposta', 4, type=int)
        sla.media_resolucao_horas = request.form.get('media_resolucao', 24, type=int)
        sla.baixa_resposta_horas = request.form.get('baixa_resposta', 8, type=int)
        sla.baixa_resolucao_horas = request.form.get('baixa_resolucao', 48, type=int)
        sla.ativo = request.form.get('ativo') == '1'

        db.session.commit()
        flash(f'SLA de {sla.cliente.nome} atualizado!', 'success')
        return redirect(url_for('users.sla_clientes'))

    sla_padrao = {s.prioridade: s for s in SLAConfig.query.all()}
    return render_template('users/sla_cliente_form.html',
                           sla=sla, clientes=[], sla_padrao=sla_padrao)


@users_bp.route('/sla-clientes/<int:id>/excluir', methods=['POST'])
@login_required
@admin_required
def excluir_sla_cliente(id):
    sla = SLACliente.query.get_or_404(id)
    nome = sla.cliente.nome
    db.session.delete(sla)
    db.session.commit()
    flash(f'SLA personalizado de {nome} removido. Será usado o SLA padrão.', 'success')
    return redirect(url_for('users.sla_clientes'))
