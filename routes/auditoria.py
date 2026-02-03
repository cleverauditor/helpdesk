from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, send_from_directory
from flask_login import login_required, current_user
from functools import wraps
from werkzeug.utils import secure_filename
from datetime import datetime
import uuid
import os

from models import db, User, Category, Modal, Rota, RotaTurno, RotaHistory, Auditoria, Cliente, TurnoPadrao

auditoria_bp = Blueprint('auditoria', __name__, url_prefix='/auditoria')


def auditoria_required(f):
    """Decorator que verifica se o usuário tem acesso ao módulo de auditoria"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.is_admin():
            return f(*args, **kwargs)
        # Verificar se tem a categoria "Auditoria"
        categoria_auditoria = Category.query.filter_by(nome='Auditoria').first()
        if not categoria_auditoria:
            flash('Módulo de auditoria não configurado.', 'danger')
            return redirect(url_for('dashboard.index'))
        if categoria_auditoria not in current_user.categorias.all():
            flash('Acesso restrito ao módulo de auditoria.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function


def allowed_kml_file(filename):
    """Verifica se arquivo é KML válido"""
    ALLOWED_KML_EXTENSIONS = current_app.config.get('ALLOWED_KML_EXTENSIONS', {'kml', 'kmz'})
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_KML_EXTENSIONS


def registrar_historico_rota(rota_id, usuario_id, acao, descricao, valor_anterior=None, valor_novo=None):
    """Registra entrada no histórico da rota"""
    historico = RotaHistory(
        rota_id=rota_id,
        usuario_id=usuario_id,
        acao=acao,
        descricao=descricao,
        valor_anterior=valor_anterior,
        valor_novo=valor_novo
    )
    db.session.add(historico)
    return historico


def get_clientes():
    """Retorna lista de clientes para seleção"""
    return Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()


# ============================================
# ROTAS DE MODAIS
# ============================================

@auditoria_bp.route('/modais')
@login_required
@auditoria_required
def modais():
    modais = Modal.query.order_by(Modal.nome).all()
    return render_template('auditoria/modais.html', modais=modais)


@auditoria_bp.route('/modais/criar', methods=['POST'])
@login_required
@auditoria_required
def criar_modal():
    nome = request.form.get('nome', '').strip()
    descricao = request.form.get('descricao', '').strip()

    if not nome:
        flash('Nome do modal é obrigatório.', 'danger')
        return redirect(url_for('auditoria.modais'))

    if Modal.query.filter_by(nome=nome).first():
        flash('Já existe um modal com este nome.', 'danger')
        return redirect(url_for('auditoria.modais'))

    modal = Modal(nome=nome, descricao=descricao)
    db.session.add(modal)
    db.session.commit()

    flash(f'Modal {nome} criado!', 'success')
    return redirect(url_for('auditoria.modais'))


@auditoria_bp.route('/modais/<int:id>/toggle', methods=['POST'])
@login_required
@auditoria_required
def toggle_modal(id):
    modal = Modal.query.get_or_404(id)
    modal.ativo = not modal.ativo
    db.session.commit()

    status = 'ativado' if modal.ativo else 'desativado'
    flash(f'Modal {modal.nome} {status}.', 'success')

    return redirect(url_for('auditoria.modais'))


# ============================================
# ROTAS DE TURNOS PADRÃO
# ============================================

@auditoria_bp.route('/turnos')
@login_required
@auditoria_required
def turnos_padrao():
    turnos = TurnoPadrao.query.order_by(TurnoPadrao.horario_inicio).all()
    return render_template('auditoria/turnos.html', turnos=turnos)


@auditoria_bp.route('/turnos/criar', methods=['POST'])
@login_required
@auditoria_required
def criar_turno_padrao():
    nome = request.form.get('nome', '').strip()
    horario_inicio_str = request.form.get('horario_inicio', '').strip()
    horario_termino_str = request.form.get('horario_termino', '').strip()
    descricao = request.form.get('descricao', '').strip()

    if not nome or not horario_inicio_str or not horario_termino_str:
        flash('Nome e horários são obrigatórios.', 'danger')
        return redirect(url_for('auditoria.turnos_padrao'))

    try:
        horario_inicio = datetime.strptime(horario_inicio_str, '%H:%M').time()
        horario_termino = datetime.strptime(horario_termino_str, '%H:%M').time()
    except ValueError:
        flash('Formato de horário inválido.', 'danger')
        return redirect(url_for('auditoria.turnos_padrao'))

    turno = TurnoPadrao(
        nome=nome,
        horario_inicio=horario_inicio,
        horario_termino=horario_termino,
        descricao=descricao
    )
    db.session.add(turno)
    db.session.commit()

    flash(f'Turno {nome} criado!', 'success')
    return redirect(url_for('auditoria.turnos_padrao'))


@auditoria_bp.route('/turnos/<int:id>/toggle', methods=['POST'])
@login_required
@auditoria_required
def toggle_turno_padrao(id):
    turno = TurnoPadrao.query.get_or_404(id)
    turno.ativo = not turno.ativo
    db.session.commit()

    status = 'ativado' if turno.ativo else 'desativado'
    flash(f'Turno {turno.nome} {status}.', 'success')

    return redirect(url_for('auditoria.turnos_padrao'))


# ============================================
# ROTAS DE CLIENTES (CRUD)
# ============================================

@auditoria_bp.route('/clientes')
@login_required
@auditoria_required
def lista_clientes():
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

    return render_template('auditoria/clientes/list.html', clientes=clientes)


@auditoria_bp.route('/clientes/criar', methods=['GET', 'POST'])
@login_required
@auditoria_required
def criar_cliente():
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        razao_social = request.form.get('razao_social', '').strip()
        cnpj = request.form.get('cnpj', '').strip()
        endereco = request.form.get('endereco', '').strip()
        cidade = request.form.get('cidade', '').strip()
        estado = request.form.get('estado', '').strip().upper()
        telefone = request.form.get('telefone', '').strip()
        email = request.form.get('email', '').strip()
        contato = request.form.get('contato', '').strip()
        observacoes = request.form.get('observacoes', '').strip()

        # Validações
        if not nome:
            flash('Nome é obrigatório.', 'danger')
            return render_template('auditoria/clientes/form.html', cliente=None)

        if cnpj and Cliente.query.filter_by(cnpj=cnpj).first():
            flash('CNPJ já cadastrado.', 'danger')
            return render_template('auditoria/clientes/form.html', cliente=None)

        cliente = Cliente(
            nome=nome,
            razao_social=razao_social,
            cnpj=cnpj or None,
            endereco=endereco,
            cidade=cidade,
            estado=estado,
            telefone=telefone,
            email=email,
            contato=contato,
            observacoes=observacoes
        )
        db.session.add(cliente)
        db.session.commit()

        flash(f'Cliente {nome} criado com sucesso!', 'success')
        return redirect(url_for('auditoria.lista_clientes'))

    return render_template('auditoria/clientes/form.html', cliente=None)


@auditoria_bp.route('/clientes/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@auditoria_required
def editar_cliente(id):
    cliente = Cliente.query.get_or_404(id)

    if request.method == 'POST':
        cliente.nome = request.form.get('nome', '').strip()
        cliente.razao_social = request.form.get('razao_social', '').strip()
        novo_cnpj = request.form.get('cnpj', '').strip()
        cliente.endereco = request.form.get('endereco', '').strip()
        cliente.cidade = request.form.get('cidade', '').strip()
        cliente.estado = request.form.get('estado', '').strip().upper()
        cliente.telefone = request.form.get('telefone', '').strip()
        cliente.email = request.form.get('email', '').strip()
        cliente.contato = request.form.get('contato', '').strip()
        cliente.observacoes = request.form.get('observacoes', '').strip()
        cliente.ativo = request.form.get('ativo') == '1'

        # Validar CNPJ único
        if novo_cnpj and novo_cnpj != cliente.cnpj:
            if Cliente.query.filter(Cliente.cnpj == novo_cnpj, Cliente.id != cliente.id).first():
                flash('CNPJ já cadastrado para outro cliente.', 'danger')
                return render_template('auditoria/clientes/form.html', cliente=cliente)
        cliente.cnpj = novo_cnpj or None

        if not cliente.nome:
            flash('Nome é obrigatório.', 'danger')
            return render_template('auditoria/clientes/form.html', cliente=cliente)

        db.session.commit()
        flash(f'Cliente {cliente.nome} atualizado!', 'success')
        return redirect(url_for('auditoria.lista_clientes'))

    return render_template('auditoria/clientes/form.html', cliente=cliente)


@auditoria_bp.route('/clientes/<int:id>/toggle', methods=['POST'])
@login_required
@auditoria_required
def toggle_cliente(id):
    cliente = Cliente.query.get_or_404(id)
    cliente.ativo = not cliente.ativo
    db.session.commit()

    status = 'ativado' if cliente.ativo else 'desativado'
    flash(f'Cliente {cliente.nome} {status}.', 'success')

    return redirect(url_for('auditoria.lista_clientes'))


# ============================================
# ROTAS DE ROTAS (CRUD)
# ============================================

@auditoria_bp.route('/rotas')
@login_required
@auditoria_required
def lista_rotas():
    page = request.args.get('page', 1, type=int)
    per_page = 20

    query = Rota.query

    # Filtros
    tag = request.args.get('tag', '').strip()
    cliente_id = request.args.get('cliente_id', type=int)
    modal_id = request.args.get('modal_id', type=int)
    ativo = request.args.get('ativo')

    if tag:
        query = query.filter(Rota.tag.ilike(f'%{tag}%'))
    if cliente_id:
        query = query.filter(Rota.cliente_id == cliente_id)
    if modal_id:
        query = query.filter(Rota.modal_id == modal_id)
    if ativo is not None and ativo != '':
        query = query.filter(Rota.ativo == (ativo == '1'))

    rotas = query.order_by(Rota.tag).paginate(page=page, per_page=per_page, error_out=False)

    clientes = get_clientes()
    modais = Modal.query.filter_by(ativo=True).order_by(Modal.nome).all()

    return render_template('auditoria/rotas/list.html',
                           rotas=rotas,
                           clientes=clientes,
                           modais=modais)


@auditoria_bp.route('/rotas/criar', methods=['GET', 'POST'])
@login_required
@auditoria_required
def criar_rota():
    clientes = get_clientes()
    modais = Modal.query.filter_by(ativo=True).order_by(Modal.nome).all()
    turnos_padrao = TurnoPadrao.query.filter_by(ativo=True).order_by(TurnoPadrao.horario_inicio).all()

    if request.method == 'POST':
        tag = request.form.get('tag', '').strip()
        nome = request.form.get('nome', '').strip()
        cliente_id = request.form.get('cliente_id', type=int)
        modal_id = request.form.get('modal_id', type=int)
        km_atual = request.form.get('km_atual', type=float) or 0
        data_implantacao_str = request.form.get('data_implantacao', '')

        # Validações
        errors = []
        if not tag:
            errors.append('Tag é obrigatória.')
        if Rota.query.filter_by(tag=tag).first():
            errors.append('Já existe uma rota com esta tag.')
        if not cliente_id:
            errors.append('Cliente é obrigatório.')

        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('auditoria/rotas/form.html',
                                   rota=None,
                                   turno=None,
                                   clientes=clientes,
                                   modais=modais,
                                   turnos_padrao=turnos_padrao)

        # Processar data de implantação
        data_implantacao = None
        if data_implantacao_str:
            try:
                data_implantacao = datetime.strptime(data_implantacao_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        rota = Rota(
            tag=tag,
            nome=nome,
            cliente_id=cliente_id,
            modal_id=modal_id if modal_id else None,
            km_atual=km_atual,
            data_implantacao=data_implantacao
        )

        # Upload KML
        if 'arquivo_kml' in request.files:
            file = request.files['arquivo_kml']
            if file and file.filename and allowed_kml_file(file.filename):
                filename = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4().hex}_{filename}"
                filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                rota.arquivo_kml = unique_filename
                rota.arquivo_kml_nome = filename

                # Calcular KM e tempo de trajeto do arquivo KML
                tempo_calculado = None
                try:
                    from kml_utils import analisar_kml
                    analise = analisar_kml(filepath)
                    if analise['km']:
                        rota.km_atual = analise['km']
                    if analise['tempo_minutos']:
                        tempo_calculado = analise['tempo_minutos']
                except Exception as e:
                    current_app.logger.error(f'Erro ao analisar KML: {e}')

        db.session.add(rota)
        db.session.flush()

        # Criar turno se informado
        turno_inicio_str = request.form.get('turno_inicio', '').strip()
        turno_termino_str = request.form.get('turno_termino', '').strip()
        if turno_inicio_str and turno_termino_str:
            try:
                horario_inicio = datetime.strptime(turno_inicio_str, '%H:%M').time()
                horario_termino = datetime.strptime(turno_termino_str, '%H:%M').time()
                turno_nome = request.form.get('turno_nome', '').strip()
                turno_tempo = request.form.get('turno_tempo', type=int)

                # Usar tempo calculado do KML se não informado manualmente
                if not turno_tempo and tempo_calculado:
                    turno_tempo = tempo_calculado

                turno = RotaTurno(
                    rota_id=rota.id,
                    nome=turno_nome,
                    horario_inicio=horario_inicio,
                    horario_termino=horario_termino,
                    tempo_trajeto_minutos=turno_tempo
                )
                db.session.add(turno)
            except ValueError:
                pass

        # Registrar histórico
        registrar_historico_rota(
            rota.id,
            current_user.id,
            'criado',
            f'Rota {tag} criada'
        )

        db.session.commit()

        flash(f'Rota {tag} criada com sucesso!', 'success')
        return redirect(url_for('auditoria.visualizar_rota', id=rota.id))

    return render_template('auditoria/rotas/form.html',
                           rota=None,
                           turno=None,
                           clientes=clientes,
                           modais=modais,
                           turnos_padrao=turnos_padrao)


@auditoria_bp.route('/rotas/<int:id>')
@login_required
@auditoria_required
def visualizar_rota(id):
    rota = Rota.query.get_or_404(id)
    turnos = rota.turnos.filter_by(ativo=True).all()
    historicos = rota.historicos.limit(50).all()
    auditorias = rota.auditorias.limit(10).all()

    return render_template('auditoria/rotas/view.html',
                           rota=rota,
                           turnos=turnos,
                           historicos=historicos,
                           auditorias=auditorias)


@auditoria_bp.route('/rotas/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@auditoria_required
def editar_rota(id):
    rota = Rota.query.get_or_404(id)
    clientes = get_clientes()
    modais = Modal.query.filter_by(ativo=True).order_by(Modal.nome).all()
    turnos_padrao = TurnoPadrao.query.filter_by(ativo=True).order_by(TurnoPadrao.horario_inicio).all()

    if request.method == 'POST':
        alteracoes = []

        # Tag
        nova_tag = request.form.get('tag', '').strip()
        if nova_tag and nova_tag != rota.tag:
            if Rota.query.filter(Rota.tag == nova_tag, Rota.id != rota.id).first():
                flash('Já existe uma rota com esta tag.', 'danger')
                turno = rota.turnos.filter_by(ativo=True).first()
                return render_template('auditoria/rotas/form.html',
                                       rota=rota,
                                       turno=turno,
                                       clientes=clientes,
                                       modais=modais,
                                       turnos_padrao=turnos_padrao)
            alteracoes.append(f'Tag: {rota.tag} → {nova_tag}')
            rota.tag = nova_tag

        # Nome
        novo_nome = request.form.get('nome', '').strip()
        if novo_nome != rota.nome:
            alteracoes.append(f'Nome alterado')
            rota.nome = novo_nome

        # Cliente
        novo_cliente_id = request.form.get('cliente_id', type=int)
        if novo_cliente_id and novo_cliente_id != rota.cliente_id:
            alteracoes.append(f'Cliente alterado')
            rota.cliente_id = novo_cliente_id

        # Modal
        novo_modal_id = request.form.get('modal_id', type=int)
        if novo_modal_id != rota.modal_id:
            modal_anterior = rota.modal.nome if rota.modal else 'Nenhum'
            novo_modal = Modal.query.get(novo_modal_id)
            modal_novo = novo_modal.nome if novo_modal else 'Nenhum'
            alteracoes.append(f'Modal: {modal_anterior} → {modal_novo}')
            rota.modal_id = novo_modal_id if novo_modal_id else None

        # KM
        novo_km = request.form.get('km_atual', type=float) or 0
        if novo_km != rota.km_atual:
            registrar_historico_rota(
                rota.id,
                current_user.id,
                'km_atualizado',
                f'KM atualizado: {rota.km_atual} → {novo_km}',
                str(rota.km_atual),
                str(novo_km)
            )
            rota.km_atual = novo_km

        # Data implantação
        data_implantacao_str = request.form.get('data_implantacao', '')
        if data_implantacao_str:
            try:
                nova_data = datetime.strptime(data_implantacao_str, '%Y-%m-%d').date()
                if nova_data != rota.data_implantacao:
                    alteracoes.append(f'Data de implantação alterada')
                    rota.data_implantacao = nova_data
            except ValueError:
                pass

        # Status
        rota.ativo = request.form.get('ativo') == '1'

        # Upload novo KML
        if 'arquivo_kml' in request.files:
            file = request.files['arquivo_kml']
            if file and file.filename and allowed_kml_file(file.filename):
                filename = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4().hex}_{filename}"
                filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)

                registrar_historico_rota(
                    rota.id,
                    current_user.id,
                    'kml_atualizado',
                    f'Arquivo KML atualizado: {rota.arquivo_kml_nome or "Nenhum"} → {filename}',
                    rota.arquivo_kml_nome,
                    filename
                )

                rota.arquivo_kml = unique_filename
                rota.arquivo_kml_nome = filename

                # Calcular KM e tempo de trajeto do arquivo KML
                try:
                    from kml_utils import analisar_kml
                    analise = analisar_kml(filepath)
                    if analise['km']:
                        rota.km_atual = analise['km']
                    # Atualizar tempo do turno se existir
                    if analise['tempo_minutos']:
                        turno_existente = rota.turnos.filter_by(ativo=True).first()
                        if turno_existente and not turno_existente.tempo_trajeto_minutos:
                            turno_existente.tempo_trajeto_minutos = analise['tempo_minutos']
                except Exception as e:
                    current_app.logger.error(f'Erro ao analisar KML: {e}')

        # Atualizar turno
        turno_inicio_str = request.form.get('turno_inicio', '').strip()
        turno_termino_str = request.form.get('turno_termino', '').strip()
        turno_existente = rota.turnos.filter_by(ativo=True).first()

        if turno_inicio_str and turno_termino_str:
            try:
                horario_inicio = datetime.strptime(turno_inicio_str, '%H:%M').time()
                horario_termino = datetime.strptime(turno_termino_str, '%H:%M').time()
                turno_nome = request.form.get('turno_nome', '').strip()
                turno_tempo = request.form.get('turno_tempo', type=int)

                if turno_existente:
                    # Atualizar turno existente
                    turno_existente.nome = turno_nome
                    turno_existente.horario_inicio = horario_inicio
                    turno_existente.horario_termino = horario_termino
                    turno_existente.tempo_trajeto_minutos = turno_tempo
                else:
                    # Criar novo turno
                    turno = RotaTurno(
                        rota_id=rota.id,
                        nome=turno_nome,
                        horario_inicio=horario_inicio,
                        horario_termino=horario_termino,
                        tempo_trajeto_minutos=turno_tempo
                    )
                    db.session.add(turno)
            except ValueError:
                pass

        # Registrar alterações gerais
        if alteracoes:
            registrar_historico_rota(
                rota.id,
                current_user.id,
                'editado',
                '; '.join(alteracoes)
            )

        db.session.commit()

        flash(f'Rota {rota.tag} atualizada!', 'success')
        return redirect(url_for('auditoria.visualizar_rota', id=rota.id))

    # Obter turno existente para o formulário
    turno = rota.turnos.filter_by(ativo=True).first()

    return render_template('auditoria/rotas/form.html',
                           rota=rota,
                           turno=turno,
                           clientes=clientes,
                           modais=modais,
                           turnos_padrao=turnos_padrao)


@auditoria_bp.route('/rotas/<int:id>/toggle', methods=['POST'])
@login_required
@auditoria_required
def toggle_rota(id):
    rota = Rota.query.get_or_404(id)
    rota.ativo = not rota.ativo

    registrar_historico_rota(
        rota.id,
        current_user.id,
        'editado',
        f'Rota {"ativada" if rota.ativo else "desativada"}'
    )

    db.session.commit()

    status = 'ativada' if rota.ativo else 'desativada'
    flash(f'Rota {rota.tag} {status}.', 'success')

    return redirect(url_for('auditoria.lista_rotas'))


# ============================================
# ROTAS DE TURNOS
# ============================================

@auditoria_bp.route('/rotas/<int:id>/turnos', methods=['POST'])
@login_required
@auditoria_required
def criar_turno(id):
    rota = Rota.query.get_or_404(id)

    nome = request.form.get('nome', '').strip()
    horario_inicio_str = request.form.get('horario_inicio', '')
    horario_termino_str = request.form.get('horario_termino', '')
    tempo_trajeto = request.form.get('tempo_trajeto_minutos', type=int)

    # Validações
    if not horario_inicio_str or not horario_termino_str:
        flash('Horários de início e término são obrigatórios.', 'danger')
        return redirect(url_for('auditoria.visualizar_rota', id=id))

    try:
        horario_inicio = datetime.strptime(horario_inicio_str, '%H:%M').time()
        horario_termino = datetime.strptime(horario_termino_str, '%H:%M').time()
    except ValueError:
        flash('Formato de horário inválido.', 'danger')
        return redirect(url_for('auditoria.visualizar_rota', id=id))

    turno = RotaTurno(
        rota_id=rota.id,
        nome=nome,
        horario_inicio=horario_inicio,
        horario_termino=horario_termino,
        tempo_trajeto_minutos=tempo_trajeto
    )
    db.session.add(turno)

    registrar_historico_rota(
        rota.id,
        current_user.id,
        'turno_adicionado',
        f'Turno adicionado: {horario_inicio_str} - {horario_termino_str}'
    )

    db.session.commit()

    flash('Turno adicionado com sucesso!', 'success')
    return redirect(url_for('auditoria.visualizar_rota', id=id))


@auditoria_bp.route('/turnos/<int:id>/editar', methods=['POST'])
@login_required
@auditoria_required
def editar_turno(id):
    turno = RotaTurno.query.get_or_404(id)

    nome = request.form.get('nome', '').strip()
    horario_inicio_str = request.form.get('horario_inicio', '')
    horario_termino_str = request.form.get('horario_termino', '')
    tempo_trajeto = request.form.get('tempo_trajeto_minutos', type=int)

    try:
        horario_inicio = datetime.strptime(horario_inicio_str, '%H:%M').time()
        horario_termino = datetime.strptime(horario_termino_str, '%H:%M').time()
    except ValueError:
        flash('Formato de horário inválido.', 'danger')
        return redirect(url_for('auditoria.visualizar_rota', id=turno.rota_id))

    # Registrar alteração
    registrar_historico_rota(
        turno.rota_id,
        current_user.id,
        'turno_alterado',
        f'Turno alterado: {turno.horario_inicio.strftime("%H:%M")} → {horario_inicio_str}',
        f'{turno.horario_inicio.strftime("%H:%M")} - {turno.horario_termino.strftime("%H:%M")}',
        f'{horario_inicio_str} - {horario_termino_str}'
    )

    turno.nome = nome
    turno.horario_inicio = horario_inicio
    turno.horario_termino = horario_termino
    turno.tempo_trajeto_minutos = tempo_trajeto

    db.session.commit()

    flash('Turno atualizado!', 'success')
    return redirect(url_for('auditoria.visualizar_rota', id=turno.rota_id))


@auditoria_bp.route('/turnos/<int:id>/excluir', methods=['POST'])
@login_required
@auditoria_required
def excluir_turno(id):
    turno = RotaTurno.query.get_or_404(id)
    rota_id = turno.rota_id

    registrar_historico_rota(
        rota_id,
        current_user.id,
        'turno_removido',
        f'Turno removido: {turno.horario_inicio.strftime("%H:%M")} - {turno.horario_termino.strftime("%H:%M")}'
    )

    turno.ativo = False
    db.session.commit()

    flash('Turno removido!', 'success')
    return redirect(url_for('auditoria.visualizar_rota', id=rota_id))


# ============================================
# PROCESSO DE AUDITORIA
# ============================================

@auditoria_bp.route('/auditar')
@login_required
@auditoria_required
def selecionar_rota_auditoria():
    rotas = Rota.query.filter_by(ativo=True).order_by(Rota.tag).all()
    return render_template('auditoria/auditar/selecionar.html', rotas=rotas)


@auditoria_bp.route('/auditar/<int:rota_id>', methods=['GET', 'POST'])
@login_required
@auditoria_required
def processar_auditoria(rota_id):
    rota = Rota.query.get_or_404(rota_id)

    if request.method == 'POST':
        observacoes = request.form.get('observacoes', '').strip()
        data_auditoria_str = request.form.get('data_auditoria', '')

        # Processar data
        data_auditoria = None
        if data_auditoria_str:
            try:
                data_auditoria = datetime.strptime(data_auditoria_str, '%Y-%m-%d').date()
            except ValueError:
                from models import agora_brasil
                data_auditoria = agora_brasil().date()
        else:
            from models import agora_brasil
            data_auditoria = agora_brasil().date()

        # Upload KML executado (obrigatório)
        if 'arquivo_kml' not in request.files or not request.files['arquivo_kml'].filename:
            flash('Arquivo KML é obrigatório para a auditoria.', 'danger')
            return render_template('auditoria/auditar/form.html', rota=rota)

        file = request.files['arquivo_kml']
        if not allowed_kml_file(file.filename):
            flash('Arquivo deve ser KML ou KMZ.', 'danger')
            return render_template('auditoria/auditar/form.html', rota=rota)

        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(filepath)

        # Processar comparação KML
        metricas = {}
        try:
            from kml_utils import comparar_kml
            kml_planejado_path = None
            if rota.arquivo_kml:
                kml_planejado_path = os.path.join(current_app.config['UPLOAD_FOLDER'], rota.arquivo_kml)
            metricas = comparar_kml(kml_planejado_path, filepath)
        except Exception as e:
            current_app.logger.error(f'Erro ao processar KML: {e}')
            metricas = {}

        # Criar auditoria
        auditoria = Auditoria(
            rota_id=rota.id,
            arquivo_kml=unique_filename,
            arquivo_kml_nome=filename,
            atendente_id=current_user.id,
            data_auditoria=data_auditoria,
            observacoes=observacoes,
            km_percorrido=metricas.get('km_percorrido'),
            km_planejado=metricas.get('km_planejado'),
            desvio_maximo_metros=metricas.get('desvio_maximo_metros'),
            aderencia_percentual=metricas.get('aderencia_percentual'),
            pontos_fora_rota=metricas.get('pontos_fora_rota')
        )
        db.session.add(auditoria)

        # Registrar no histórico da rota
        registrar_historico_rota(
            rota.id,
            current_user.id,
            'auditoria_realizada',
            f'Auditoria realizada em {data_auditoria.strftime("%d/%m/%Y")} por {current_user.nome}'
        )

        db.session.commit()

        flash('Auditoria registrada com sucesso!', 'success')
        return redirect(url_for('auditoria.visualizar_rota', id=rota.id))

    return render_template('auditoria/auditar/form.html', rota=rota)


@auditoria_bp.route('/auditorias')
@login_required
@auditoria_required
def lista_auditorias():
    page = request.args.get('page', 1, type=int)
    per_page = 20

    query = Auditoria.query

    # Filtros
    rota_id = request.args.get('rota_id', type=int)
    cliente_id = request.args.get('cliente_id', type=int)
    data_inicio = request.args.get('data_inicio', '')
    data_fim = request.args.get('data_fim', '')

    if rota_id:
        query = query.filter(Auditoria.rota_id == rota_id)
    if cliente_id:
        query = query.join(Rota).filter(Rota.cliente_id == cliente_id)
    if data_inicio:
        try:
            dt_inicio = datetime.strptime(data_inicio, '%Y-%m-%d').date()
            query = query.filter(Auditoria.data_auditoria >= dt_inicio)
        except ValueError:
            pass
    if data_fim:
        try:
            dt_fim = datetime.strptime(data_fim, '%Y-%m-%d').date()
            query = query.filter(Auditoria.data_auditoria <= dt_fim)
        except ValueError:
            pass

    auditorias = query.order_by(Auditoria.criado_em.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    rotas = Rota.query.filter_by(ativo=True).order_by(Rota.tag).all()
    clientes = get_clientes()

    return render_template('auditoria/auditar/lista.html',
                           auditorias=auditorias,
                           rotas=rotas,
                           clientes=clientes)


# ============================================
# RELATÓRIOS
# ============================================

@auditoria_bp.route('/relatorios')
@login_required
@auditoria_required
def relatorios():
    clientes = get_clientes()
    rotas = Rota.query.filter_by(ativo=True).order_by(Rota.tag).all()

    # Filtros
    cliente_id = request.args.get('cliente_id', type=int)
    data_inicio = request.args.get('data_inicio', '')
    data_fim = request.args.get('data_fim', '')

    query = Auditoria.query

    if cliente_id:
        query = query.join(Rota).filter(Rota.cliente_id == cliente_id)
    if data_inicio:
        try:
            dt_inicio = datetime.strptime(data_inicio, '%Y-%m-%d').date()
            query = query.filter(Auditoria.data_auditoria >= dt_inicio)
        except ValueError:
            pass
    if data_fim:
        try:
            dt_fim = datetime.strptime(data_fim, '%Y-%m-%d').date()
            query = query.filter(Auditoria.data_auditoria <= dt_fim)
        except ValueError:
            pass

    auditorias = query.order_by(Auditoria.data_auditoria.desc()).all()

    return render_template('auditoria/relatorios/index.html',
                           auditorias=auditorias,
                           clientes=clientes,
                           rotas=rotas)


@auditoria_bp.route('/relatorios/exportar', methods=['POST'])
@login_required
@auditoria_required
def exportar_relatorio():
    import csv
    from io import StringIO
    from flask import Response

    cliente_id = request.form.get('cliente_id', type=int)
    data_inicio = request.form.get('data_inicio', '')
    data_fim = request.form.get('data_fim', '')

    query = Auditoria.query

    if cliente_id:
        query = query.join(Rota).filter(Rota.cliente_id == cliente_id)
    if data_inicio:
        try:
            dt_inicio = datetime.strptime(data_inicio, '%Y-%m-%d').date()
            query = query.filter(Auditoria.data_auditoria >= dt_inicio)
        except ValueError:
            pass
    if data_fim:
        try:
            dt_fim = datetime.strptime(data_fim, '%Y-%m-%d').date()
            query = query.filter(Auditoria.data_auditoria <= dt_fim)
        except ValueError:
            pass

    auditorias = query.order_by(Auditoria.data_auditoria.desc()).all()

    # Gerar CSV
    output = StringIO()
    writer = csv.writer(output, delimiter=';')

    # Cabeçalho
    writer.writerow([
        'Data', 'Rota', 'Cliente', 'Atendente',
        'KM Planejado', 'KM Percorrido', 'Desvio Máx (m)',
        'Aderência %', 'Pontos Fora', 'Observações'
    ])

    for a in auditorias:
        writer.writerow([
            a.data_auditoria.strftime('%d/%m/%Y') if a.data_auditoria else '',
            a.rota.tag,
            a.rota.cliente.nome,
            a.atendente.nome,
            a.km_planejado or '',
            a.km_percorrido or '',
            a.desvio_maximo_metros or '',
            a.aderencia_percentual or '',
            a.pontos_fora_rota or '',
            a.observacoes or ''
        ])

    output.seek(0)

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition': 'attachment; filename=relatorio_auditorias.csv'
        }
    )


# ============================================
# DOWNLOAD DE ARQUIVOS
# ============================================

@auditoria_bp.route('/download/kml/<tipo>/<int:id>')
@login_required
@auditoria_required
def download_kml(tipo, id):
    if tipo == 'rota':
        rota = Rota.query.get_or_404(id)
        if not rota.arquivo_kml:
            flash('Rota não possui arquivo KML.', 'danger')
            return redirect(url_for('auditoria.visualizar_rota', id=id))
        return send_from_directory(
            current_app.config['UPLOAD_FOLDER'],
            rota.arquivo_kml,
            download_name=rota.arquivo_kml_nome
        )
    elif tipo == 'auditoria':
        auditoria = Auditoria.query.get_or_404(id)
        return send_from_directory(
            current_app.config['UPLOAD_FOLDER'],
            auditoria.arquivo_kml,
            download_name=auditoria.arquivo_kml_nome
        )
    else:
        flash('Tipo de download inválido.', 'danger')
        return redirect(url_for('auditoria.lista_rotas'))
