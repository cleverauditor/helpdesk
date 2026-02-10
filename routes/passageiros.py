import os
import uuid
from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, jsonify, current_app)
from flask_login import login_required, current_user
from functools import wraps
from werkzeug.utils import secure_filename
from models import db, Cliente, ClienteTurno, PassageiroBase, Roteirizacao
import roteirizador_utils as rutils

passageiros_bp = Blueprint('passageiros', __name__, url_prefix='/passageiros')


def roteirizador_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin():
            cat = current_user.categorias.filter_by(nome='Roteirizador').first()
            if not cat:
                flash('Acesso restrito.', 'danger')
                return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def allowed_import_file(filename):
    allowed = current_app.config.get('ALLOWED_IMPORT_EXTENSIONS', {'csv', 'xlsx', 'xls'})
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed


# ============================================
# LISTAGEM
# ============================================

@passageiros_bp.route('/')
@login_required
@roteirizador_required
def lista():
    page = request.args.get('page', 1, type=int)
    per_page = 20

    query = PassageiroBase.query

    # Filtros
    cliente_id = request.args.get('cliente_id', type=int)
    turno_id = request.args.get('turno_id', type=int)
    busca = request.args.get('busca', '').strip()
    vinculo = request.args.get('vinculo', '')
    geocode = request.args.get('geocode', '')

    if cliente_id:
        query = query.filter(PassageiroBase.cliente_id == cliente_id)
    if turno_id:
        query = query.filter(PassageiroBase.turno_id == turno_id)
    if busca:
        query = query.filter(
            db.or_(
                PassageiroBase.nome.ilike(f'%{busca}%'),
                PassageiroBase.endereco.ilike(f'%{busca}%'),
                PassageiroBase.bairro.ilike(f'%{busca}%'),
                PassageiroBase.cidade.ilike(f'%{busca}%')
            )
        )
    if vinculo == 'vinculado':
        query = query.filter(PassageiroBase.roteirizacao_vinculada_id.isnot(None))
    elif vinculo == 'disponivel':
        query = query.filter(PassageiroBase.roteirizacao_vinculada_id.is_(None))
    if geocode:
        query = query.filter(PassageiroBase.geocode_status == geocode)

    query = query.filter(PassageiroBase.ativo == True)
    passageiros = query.order_by(PassageiroBase.nome).paginate(page=page, per_page=per_page, error_out=False)

    clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()

    # Carregar turnos do cliente selecionado
    turnos = []
    if cliente_id:
        turnos = ClienteTurno.query.filter_by(cliente_id=cliente_id, ativo=True).order_by(
            ClienteTurno.horario_inicio).all()

    return render_template('passageiros/list.html',
                           passageiros=passageiros, clientes=clientes, turnos=turnos)


# ============================================
# CRIAR INDIVIDUAL
# ============================================

@passageiros_bp.route('/criar', methods=['GET', 'POST'])
@login_required
@roteirizador_required
def criar():
    if request.method == 'POST':
        cliente_id = request.form.get('cliente_id', type=int)
        turno_id = request.form.get('turno_id', type=int)
        nome = request.form.get('nome', '').strip()

        if not nome or not cliente_id or not turno_id:
            flash('Nome, cliente e turno são obrigatórios.', 'danger')
            clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
            return render_template('passageiros/form.html', passageiro=None, clientes=clientes)

        p = PassageiroBase(
            cliente_id=cliente_id,
            turno_id=turno_id,
            nome=nome,
            endereco=request.form.get('endereco', '').strip(),
            numero=request.form.get('numero', '').strip(),
            bairro=request.form.get('bairro', '').strip(),
            cidade=request.form.get('cidade', '').strip(),
            estado=request.form.get('estado', '').strip().upper(),
            cep=request.form.get('cep', '').strip(),
            complemento=request.form.get('complemento', '').strip(),
            telefone=request.form.get('telefone', '').strip(),
            observacoes=request.form.get('observacoes', '').strip(),
        )
        db.session.add(p)
        db.session.flush()

        # Geocodificar automaticamente
        endereco_completo = p.endereco_completo()
        if endereco_completo:
            rutils.init_api_key(current_app.config['GOOGLE_MAPS_API_KEY'])
            geo = rutils.geocode_endereco(endereco_completo)
            if geo['status'] == 'sucesso':
                p.lat = geo['lat']
                p.lng = geo['lng']
                p.endereco_formatado = geo['endereco_formatado']
                p.geocode_status = 'sucesso'
            else:
                p.geocode_status = 'falha'

        db.session.commit()

        geo_msg = ''
        if p.geocode_status == 'sucesso':
            geo_msg = ' (geocodificado)'
        elif p.geocode_status == 'falha':
            geo_msg = ' (falha na geocodificação)'
        flash(f'Passageiro "{nome}" cadastrado{geo_msg}.', 'success' if p.geocode_status != 'falha' else 'warning')
        return redirect(url_for('passageiros.lista', cliente_id=cliente_id, turno_id=turno_id))

    clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
    return render_template('passageiros/form.html', passageiro=None, clientes=clientes)


# ============================================
# EDITAR
# ============================================

@passageiros_bp.route('/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@roteirizador_required
def editar(id):
    p = PassageiroBase.query.get_or_404(id)

    if request.method == 'POST':
        p.nome = request.form.get('nome', '').strip()
        p.cliente_id = request.form.get('cliente_id', type=int) or p.cliente_id
        p.turno_id = request.form.get('turno_id', type=int) or p.turno_id
        p.endereco = request.form.get('endereco', '').strip()
        p.numero = request.form.get('numero', '').strip()
        p.bairro = request.form.get('bairro', '').strip()
        p.cidade = request.form.get('cidade', '').strip()
        p.estado = request.form.get('estado', '').strip().upper()
        p.cep = request.form.get('cep', '').strip()
        p.complemento = request.form.get('complemento', '').strip()
        p.telefone = request.form.get('telefone', '').strip()
        p.observacoes = request.form.get('observacoes', '').strip()

        if not p.nome:
            flash('Nome é obrigatório.', 'danger')
            clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
            return render_template('passageiros/form.html', passageiro=p, clientes=clientes)

        # Se endereço mudou, re-geocodificar
        endereco_novo = p.endereco_completo()
        if endereco_novo and (not p.endereco_formatado or endereco_novo != p.endereco_formatado or p.geocode_status != 'sucesso'):
            rutils.init_api_key(current_app.config['GOOGLE_MAPS_API_KEY'])
            geo = rutils.geocode_endereco(endereco_novo)
            if geo['status'] == 'sucesso':
                p.lat = geo['lat']
                p.lng = geo['lng']
                p.endereco_formatado = geo['endereco_formatado']
                p.geocode_status = 'sucesso'
            else:
                p.geocode_status = 'falha'
                p.lat = None
                p.lng = None

        db.session.commit()
        flash(f'Passageiro "{p.nome}" atualizado.', 'success')
        return redirect(url_for('passageiros.lista', cliente_id=p.cliente_id, turno_id=p.turno_id))

    clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
    return render_template('passageiros/form.html', passageiro=p, clientes=clientes)


# ============================================
# TOGGLE ATIVO
# ============================================

@passageiros_bp.route('/<int:id>/toggle', methods=['POST'])
@login_required
@roteirizador_required
def toggle(id):
    p = PassageiroBase.query.get_or_404(id)
    p.ativo = not p.ativo
    db.session.commit()

    status = 'ativado' if p.ativo else 'desativado'
    flash(f'Passageiro "{p.nome}" {status}.', 'success')
    return redirect(url_for('passageiros.lista', cliente_id=p.cliente_id, turno_id=p.turno_id))


# ============================================
# IMPORTAR CSV/XLSX
# ============================================

@passageiros_bp.route('/importar', methods=['GET', 'POST'])
@login_required
@roteirizador_required
def importar():
    if request.method == 'POST':
        cliente_id = request.form.get('cliente_id', type=int)
        turno_id = request.form.get('turno_id', type=int)
        arquivo = request.files.get('arquivo')

        if not cliente_id or not turno_id:
            flash('Selecione cliente e turno.', 'danger')
            clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
            return render_template('passageiros/importar.html', clientes=clientes)

        if not arquivo or not arquivo.filename or not allowed_import_file(arquivo.filename):
            flash('Envie um arquivo CSV ou XLSX válido.', 'danger')
            clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
            return render_template('passageiros/importar.html', clientes=clientes)

        filename = secure_filename(arquivo.filename)
        saved_name = f"{uuid.uuid4().hex}_{filename}"
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], saved_name)
        arquivo.save(filepath)

        result = rutils.parse_arquivo_passageiros(filepath)

        if result['erros']:
            for erro in result['erros'][:5]:
                flash(f'Aviso: {erro}', 'warning')

        count = 0
        duplicados = 0
        for p_data in result['passageiros']:
            nome = p_data.get('nome', '').strip()
            endereco = p_data.get('endereco', '').strip()
            numero = p_data.get('numero', '').strip()

            # Verificar duplicidade por nome + endereço + numero + cliente + turno
            if nome:
                existente = PassageiroBase.query.filter(
                    PassageiroBase.cliente_id == cliente_id,
                    PassageiroBase.turno_id == turno_id,
                    db.func.lower(PassageiroBase.nome) == nome.lower(),
                    db.func.lower(db.func.coalesce(PassageiroBase.endereco, '')) == endereco.lower(),
                    db.func.coalesce(PassageiroBase.numero, '') == numero
                ).first()
                if existente:
                    duplicados += 1
                    continue

            p = PassageiroBase(
                cliente_id=cliente_id,
                turno_id=turno_id,
                nome=nome,
                endereco=endereco,
                numero=numero,
                bairro=p_data.get('bairro', ''),
                cidade=p_data.get('cidade', ''),
                estado=p_data.get('estado', ''),
                cep=p_data.get('cep', ''),
                complemento=p_data.get('complemento', ''),
                telefone=p_data.get('telefone', ''),
                observacoes=p_data.get('observacoes', ''),
            )
            db.session.add(p)
            count += 1

        db.session.commit()

        # Limpar arquivo temporário
        try:
            os.remove(filepath)
        except OSError:
            pass

        # Geocodificar os passageiros recém-importados
        geo_sucesso = 0
        geo_falha = 0
        if count > 0:
            rutils.init_api_key(current_app.config['GOOGLE_MAPS_API_KEY'])
            novos = PassageiroBase.query.filter_by(
                cliente_id=cliente_id, turno_id=turno_id,
                geocode_status='pendente', ativo=True
            ).all()
            for p in novos:
                endereco_completo = p.endereco_completo()
                if not endereco_completo:
                    p.geocode_status = 'falha'
                    geo_falha += 1
                    continue
                geo = rutils.geocode_endereco(endereco_completo)
                if geo['status'] == 'sucesso':
                    p.lat = geo['lat']
                    p.lng = geo['lng']
                    p.endereco_formatado = geo['endereco_formatado']
                    p.geocode_status = 'sucesso'
                    geo_sucesso += 1
                else:
                    p.geocode_status = 'falha'
                    geo_falha += 1
            db.session.commit()

        msg = f'{count} passageiros importados!'
        if duplicados:
            msg += f' ({duplicados} duplicados ignorados)'
        if geo_sucesso or geo_falha:
            msg += f' Geocodificação: {geo_sucesso} OK, {geo_falha} falha(s).'
        flash(msg, 'success' if geo_falha == 0 else 'warning')
        return redirect(url_for('passageiros.lista', cliente_id=cliente_id, turno_id=turno_id))

    clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
    return render_template('passageiros/importar.html', clientes=clientes)


# ============================================
# GEOCODIFICAR EM LOTE
# ============================================

@passageiros_bp.route('/geocodificar', methods=['POST'])
@login_required
@roteirizador_required
def geocodificar():
    cliente_id = request.form.get('cliente_id', type=int)
    turno_id = request.form.get('turno_id', type=int)

    query = PassageiroBase.query.filter(
        PassageiroBase.geocode_status.in_(['pendente', 'falha']),
        PassageiroBase.ativo == True
    )
    if cliente_id:
        query = query.filter(PassageiroBase.cliente_id == cliente_id)
    if turno_id:
        query = query.filter(PassageiroBase.turno_id == turno_id)

    pendentes = query.all()

    if not pendentes:
        flash('Nenhum passageiro pendente de geocodificação.', 'info')
        return redirect(url_for('passageiros.lista', cliente_id=cliente_id, turno_id=turno_id))

    rutils.init_api_key(current_app.config['GOOGLE_MAPS_API_KEY'])

    sucesso = 0
    falha = 0
    for p in pendentes:
        endereco = p.endereco_completo()
        if not endereco:
            p.geocode_status = 'falha'
            falha += 1
            continue

        geo = rutils.geocode_endereco(endereco)
        if geo['status'] == 'sucesso':
            p.lat = geo['lat']
            p.lng = geo['lng']
            p.endereco_formatado = geo['endereco_formatado']
            p.geocode_status = 'sucesso'
            sucesso += 1
        else:
            p.geocode_status = 'falha'
            falha += 1

    db.session.commit()

    flash(f'Geocodificação: {sucesso} sucesso, {falha} falha(s).', 'success' if falha == 0 else 'warning')
    return redirect(url_for('passageiros.lista', cliente_id=cliente_id, turno_id=turno_id))


# ============================================
# DESVINCULAR DE ROTA
# ============================================

@passageiros_bp.route('/<int:id>/desvincular', methods=['POST'])
@login_required
@roteirizador_required
def desvincular(id):
    from models import Passageiro, PontoParada, RoteiroPlanejado

    p = PassageiroBase.query.get_or_404(id)

    if not p.roteirizacao_vinculada_id:
        flash('Passageiro já está disponível.', 'info')
    else:
        rot_id = p.roteirizacao_vinculada_id

        # Desativar o Passageiro correspondente na roteirização
        passageiro_rot = Passageiro.query.filter_by(
            passageiro_base_id=p.id,
            roteirizacao_id=rot_id,
            ativo=True
        ).first()

        if passageiro_rot:
            passageiro_rot.ativo = False
            parada = PontoParada.query.get(passageiro_rot.parada_id) if passageiro_rot.parada_id else None
            if parada:
                parada.total_passageiros = max(0, (parada.total_passageiros or 1) - 1)
                # Se parada ficou vazia, desativar
                if parada.total_passageiros == 0:
                    parada.ativo = False
                # Atualizar total do roteiro
                if parada.roteiro_id:
                    roteiro = RoteiroPlanejado.query.get(parada.roteiro_id)
                    if roteiro:
                        roteiro.total_passageiros = sum(
                            pp.total_passageiros or 0 for pp in PontoParada.query.filter_by(
                                roteiro_id=roteiro.id, ativo=True
                            ).all()
                        )

        p.roteirizacao_vinculada_id = None
        db.session.commit()
        flash(f'Passageiro "{p.nome}" desvinculado da rota.', 'success')

    return redirect(url_for('passageiros.lista', cliente_id=p.cliente_id, turno_id=p.turno_id))


# ============================================
# APIs JSON (para formulários dinâmicos)
# ============================================

@passageiros_bp.route('/api/turnos')
@login_required
def api_turnos():
    cliente_id = request.args.get('cliente_id', type=int)
    if not cliente_id:
        return jsonify([])

    turnos = ClienteTurno.query.filter_by(
        cliente_id=cliente_id, ativo=True
    ).order_by(ClienteTurno.horario_inicio).all()

    return jsonify([{
        'id': t.id,
        'nome': t.nome,
        'horario': t.horario_formatado()
    } for t in turnos])


@passageiros_bp.route('/api/disponiveis')
@login_required
def api_disponiveis():
    cliente_id = request.args.get('cliente_id', type=int)
    turno_id = request.args.get('turno_id', type=int)

    if not cliente_id or not turno_id:
        return jsonify({'total': 0, 'passageiros': []})

    query = PassageiroBase.query.filter_by(
        cliente_id=cliente_id,
        turno_id=turno_id,
        roteirizacao_vinculada_id=None,
        ativo=True
    )

    total = query.count()
    passageiros = [{
        'id': p.id,
        'nome': p.nome,
        'geocode_status': p.geocode_status
    } for p in query.order_by(PassageiroBase.nome).limit(200).all()]

    return jsonify({'total': total, 'passageiros': passageiros})
