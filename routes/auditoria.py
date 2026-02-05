from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, send_from_directory
from flask_login import login_required, current_user
from functools import wraps
from werkzeug.utils import secure_filename
from datetime import datetime
import uuid
import os

from models import (db, User, Category, Modal, Rota, RotaTurno, RotaHistory,
                    Auditoria, Cliente, TurnoPadrao, CombustivelAnalise,
                    CombustivelRegistro, CombustivelMediaPadrao)

auditoria_bp = Blueprint('auditoria', __name__, url_prefix='/auditoria')


def auditoria_required(f):
    """Decorator que verifica se o usuário tem acesso ao módulo de auditoria"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.is_admin():
            return f(*args, **kwargs)
        categoria_auditoria = Category.query.filter_by(nome='Auditoria').first()
        if not categoria_auditoria:
            flash('Módulo de auditoria não configurado.', 'danger')
            return redirect(url_for('index'))
        if categoria_auditoria not in current_user.categorias.all():
            flash('Acesso restrito ao módulo de auditoria.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def combustivel_required(f):
    """Decorator que verifica se o usuário tem acesso ao módulo de combustível"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.is_admin():
            return f(*args, **kwargs)
        categoria = Category.query.filter_by(nome='Análise de Combustível').first()
        if not categoria:
            flash('Módulo de combustível não configurado.', 'danger')
            return redirect(url_for('index'))
        if categoria not in current_user.categorias.all():
            flash('Acesso restrito ao módulo de combustível.', 'danger')
            return redirect(url_for('index'))
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

    clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
    modais = Modal.query.filter_by(ativo=True).order_by(Modal.nome).all()

    return render_template('auditoria/rotas/list.html',
                           rotas=rotas,
                           clientes=clientes,
                           modais=modais)


@auditoria_bp.route('/rotas/criar', methods=['GET', 'POST'])
@login_required
@auditoria_required
def criar_rota():
    clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
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

                # Calcular KM do arquivo KML
                try:
                    from kml_utils import analisar_kml
                    analise = analisar_kml(filepath)
                    if analise['km']:
                        rota.km_atual = analise['km']
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

                # Calcular tempo de trajeto: hora fim - hora início
                minutos_inicio = horario_inicio.hour * 60 + horario_inicio.minute
                minutos_termino = horario_termino.hour * 60 + horario_termino.minute
                turno_tempo = minutos_termino - minutos_inicio
                if turno_tempo < 0:
                    turno_tempo += 1440  # cruza meia-noite

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
    clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
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

                # Calcular KM do arquivo KML
                try:
                    from kml_utils import analisar_kml
                    analise = analisar_kml(filepath)
                    if analise['km']:
                        rota.km_atual = analise['km']
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

                # Calcular tempo de trajeto: hora fim - hora início
                minutos_inicio = horario_inicio.hour * 60 + horario_inicio.minute
                minutos_termino = horario_termino.hour * 60 + horario_termino.minute
                turno_tempo = minutos_termino - minutos_inicio
                if turno_tempo < 0:
                    turno_tempo += 1440  # cruza meia-noite

                if turno_existente:
                    turno_existente.nome = turno_nome
                    turno_existente.horario_inicio = horario_inicio
                    turno_existente.horario_termino = horario_termino
                    turno_existente.tempo_trajeto_minutos = turno_tempo
                else:
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
    clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()

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
    clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
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


# ============================================
# AUDITORIA DE COMBUSTÍVEL
# ============================================

@auditoria_bp.route('/combustivel')
@login_required
@combustivel_required
def combustivel():
    page = request.args.get('page', 1, type=int)
    per_page = 20

    analises = CombustivelAnalise.query.order_by(
        CombustivelAnalise.criado_em.desc()
    ).paginate(page=page, per_page=per_page, error_out=False)

    return render_template('auditoria/combustivel/index.html', analises=analises)


@auditoria_bp.route('/combustivel/upload', methods=['POST'])
@login_required
@combustivel_required
def combustivel_upload():
    if 'arquivo' not in request.files or not request.files['arquivo'].filename:
        flash('Selecione um arquivo TXT para importar.', 'danger')
        return redirect(url_for('auditoria.combustivel'))

    file = request.files['arquivo']
    if not file.filename.lower().endswith('.txt'):
        flash('O arquivo deve ser do tipo TXT.', 'danger')
        return redirect(url_for('auditoria.combustivel'))

    # Salvar arquivo temporariamente
    from werkzeug.utils import secure_filename as sf
    filename = sf(file.filename)
    unique_filename = f"{uuid.uuid4().hex}_{filename}"
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
    file.save(filepath)

    try:
        from combustivel_utils import parse_arquivo_combustivel, analisar_combustivel

        # Parse do arquivo
        dados = parse_arquivo_combustivel(filepath)
        registros = dados['registros']

        if not registros:
            flash('Nenhum registro encontrado no arquivo. Verifique o formato.', 'danger')
            os.remove(filepath)
            return redirect(url_for('auditoria.combustivel'))

        # Analisar
        analise_resultado = analisar_combustivel(registros)
        resumo = analise_resultado['resumo']

        # Criar registro de análise
        analise = CombustivelAnalise(
            nome_arquivo=file.filename,
            empresa=dados['empresa'],
            periodo_inicio=dados['periodo_inicio'],
            periodo_fim=dados['periodo_fim'],
            total_litros=resumo['total_litros'],
            total_km=resumo['total_km'],
            media_kml=resumo['media_kml'],
            total_registros=resumo['total_registros'],
            total_veiculos=resumo['total_veiculos'],
            total_alertas=resumo['total_alertas'],
            usuario_id=current_user.id
        )
        db.session.add(analise)
        db.session.flush()

        # Mapear índices com alertas
        alertas_por_indice = {}
        for alerta in analise_resultado['alertas']:
            idx = alerta['indice']
            tipos = '; '.join(p['tipo'] for p in alerta['problemas'])
            descricoes = ' | '.join(p['descricao'] for p in alerta['problemas'])
            alertas_por_indice[idx] = (tipos, descricoes)

        # Salvar registros
        for idx, r in enumerate(registros):
            tem_alerta = idx in alertas_por_indice
            tipo_alerta, desc_alerta = alertas_por_indice.get(idx, ('', ''))

            reg = CombustivelRegistro(
                analise_id=analise.id,
                prefixo=r['prefixo'],
                data=r['data'],
                hora=r['hora'],
                tanque=r['tanque'],
                bomba=r['bomba'],
                litros=r['litros'],
                hodometro_inicio=r['hodometro_inicio'],
                hodometro_fim=r['hodometro_fim'],
                km=r['km'],
                km_acumulado=r['km_acumulado'],
                kml=r['kml'],
                modelo=r['modelo'],
                garagem=r['garagem'],
                flag=r['flag'],
                alerta=tem_alerta,
                tipo_alerta=tipo_alerta if tem_alerta else None,
                descricao_alerta=desc_alerta if tem_alerta else None,
            )
            db.session.add(reg)

        db.session.commit()

        # Remover arquivo temporário
        os.remove(filepath)

        flash(
            f'Arquivo importado! {resumo["total_registros"]} registros, '
            f'{resumo["total_alertas"]} alertas encontrados.',
            'success'
        )
        return redirect(url_for('auditoria.combustivel_analise', id=analise.id))

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Erro ao processar arquivo de combustível: {e}')
        if os.path.exists(filepath):
            os.remove(filepath)
        flash(f'Erro ao processar o arquivo: {str(e)}', 'danger')
        return redirect(url_for('auditoria.combustivel'))


@auditoria_bp.route('/combustivel/<int:id>')
@login_required
@combustivel_required
def combustivel_analise(id):
    analise = CombustivelAnalise.query.get_or_404(id)

    # Buscar registros
    registros = analise.registros.order_by(
        CombustivelRegistro.modelo,
        CombustivelRegistro.prefixo,
        CombustivelRegistro.data
    ).all()

    # Buscar apenas alertas
    alertas = analise.registros.filter_by(alerta=True).order_by(
        CombustivelRegistro.modelo,
        CombustivelRegistro.prefixo,
        CombustivelRegistro.data
    ).all()

    # Estatísticas por modelo (recalcular a partir dos registros salvos)
    modelos_stats = {}
    for r in registros:
        modelo = r.modelo or 'DESCONHECIDO'
        if modelo not in modelos_stats:
            modelos_stats[modelo] = {
                'litros': 0, 'km': 0, 'registros': 0,
                'veiculos': set(), 'kml_list': [], 'alertas': 0
            }
        s = modelos_stats[modelo]
        s['litros'] += r.litros or 0
        if r.km and r.km > 0:
            s['km'] += r.km
            if r.kml and r.kml > 0:
                s['kml_list'].append(r.kml)
        s['registros'] += 1
        s['veiculos'].add(r.prefixo)
        if r.alerta:
            s['alertas'] += 1

    # Buscar médias padrão cadastradas
    medias_padrao = {
        mp.modelo: mp for mp in CombustivelMediaPadrao.query.filter_by(ativo=True).all()
    }

    # Formatar para template
    modelos = {}
    for modelo, s in sorted(modelos_stats.items()):
        kml_list = s['kml_list']
        mp = medias_padrao.get(modelo)
        modelos[modelo] = {
            'media_kml': round(sum(kml_list) / len(kml_list), 2) if kml_list else 0,
            'mediana_kml': round(sorted(kml_list)[len(kml_list) // 2], 2) if kml_list else 0,
            'min_kml': round(min(kml_list), 2) if kml_list else 0,
            'max_kml': round(max(kml_list), 2) if kml_list else 0,
            'total_litros': round(s['litros'], 2),
            'total_km': round(s['km'], 2),
            'total_registros': s['registros'],
            'total_veiculos': len(s['veiculos']),
            'total_alertas': s['alertas'],
            'kml_geral': round(s['km'] / s['litros'], 2) if s['litros'] > 0 else 0,
            'ref_kml': mp.media_kml_referencia if mp else None,
            'ref_min': mp.kml_minimo_aceitavel if mp else None,
            'ref_max': mp.kml_maximo_aceitavel if mp else None,
            'categoria': mp.categoria if mp else None,
        }

    # Filtro de visualização
    filtro = request.args.get('filtro', 'alertas')  # alertas, todos
    modelo_filtro = request.args.get('modelo', '')
    prefixo_filtro = request.args.get('prefixo', '')

    registros_exibir = alertas if filtro == 'alertas' else registros

    if modelo_filtro:
        registros_exibir = [r for r in registros_exibir if r.modelo == modelo_filtro]
    if prefixo_filtro:
        registros_exibir = [r for r in registros_exibir if r.prefixo == prefixo_filtro]

    # Prefixos únicos para filtro
    prefixos = sorted(set(r.prefixo for r in registros))

    return render_template('auditoria/combustivel/analise.html',
                           analise=analise,
                           registros=registros_exibir,
                           alertas=alertas,
                           modelos=modelos,
                           filtro=filtro,
                           modelo_filtro=modelo_filtro,
                           prefixo_filtro=prefixo_filtro,
                           prefixos=prefixos,
                           total_registros=len(registros))


@auditoria_bp.route('/combustivel/<int:id>/excluir', methods=['POST'])
@login_required
@combustivel_required
def combustivel_excluir(id):
    analise = CombustivelAnalise.query.get_or_404(id)
    nome = analise.nome_arquivo

    db.session.delete(analise)
    db.session.commit()

    flash(f'Análise "{nome}" excluída.', 'success')
    return redirect(url_for('auditoria.combustivel'))


@auditoria_bp.route('/combustivel/<int:id>/exportar', methods=['POST'])
@login_required
@combustivel_required
def combustivel_exportar(id):
    import csv
    from io import StringIO
    from flask import Response

    analise = CombustivelAnalise.query.get_or_404(id)

    apenas_alertas = request.form.get('apenas_alertas', '0') == '1'

    query = analise.registros
    if apenas_alertas:
        query = query.filter_by(alerta=True)

    registros = query.order_by(
        CombustivelRegistro.modelo,
        CombustivelRegistro.prefixo,
        CombustivelRegistro.data
    ).all()

    output = StringIO()
    writer = csv.writer(output, delimiter=';')

    writer.writerow([
        'Prefixo', 'Data', 'Hora', 'Modelo', 'Litros',
        'Hod. Inicial', 'Hod. Final', 'Km', 'Km/L',
        'Flag', 'Alerta', 'Tipo Alerta', 'Descrição'
    ])

    for r in registros:
        writer.writerow([
            r.prefixo,
            r.data.strftime('%d/%m/%Y') if r.data else '',
            r.hora or '',
            r.modelo or '',
            f'{r.litros:.2f}'.replace('.', ',') if r.litros else '',
            f'{r.hodometro_inicio:.0f}' if r.hodometro_inicio else '',
            f'{r.hodometro_fim:.0f}' if r.hodometro_fim else '',
            f'{r.km:.2f}'.replace('.', ',') if r.km else '',
            f'{r.kml:.2f}'.replace('.', ',') if r.kml else '',
            r.flag or '',
            'Sim' if r.alerta else '',
            r.tipo_alerta or '',
            r.descricao_alerta or '',
        ])

    output.seek(0)

    sufixo = '_alertas' if apenas_alertas else ''
    nome_export = f'combustivel_{analise.id}{sufixo}.csv'

    return Response(
        '\ufeff' + output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename={nome_export}'}
    )


# ============================================
# MÉDIAS PADRÃO DE COMBUSTÍVEL
# ============================================

@auditoria_bp.route('/combustivel/medias-padrao')
@login_required
@combustivel_required
def combustivel_medias_padrao():
    medias = CombustivelMediaPadrao.query.order_by(CombustivelMediaPadrao.modelo).all()

    # Buscar modelos únicos já importados (para sugerir cadastro)
    modelos_importados = db.session.query(
        CombustivelRegistro.modelo
    ).distinct().all()
    modelos_importados = sorted(set(m[0] for m in modelos_importados if m[0]))

    # Modelos já cadastrados
    modelos_cadastrados = set(mp.modelo for mp in medias)
    modelos_pendentes = [m for m in modelos_importados if m not in modelos_cadastrados]

    return render_template('auditoria/combustivel/medias_padrao.html',
                           medias=medias,
                           modelos_pendentes=modelos_pendentes)


@auditoria_bp.route('/combustivel/medias-padrao/salvar', methods=['POST'])
@login_required
@combustivel_required
def combustivel_salvar_media_padrao():
    modelo = request.form.get('modelo', '').strip()
    categoria = request.form.get('categoria', '').strip()
    media_kml_ref = request.form.get('media_kml_referencia', type=float)
    kml_min = request.form.get('kml_minimo_aceitavel', type=float)
    kml_max = request.form.get('kml_maximo_aceitavel', type=float)
    observacoes = request.form.get('observacoes', '').strip()

    if not modelo or not media_kml_ref:
        flash('Modelo e Km/L de referência são obrigatórios.', 'danger')
        return redirect(url_for('auditoria.combustivel_medias_padrao'))

    # Verificar se já existe
    existente = CombustivelMediaPadrao.query.filter_by(modelo=modelo).first()
    if existente:
        existente.categoria = categoria
        existente.media_kml_referencia = media_kml_ref
        existente.kml_minimo_aceitavel = kml_min
        existente.kml_maximo_aceitavel = kml_max
        existente.observacoes = observacoes
        flash(f'Média padrão para {modelo} atualizada!', 'success')
    else:
        mp = CombustivelMediaPadrao(
            modelo=modelo,
            categoria=categoria,
            media_kml_referencia=media_kml_ref,
            kml_minimo_aceitavel=kml_min,
            kml_maximo_aceitavel=kml_max,
            observacoes=observacoes,
        )
        db.session.add(mp)
        flash(f'Média padrão para {modelo} cadastrada!', 'success')

    db.session.commit()
    return redirect(url_for('auditoria.combustivel_medias_padrao'))


@auditoria_bp.route('/combustivel/medias-padrao/<int:id>/editar', methods=['POST'])
@login_required
@combustivel_required
def combustivel_editar_media_padrao(id):
    mp = CombustivelMediaPadrao.query.get_or_404(id)

    mp.categoria = request.form.get('categoria', '').strip()
    media_kml_ref = request.form.get('media_kml_referencia', type=float)
    if not media_kml_ref:
        flash('Km/L de referência é obrigatório.', 'danger')
        return redirect(url_for('auditoria.combustivel_medias_padrao'))

    mp.media_kml_referencia = media_kml_ref
    mp.kml_minimo_aceitavel = request.form.get('kml_minimo_aceitavel', type=float)
    mp.kml_maximo_aceitavel = request.form.get('kml_maximo_aceitavel', type=float)
    mp.observacoes = request.form.get('observacoes', '').strip()

    db.session.commit()
    flash(f'Média padrão para {mp.modelo} atualizada!', 'success')
    return redirect(url_for('auditoria.combustivel_medias_padrao'))


@auditoria_bp.route('/combustivel/medias-padrao/<int:id>/toggle', methods=['POST'])
@login_required
@combustivel_required
def combustivel_toggle_media_padrao(id):
    mp = CombustivelMediaPadrao.query.get_or_404(id)
    mp.ativo = not mp.ativo
    db.session.commit()

    status = 'ativada' if mp.ativo else 'desativada'
    flash(f'Média padrão para {mp.modelo} {status}.', 'success')
    return redirect(url_for('auditoria.combustivel_medias_padrao'))


@auditoria_bp.route('/combustivel/reanalisar/<int:id>', methods=['POST'])
@login_required
@combustivel_required
def combustivel_reanalisar(id):
    """Reanalisa uma importação existente usando as médias padrão atuais"""
    analise = CombustivelAnalise.query.get_or_404(id)

    # Buscar médias padrão
    medias_padrao = {
        mp.modelo: mp for mp in CombustivelMediaPadrao.query.filter_by(ativo=True).all()
    }

    registros = analise.registros.order_by(
        CombustivelRegistro.modelo,
        CombustivelRegistro.prefixo,
        CombustivelRegistro.data
    ).all()

    # Recalcular medianas por modelo (dos próprios dados)
    por_modelo = {}
    for r in registros:
        modelo = r.modelo or 'DESCONHECIDO'
        if modelo not in por_modelo:
            por_modelo[modelo] = []
        if r.kml and r.kml > 0 and r.km and r.km > 0:
            por_modelo[modelo].append(r.kml)

    from statistics import median as calc_median
    medianas = {}
    for modelo, kml_list in por_modelo.items():
        medianas[modelo] = calc_median(kml_list) if kml_list else 0

    # Agrupar registros por prefixo para verificação de hodômetro
    por_prefixo = {}
    for r in registros:
        if r.prefixo not in por_prefixo:
            por_prefixo[r.prefixo] = []
        por_prefixo[r.prefixo].append(r)

    total_alertas = 0

    for r in registros:
        modelo = r.modelo or 'DESCONHECIDO'
        mp = medias_padrao.get(modelo)
        problemas = []

        # Determinar Km/L de referência: média padrão > mediana calculada
        if mp:
            ref_kml = mp.media_kml_referencia
            ref_min = mp.kml_minimo_aceitavel
            ref_max = mp.kml_maximo_aceitavel
        else:
            ref_kml = medianas.get(modelo, 0)
            ref_min = None
            ref_max = None

        # 1. Km zero ou negativo
        if r.km is not None and r.km <= 0:
            problemas.append(f"Km {r.km:.1f} (zero ou negativo)")

        # 2. Hodômetro decrescente
        if r.hodometro_fim and r.hodometro_inicio and r.hodometro_fim < r.hodometro_inicio:
            problemas.append(
                f"Hodômetro final ({r.hodometro_fim:.0f}) menor que "
                f"inicial ({r.hodometro_inicio:.0f})"
            )

        # 3. Verificar contra faixa aceitável (média padrão)
        if ref_min and ref_max and r.kml and r.kml > 0 and r.km and r.km > 0:
            if r.kml < ref_min:
                problemas.append(
                    f"Km/L {r.kml:.2f} abaixo do mínimo aceitável "
                    f"({ref_min:.2f}) para {modelo}"
                )
            elif r.kml > ref_max:
                problemas.append(
                    f"Km/L {r.kml:.2f} acima do máximo aceitável "
                    f"({ref_max:.2f}) para {modelo}"
                )
        elif ref_kml > 0 and r.kml and r.kml > 0 and r.km and r.km > 0:
            # Sem faixa definida, usar percentual da referência
            percentual = (r.kml / ref_kml) * 100
            fonte = "padrão" if mp else "mediana"
            if percentual < 60:
                problemas.append(
                    f"Km/L {r.kml:.2f} está {100-percentual:.0f}% abaixo "
                    f"da média {fonte} ({ref_kml:.2f})"
                )
            elif percentual < 75:
                problemas.append(
                    f"Km/L {r.kml:.2f} está {100-percentual:.0f}% abaixo "
                    f"da média {fonte} ({ref_kml:.2f})"
                )
            elif percentual > 200:
                problemas.append(
                    f"Km/L {r.kml:.2f} está {percentual-100:.0f}% acima "
                    f"da média {fonte} ({ref_kml:.2f})"
                )
            elif percentual > 150:
                problemas.append(
                    f"Km/L {r.kml:.2f} está {percentual-100:.0f}% acima "
                    f"da média {fonte} ({ref_kml:.2f})"
                )

        # 4. Hodômetro inconsistente com registro anterior
        pref_regs = por_prefixo.get(r.prefixo, [])
        pref_idx = next((i for i, pr in enumerate(pref_regs) if pr.id == r.id), -1)
        if pref_idx > 0:
            anterior = pref_regs[pref_idx - 1]
            if r.hodometro_inicio and anterior.hodometro_fim and r.hodometro_inicio < anterior.hodometro_fim:
                problemas.append(
                    f"Hodômetro inicial ({r.hodometro_inicio:.0f}) menor que "
                    f"o final do abast. anterior ({anterior.hodometro_fim:.0f})"
                )

        # 5. Flag PRAXIO (só se não há outro alerta)
        if r.flag == '*' and not problemas:
            problemas.append("Marcado pelo sistema PRAXIO (*)")

        # Atualizar registro
        if problemas:
            r.alerta = True
            r.tipo_alerta = 'REANALISADO'
            r.descricao_alerta = ' | '.join(problemas)
            total_alertas += 1
        else:
            r.alerta = False
            r.tipo_alerta = None
            r.descricao_alerta = None

    # Atualizar totais da análise
    analise.total_alertas = total_alertas
    db.session.commit()

    flash(
        f'Reanálise concluída com médias padrão! {total_alertas} alertas encontrados.',
        'success'
    )
    return redirect(url_for('auditoria.combustivel_analise', id=analise.id))
