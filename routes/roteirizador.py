# ============================================
# ROTEIRIZADOR INTELIGENTE - ROTAS (Blueprint)
# ============================================

import os
import uuid
import csv
import io
import json
import threading
import time as _time
from functools import wraps
from datetime import datetime, time, timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, current_app, send_file, jsonify
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import (
    db, Category, Roteirizacao, Passageiro, PontoParada, RoteiroPlanejado,
    Cliente, TipoVeiculo, Simulacao, ClienteTurno, PassageiroBase
)
import roteirizador_utils as rutils

roteirizador_bp = Blueprint('roteirizador', __name__, url_prefix='/roteirizador')


# ============================================
# CONTROLE DE ACESSO
# ============================================

def roteirizador_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.is_admin():
            return f(*args, **kwargs)
        cat = Category.query.filter_by(nome='Roteirizador').first()
        if cat and cat in current_user.categorias.all():
            return f(*args, **kwargs)
        flash('Acesso restrito ao módulo de roteirização.', 'danger')
        return redirect(url_for('index'))
    return decorated


def allowed_import_file(filename):
    allowed = current_app.config.get('ALLOWED_IMPORT_EXTENSIONS', {'csv', 'xlsx', 'xls'})
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed


# ============================================
# PROGRESSO DE OPERAÇÕES LONGAS
# ============================================

def _atualizar_progresso(app, rot_id, dados):
    """Atualiza o campo progresso_json de uma roteirização."""
    with app.app_context():
        rot = db.session.get(Roteirizacao, rot_id)
        if rot:
            rot.progresso_json = json.dumps(dados, ensure_ascii=False)
            db.session.commit()


@roteirizador_bp.route('/<int:id>/progresso')
@roteirizador_required
def progresso(id):
    rot = Roteirizacao.query.get_or_404(id)
    if rot.progresso_json:
        data = json.loads(rot.progresso_json)
        if data.get('inicio'):
            elapsed = _time.time() - data['inicio']
            data['elapsed'] = round(elapsed)
            # Detecção de operação abandonada (servidor reiniciou)
            if data.get('status') == 'running' and elapsed > 300:
                data['status'] = 'error'
                data['erro'] = 'Operação expirou. Tente novamente.'
                rot.progresso_json = None
                db.session.commit()
        return jsonify(data)
    return jsonify({'status': 'idle'})


@roteirizador_bp.route('/<int:id>/progresso/limpar', methods=['POST'])
@roteirizador_required
def limpar_progresso(id):
    rot = Roteirizacao.query.get_or_404(id)
    rot.progresso_json = None
    db.session.commit()
    return jsonify({'ok': True})


# ============================================
# LISTA DE ROTEIRIZAÇÕES
# ============================================

@roteirizador_bp.route('/')
@roteirizador_required
def lista():
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')
    cliente_filter = request.args.get('cliente_id', '', type=str)
    busca = request.args.get('busca', '')

    query = Roteirizacao.query.filter_by(ativo=True)

    if status_filter:
        query = query.filter_by(status=status_filter)
    if cliente_filter:
        query = query.filter_by(cliente_id=int(cliente_filter))
    if busca:
        query = query.filter(Roteirizacao.nome.ilike(f'%{busca}%'))

    query = query.order_by(Roteirizacao.criado_em.desc())
    roteirizacoes = query.paginate(page=page, per_page=20, error_out=False)

    clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()

    return render_template('roteirizador/list.html',
                           roteirizacoes=roteirizacoes,
                           clientes=clientes,
                           status_filter=status_filter,
                           cliente_filter=cliente_filter,
                           busca=busca)


# ============================================
# CRIAR ROTEIRIZAÇÃO
# ============================================

@roteirizador_bp.route('/criar', methods=['GET', 'POST'])
@roteirizador_required
def criar():
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        destino = request.form.get('destino_endereco', '').strip()
        horario = request.form.get('horario_chegada', '07:00')
        dist_max = request.form.get('distancia_maxima_caminhada', 300, type=int)
        tempo_max = request.form.get('tempo_maximo_viagem', 90, type=int)
        capacidade = request.form.get('capacidade_veiculo', 44, type=int)
        cliente_id = request.form.get('cliente_id', type=int)
        turno_id = request.form.get('turno_id', type=int)
        modo_passageiros = request.form.get('modo_passageiros', 'arquivo')

        if not nome or not destino:
            flash('Preencha o nome e o endereço de destino.', 'danger')
            clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
            return render_template('roteirizador/form.html', clientes=clientes)

        # Parse horário
        try:
            h, m = horario.split(':')
            horario_time = time(int(h), int(m))
        except (ValueError, AttributeError):
            horario_time = time(7, 0)

        # Geocodificar destino
        rutils.init_api_key(current_app.config['GOOGLE_MAPS_API_KEY'])
        geo = rutils.geocode_endereco(destino)

        if geo['status'] != 'sucesso':
            flash('Não foi possível geocodificar o endereço de destino. Verifique e tente novamente.', 'danger')
            clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
            return render_template('roteirizador/form.html', clientes=clientes)

        # Criar roteirização
        rot = Roteirizacao(
            nome=nome,
            destino_endereco=destino,
            destino_lat=geo['lat'],
            destino_lng=geo['lng'],
            horario_chegada=horario_time,
            distancia_maxima_caminhada=dist_max,
            tempo_maximo_viagem=tempo_max,
            capacidade_veiculo=capacidade,
            cliente_id=cliente_id if cliente_id else None,
            turno_id=turno_id if turno_id else None,
            usuario_id=current_user.id
        )

        # MODO BASE: usar passageiros cadastrados
        if modo_passageiros == 'base' and cliente_id and turno_id:
            passageiros_base = PassageiroBase.query.filter_by(
                cliente_id=cliente_id,
                turno_id=turno_id,
                roteirizacao_vinculada_id=None,
                ativo=True
            ).all()

            if not passageiros_base:
                flash('Nenhum passageiro disponível para este cliente/turno.', 'warning')
                clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
                return render_template('roteirizador/form.html', clientes=clientes)

            db.session.add(rot)
            db.session.flush()

            count = 0
            for pb in passageiros_base:
                passageiro = Passageiro(
                    roteirizacao_id=rot.id,
                    passageiro_base_id=pb.id,
                    nome=pb.nome,
                    endereco=pb.endereco,
                    numero=pb.numero,
                    bairro=pb.bairro,
                    cidade=pb.cidade,
                    estado=pb.estado,
                    cep=pb.cep,
                    complemento=pb.complemento,
                    telefone=pb.telefone,
                    observacoes=pb.observacoes,
                    lat=pb.lat,
                    lng=pb.lng,
                    endereco_formatado=pb.endereco_formatado,
                    geocode_status=pb.geocode_status if pb.lat else 'pendente',
                )
                db.session.add(passageiro)
                count += 1

            rot.total_passageiros = count

            # Se todos os passageiros já estão geocodificados, avançar status
            todos_geo = all(p.geocode_status == 'sucesso' for p in rot.passageiros)
            if todos_geo and count > 0:
                rot.status = 'geocodificado'
                flash(f'Roteirização criada com {count} passageiros do cadastro (todos já geocodificados). Pronta para clusterizar!', 'success')
            else:
                flash(f'Roteirização criada com {count} passageiros do cadastro.', 'success')

            db.session.commit()
            return redirect(url_for('roteirizador.visualizar', id=rot.id))

        # MODO ARQUIVO: importar CSV/XLSX (fluxo original)
        arquivo = request.files.get('arquivo')
        if arquivo and arquivo.filename and allowed_import_file(arquivo.filename):
            filename = secure_filename(arquivo.filename)
            saved_name = f"{uuid.uuid4().hex}_{filename}"
            filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], saved_name)
            arquivo.save(filepath)

            rot.arquivo_importacao = saved_name
            rot.arquivo_importacao_nome = filename

            # Parse arquivo
            result = rutils.parse_arquivo_passageiros(filepath)

            if result['erros']:
                for erro in result['erros'][:5]:
                    flash(f'Aviso na importação: {erro}', 'warning')

            db.session.add(rot)
            db.session.flush()  # para ter o ID

            # Criar passageiros
            count = 0
            for p_data in result['passageiros']:
                passageiro = Passageiro(
                    roteirizacao_id=rot.id,
                    nome=p_data.get('nome', ''),
                    endereco=p_data.get('endereco', ''),
                    numero=p_data.get('numero', ''),
                    bairro=p_data.get('bairro', ''),
                    cidade=p_data.get('cidade', ''),
                    estado=p_data.get('estado', ''),
                    cep=p_data.get('cep', ''),
                    complemento=p_data.get('complemento', ''),
                    telefone=p_data.get('telefone', ''),
                    observacoes=p_data.get('observacoes', '')
                )
                db.session.add(passageiro)
                count += 1

            rot.total_passageiros = count
            db.session.commit()
            flash(f'Roteirização criada com {count} passageiros importados.', 'success')
            return redirect(url_for('roteirizador.visualizar', id=rot.id))

        else:
            db.session.add(rot)
            db.session.commit()
            flash('Roteirização criada. Importe um arquivo de passageiros.', 'info')
            return redirect(url_for('roteirizador.visualizar', id=rot.id))

    clientes = Cliente.query.filter_by(ativo=True).order_by(Cliente.nome).all()
    return render_template('roteirizador/form.html', clientes=clientes)


# ============================================
# VISUALIZAR ROTEIRIZAÇÃO (Dashboard)
# ============================================

@roteirizador_bp.route('/<int:id>')
@roteirizador_required
def visualizar(id):
    rot = Roteirizacao.query.get_or_404(id)

    passageiros = rot.passageiros.filter_by(ativo=True).all()
    paradas = rot.paradas.filter_by(ativo=True).order_by(PontoParada.roteiro_id, PontoParada.ordem).all()

    # Separar roteiros ida e volta
    all_roteiros = rot.roteiros.filter_by(ativo=True).order_by(RoteiroPlanejado.ordem).all()
    roteiros = [r for r in all_roteiros if r.tipo != 'volta']
    roteiros_volta = [r for r in all_roteiros if r.tipo == 'volta']

    # Paradas da volta (vinculadas a roteiros volta)
    volta_ids = {r.id for r in roteiros_volta}
    paradas_volta = [p for p in paradas if p.roteiro_id in volta_ids]
    paradas = [p for p in paradas if p.roteiro_id not in volta_ids]

    # Contagens de geocode
    total_geo = sum(1 for p in passageiros if p.geocode_status == 'sucesso')
    total_falha = sum(1 for p in passageiros if p.geocode_status == 'falha')
    total_pendente = sum(1 for p in passageiros if p.geocode_status == 'pendente')

    api_key = current_app.config['GOOGLE_MAPS_API_KEY']
    tipos_veiculo = TipoVeiculo.query.filter_by(ativo=True).order_by(TipoVeiculo.capacidade).all()
    simulacoes = rot.simulacoes.order_by(Simulacao.criado_em.desc()).all()

    # Rotas existentes (finalizadas) do mesmo cliente+turno
    rotas_existentes = []
    if rot.cliente_id and rot.turno_id:
        outras = Roteirizacao.query.filter(
            Roteirizacao.cliente_id == rot.cliente_id,
            Roteirizacao.turno_id == rot.turno_id,
            Roteirizacao.status == 'finalizado',
            Roteirizacao.ativo == True,
            Roteirizacao.id != rot.id
        ).all()
        for outra in outras:
            for r in outra.roteiros.filter_by(ativo=True, tipo='ida').all():
                if r.polyline_encoded:
                    rotas_existentes.append({
                        'nome': f'{outra.nome} - {r.nome}',
                        'polyline': r.polyline_encoded,
                        'roteirizacao_id': outra.id,
                    })

    return render_template('roteirizador/view.html',
                           rot=rot,
                           passageiros=passageiros,
                           paradas=paradas,
                           roteiros=roteiros,
                           roteiros_volta=roteiros_volta,
                           paradas_volta=paradas_volta,
                           total_geo=total_geo,
                           total_falha=total_falha,
                           total_pendente=total_pendente,
                           api_key=api_key,
                           tipos_veiculo=tipos_veiculo,
                           simulacoes=simulacoes,
                           rotas_existentes=rotas_existentes)


# ============================================
# LISTA DE PASSAGEIROS
# ============================================

@roteirizador_bp.route('/<int:id>/passageiros')
@roteirizador_required
def listar_passageiros(id):
    rot = Roteirizacao.query.get_or_404(id)
    passageiros = rot.passageiros.filter_by(ativo=True).order_by(Passageiro.nome).all()
    return render_template('roteirizador/passageiros.html', rot=rot, passageiros=passageiros)


# ============================================
# EDITAR PASSAGEIRO (correção manual)
# ============================================

@roteirizador_bp.route('/<int:id>/passageiro/<int:pid>/editar', methods=['POST'])
@roteirizador_required
def editar_passageiro(id, pid):
    passageiro = Passageiro.query.get_or_404(pid)
    if passageiro.roteirizacao_id != id:
        flash('Passageiro não pertence a esta roteirização.', 'danger')
        return redirect(url_for('roteirizador.visualizar', id=id))

    lat = request.form.get('lat', type=float)
    lng = request.form.get('lng', type=float)
    endereco = request.form.get('endereco', '').strip()

    if lat and lng:
        passageiro.lat = lat
        passageiro.lng = lng
        passageiro.geocode_status = 'manual'
        if endereco:
            passageiro.endereco = endereco
        db.session.commit()
        flash(f'Coordenadas de {passageiro.nome} atualizadas.', 'success')
    else:
        flash('Informe latitude e longitude válidas.', 'danger')

    return redirect(url_for('roteirizador.listar_passageiros', id=id))


# ============================================
# GEOCODIFICAR PASSAGEIROS
# ============================================

@roteirizador_bp.route('/<int:id>/geocodificar', methods=['POST'])
@roteirizador_required
def geocodificar(id):
    rot = Roteirizacao.query.get_or_404(id)
    rutils.init_api_key(current_app.config['GOOGLE_MAPS_API_KEY'])

    passageiros = rot.passageiros.filter(
        Passageiro.ativo == True,
        Passageiro.geocode_status.in_(['pendente', 'falha'])
    ).all()

    if not passageiros:
        if rot.status == 'rascunho':
            rot.status = 'geocodificado'
            db.session.commit()
        flash('Todos os passageiros já foram geocodificados.', 'info')
        return redirect(url_for('roteirizador.visualizar', id=id))

    # Preparar dados para geocodificação em lote
    dados = [{'id': p.id, 'endereco_completo': p.endereco_completo()} for p in passageiros]
    resultados = rutils.geocode_lote(dados, delay=0.1)

    sucesso = 0
    falha = 0
    for r in resultados:
        p = Passageiro.query.get(r['id'])
        if p:
            p.lat = r['lat']
            p.lng = r['lng']
            p.endereco_formatado = r['endereco_formatado']
            p.geocode_status = r['status']
            if r['status'] == 'sucesso':
                sucesso += 1
            else:
                falha += 1

    rot.status = 'geocodificado'
    db.session.commit()

    flash(f'Geocodificação concluída: {sucesso} sucesso, {falha} falhas.', 'success' if falha == 0 else 'warning')
    return redirect(url_for('roteirizador.visualizar', id=id))


# ============================================
# CLUSTERIZAR (agrupar em paradas)
# ============================================

@roteirizador_bp.route('/<int:id>/clusterizar', methods=['POST'])
@roteirizador_required
def clusterizar(id):
    rot = Roteirizacao.query.get_or_404(id)

    # Verificar se já tem operação em andamento
    if rot.progresso_json:
        prog = json.loads(rot.progresso_json)
        if prog.get('status') == 'running':
            return jsonify({'ok': False, 'msg': 'Operação já em andamento.'}), 409

    # Validar passageiros
    total_geo = rot.passageiros.filter(
        Passageiro.ativo == True,
        Passageiro.geocode_status.in_(['sucesso', 'manual']),
        Passageiro.lat.isnot(None)
    ).count()

    if not total_geo:
        return jsonify({'ok': False, 'msg': 'Nenhum passageiro geocodificado para agrupar.'}), 400

    # Gravar progresso inicial
    inicio = _time.time()
    rot.progresso_json = json.dumps({
        'operacao': 'clusterizar', 'status': 'running',
        'etapa': 'Iniciando clusterização...', 'percentual': 0, 'inicio': inicio
    }, ensure_ascii=False)
    db.session.commit()

    # Lançar thread em background
    app = current_app._get_current_object()
    api_key = current_app.config['GOOGLE_MAPS_API_KEY']
    thread = threading.Thread(target=_clusterizar_background, args=(app, id, api_key, inicio), daemon=True)
    thread.start()

    return jsonify({'ok': True, 'msg': 'Clusterização iniciada.'})


def _clusterizar_background(app, rot_id, api_key, inicio):
    """Executa clusterização em background com atualizações de progresso."""
    with app.app_context():
        try:
            rutils.init_api_key(api_key)
            rot = db.session.get(Roteirizacao, rot_id)

            # Etapa 1: Resetar
            _atualizar_progresso(app, rot_id, {
                'operacao': 'clusterizar', 'status': 'running',
                'etapa': 'Resetando atribuições anteriores...', 'percentual': 5, 'inicio': inicio
            })
            for p in rot.passageiros.filter_by(ativo=True).all():
                p.parada_id = None
                p.distancia_ate_parada = None
                p.tempo_no_veiculo = None
            db.session.flush()
            PontoParada.query.filter_by(roteirizacao_id=rot_id).delete()
            RoteiroPlanejado.query.filter_by(roteirizacao_id=rot_id).delete()

            # Etapa 2: Clusterizar
            _atualizar_progresso(app, rot_id, {
                'operacao': 'clusterizar', 'status': 'running',
                'etapa': 'Calculando rota-tronco e agrupando paradas...', 'percentual': 15, 'inicio': inicio
            })
            passageiros = rot.passageiros.filter(
                Passageiro.ativo == True,
                Passageiro.geocode_status.in_(['sucesso', 'manual']),
                Passageiro.lat.isnot(None)
            ).all()
            dados = [{'id': p.id, 'lat': p.lat, 'lng': p.lng} for p in passageiros]

            departure_ts = None
            if rot.horario_chegada:
                partida_estimada = datetime.combine(datetime.today(), rot.horario_chegada) - timedelta(minutes=rot.tempo_maximo_viagem or 90)
                departure_ts = rutils._prox_dia_util_timestamp(partida_estimada.time())

            clusters = rutils.clusterizar_passageiros(dados, rot.distancia_maxima_caminhada, rot.destino_lat, rot.destino_lng, departure_ts)

            # Etapa 3: Criar paradas com reverse geocode
            total_clusters = len(clusters)
            for i, cluster in enumerate(clusters, start=1):
                _atualizar_progresso(app, rot_id, {
                    'operacao': 'clusterizar', 'status': 'running',
                    'etapa': f'Geocodificando parada {i} de {total_clusters}...',
                    'percentual': 30 + int(60 * i / total_clusters), 'inicio': inicio
                })
                endereco_ref = rutils.reverse_geocode(cluster['centroid_lat'], cluster['centroid_lng'])
                parada = PontoParada(
                    roteirizacao_id=rot_id,
                    nome=f'Parada {i}',
                    endereco_referencia=endereco_ref,
                    lat=cluster['centroid_lat'],
                    lng=cluster['centroid_lng'],
                    total_passageiros=len(cluster['passageiro_ids'])
                )
                db.session.add(parada)
                db.session.flush()
                for pid in cluster['passageiro_ids']:
                    p = Passageiro.query.get(pid)
                    if p:
                        p.parada_id = parada.id
                        p.distancia_ate_parada = cluster['distancias'].get(pid, 0)

            # Etapa 4: Finalizar
            rot.total_paradas = total_clusters
            rot.status = 'clusterizado'
            rot.progresso_json = json.dumps({
                'operacao': 'clusterizar', 'status': 'completed',
                'etapa': 'Concluído!', 'percentual': 100, 'inicio': inicio,
                'resultado_flash': {'msg': f'{total_clusters} pontos de parada criados.', 'cat': 'success'}
            }, ensure_ascii=False)
            db.session.commit()

        except Exception as e:
            try:
                rot = db.session.get(Roteirizacao, rot_id)
                if rot:
                    rot.progresso_json = json.dumps({
                        'operacao': 'clusterizar', 'status': 'error',
                        'etapa': 'Erro na clusterização', 'percentual': 0, 'inicio': inicio,
                        'erro': str(e)
                    }, ensure_ascii=False)
                    db.session.commit()
            except Exception:
                pass


# ============================================
# OTIMIZAR ROTA
# ============================================

@roteirizador_bp.route('/<int:id>/otimizar', methods=['POST'])
@roteirizador_required
def otimizar(id):
    rot = Roteirizacao.query.get_or_404(id)

    # Verificar se já tem operação em andamento
    if rot.progresso_json:
        prog = json.loads(rot.progresso_json)
        if prog.get('status') == 'running':
            return jsonify({'ok': False, 'msg': 'Operação já em andamento.'}), 409

    paradas = rot.paradas.filter_by(ativo=True).all()
    if not paradas:
        return jsonify({'ok': False, 'msg': 'Nenhum ponto de parada. Execute a clusterização primeiro.'}), 400

    # Gravar progresso inicial
    inicio = _time.time()
    rot.progresso_json = json.dumps({
        'operacao': 'otimizar', 'status': 'running',
        'etapa': 'Iniciando otimização...', 'percentual': 0, 'inicio': inicio
    }, ensure_ascii=False)
    db.session.commit()

    # Lançar thread em background
    app = current_app._get_current_object()
    api_key = current_app.config['GOOGLE_MAPS_API_KEY']
    dwell_time = current_app.config.get('ROTEIRIZADOR_DWELL_TIME', 60)
    thread = threading.Thread(target=_otimizar_background, args=(app, id, api_key, dwell_time, inicio), daemon=True)
    thread.start()

    return jsonify({'ok': True, 'msg': 'Otimização iniciada.'})


def _otimizar_background(app, rot_id, api_key, dwell_time, inicio):
    """Executa otimização em background com atualizações de progresso."""
    with app.app_context():
        try:
            rutils.init_api_key(api_key)
            rot = db.session.get(Roteirizacao, rot_id)
            paradas = rot.paradas.filter_by(ativo=True).all()

            # Etapa 1: Limpar roteiros anteriores
            _atualizar_progresso(app, rot_id, {
                'operacao': 'otimizar', 'status': 'running',
                'etapa': 'Preparando dados...', 'percentual': 5, 'inicio': inicio
            })
            RoteiroPlanejado.query.filter_by(roteirizacao_id=rot_id).delete()
            for p in paradas:
                p.roteiro_id = None
                p.ordem = None
                p.horario_chegada = None
                p.horario_partida = None

            # Validar e preparar dados
            clusters_data = []
            for p in paradas:
                if p.lat and p.lng and -90 <= p.lat <= 90 and -180 <= p.lng <= 180:
                    clusters_data.append({
                        'id': p.id, 'lat': p.lat, 'lng': p.lng,
                        'centroid_lat': p.lat, 'centroid_lng': p.lng,
                        'passageiro_ids': [px.id for px in p.passageiros.filter_by(ativo=True).all()]
                    })

            # Etapa 2: Dividir por capacidade
            _atualizar_progresso(app, rot_id, {
                'operacao': 'otimizar', 'status': 'running',
                'etapa': 'Dividindo por capacidade do veículo...', 'percentual': 10, 'inicio': inicio
            })
            sub_rotas_capacidade = rutils.dividir_rotas_por_capacidade(clusters_data, rot.capacidade_veiculo)

            departure_ts = None
            if rot.horario_chegada:
                partida_estimada = datetime.combine(datetime.today(), rot.horario_chegada) - timedelta(minutes=rot.tempo_maximo_viagem or 90)
                departure_ts = rutils._prox_dia_util_timestamp(partida_estimada.time())

            # Etapa 3: Otimizar cada grupo
            start_time = _time.time()
            TIMEOUT_SECONDS = 240
            sub_rotas_finais = []
            timeout_hit = False
            total_grupos = len(sub_rotas_capacidade)

            for g_idx, grupo_clusters in enumerate(sub_rotas_capacidade, start=1):
                if _time.time() - start_time > TIMEOUT_SECONDS:
                    timeout_hit = True
                    break

                pct = 15 + int(55 * g_idx / total_grupos)
                _atualizar_progresso(app, rot_id, {
                    'operacao': 'otimizar', 'status': 'running',
                    'etapa': f'Otimizando grupo {g_idx} de {total_grupos}...',
                    'percentual': pct, 'inicio': inicio
                })

                paradas_opt = [{'id': c['id'], 'lat': c['lat'], 'lng': c['lng']} for c in grupo_clusters]
                resultado = rutils.otimizar_rota_google(paradas_opt, rot.destino_lat, rot.destino_lng, departure_ts)

                if not resultado or 'error' in resultado:
                    sub_rotas_finais.append((grupo_clusters, resultado))
                    continue

                sub_tempo = rutils.dividir_rotas_por_tempo(
                    grupo_clusters, resultado, rot.tempo_maximo_viagem,
                    rot.destino_lat, rot.destino_lng, departure_ts
                )
                sub_rotas_finais.extend(sub_tempo)

            # Etapa 4: Criar roteiros planejados
            _atualizar_progresso(app, rot_id, {
                'operacao': 'otimizar', 'status': 'running',
                'etapa': 'Criando roteiros planejados...', 'percentual': 80, 'inicio': inicio
            })

            total_dist = 0
            duracoes_rotas = []
            num_roteiros = 0
            ordem_global = 0

            for r_idx, (grupo_clusters, resultado) in enumerate(sub_rotas_finais, start=1):
                if not resultado or 'error' in resultado:
                    continue

                schedule = rutils.calcular_horarios(resultado['legs'], rot.horario_chegada, dwell_time)
                roteiro = RoteiroPlanejado(
                    roteirizacao_id=rot_id,
                    nome=f'Rota {r_idx}',
                    ordem=r_idx,
                    distancia_km=resultado['total_distance_km'],
                    duracao_minutos=resultado['total_duration_min'],
                    polyline_encoded=resultado['polyline'],
                    horario_chegada_destino=rot.horario_chegada,
                    capacidade_veiculo=rot.capacidade_veiculo
                )
                if schedule:
                    roteiro.horario_saida = schedule[0]['chegada']
                total_pax = sum(len(c.get('passageiro_ids', [])) for c in grupo_clusters)
                roteiro.total_passageiros = total_pax
                db.session.add(roteiro)
                db.session.flush()

                ordem_otimizada = resultado['waypoint_order']
                paradas_opt = [{'id': c['id'], 'lat': c['lat'], 'lng': c['lng']} for c in grupo_clusters]
                for seq_local, orig_idx in enumerate(ordem_otimizada):
                    if orig_idx < len(paradas_opt):
                        parada_id = paradas_opt[orig_idx]['id']
                        parada = PontoParada.query.get(parada_id)
                        if parada:
                            ordem_global += 1
                            parada.roteiro_id = roteiro.id
                            parada.ordem = ordem_global
                            parada.nome = f'Parada {ordem_global}'
                            if seq_local < len(schedule):
                                parada.horario_chegada = schedule[seq_local]['chegada']
                                parada.horario_partida = schedule[seq_local]['partida']
                            if parada.horario_partida:
                                tempo_veiculo = rutils.calcular_tempo_veiculo(
                                    seq_local + 1, parada.horario_partida, rot.horario_chegada
                                )
                                for passageiro in parada.passageiros.filter_by(ativo=True).all():
                                    passageiro.tempo_no_veiculo = tempo_veiculo

                total_dist += resultado['total_distance_km']
                duracoes_rotas.append(resultado['total_duration_min'])
                num_roteiros += 1

            # Finalizar
            max_dur = max(duracoes_rotas) if duracoes_rotas else 0
            rot.total_rotas = num_roteiros
            rot.distancia_total_km = round(total_dist, 2)
            rot.duracao_total_minutos = round(max_dur)
            rot.status = 'otimizado'

            elapsed = round(_time.time() - start_time)
            msg_tempo = ''
            if num_roteiros > 1:
                msg_tempo = f' (dividido em {num_roteiros} rotas para respeitar tempo máximo de {rot.tempo_maximo_viagem} min)'
            if timeout_hit:
                rotas_restantes = len(sub_rotas_capacidade) - len(sub_rotas_finais)
                flash_msg = (f'Otimização parcial ({elapsed}s): {num_roteiros} rota(s) processadas. '
                             f'{rotas_restantes} grupo(s) não processado(s) por timeout. '
                             f'Tente "Recalcular" para as rotas restantes.')
                flash_cat = 'warning'
            else:
                flash_msg = f'Otimização concluída ({elapsed}s): {num_roteiros} rota(s), {round(total_dist, 1)} km total.{msg_tempo}'
                flash_cat = 'success'

            rot.progresso_json = json.dumps({
                'operacao': 'otimizar', 'status': 'completed',
                'etapa': 'Concluído!', 'percentual': 100, 'inicio': inicio,
                'resultado_flash': {'msg': flash_msg, 'cat': flash_cat}
            }, ensure_ascii=False)
            db.session.commit()

        except Exception as e:
            try:
                rot = db.session.get(Roteirizacao, rot_id)
                if rot:
                    rot.progresso_json = json.dumps({
                        'operacao': 'otimizar', 'status': 'error',
                        'etapa': 'Erro na otimização', 'percentual': 0, 'inicio': inicio,
                        'erro': str(e)
                    }, ensure_ascii=False)
                    db.session.commit()
            except Exception:
                pass


# ============================================
# RECALCULAR
# ============================================

@roteirizador_bp.route('/<int:id>/recalcular', methods=['POST'])
@roteirizador_required
def recalcular(id):
    rot = Roteirizacao.query.get_or_404(id)

    # Salvar simulação atual antes de recalcular (se já otimizado)
    if rot.status in ('otimizado', 'finalizado') and rot.total_rotas:
        _salvar_simulacao(rot)

    # Atualizar parâmetros
    dist_max = request.form.get('distancia_maxima_caminhada', type=int)
    tempo_max = request.form.get('tempo_maximo_viagem', type=int)
    horario = request.form.get('horario_chegada', '')
    capacidade = request.form.get('capacidade_veiculo', type=int)

    if dist_max:
        rot.distancia_maxima_caminhada = dist_max
    if tempo_max:
        rot.tempo_maximo_viagem = tempo_max
    if capacidade:
        rot.capacidade_veiculo = capacidade
    if horario:
        try:
            h, m = horario.split(':')
            rot.horario_chegada = time(int(h), int(m))
        except (ValueError, AttributeError):
            pass

    # Voltar ao status geocodificado para re-clusterizar e re-otimizar
    rot.status = 'geocodificado'
    db.session.commit()

    flash('Simulação anterior salva. Clusterize e otimize com os novos parâmetros.', 'info')
    return redirect(url_for('roteirizador.visualizar', id=id))


def _salvar_simulacao(rot):
    """Salva o estado atual da roteirização como simulação"""
    num = rot.simulacoes.count() + 1
    nome = f'Simulação {num} - {rot.distancia_maxima_caminhada}m, Cap. {rot.capacidade_veiculo}'

    # Snapshot dos dados das rotas e paradas
    all_roteiros = rot.roteiros.filter_by(ativo=True).order_by(RoteiroPlanejado.ordem).all()
    roteiros_ida = [r for r in all_roteiros if r.tipo != 'volta']
    roteiros_volta = [r for r in all_roteiros if r.tipo == 'volta']

    all_paradas = rot.paradas.filter_by(ativo=True).order_by(PontoParada.roteiro_id, PontoParada.ordem).all()
    volta_ids = {r.id for r in roteiros_volta}
    paradas_ida = [p for p in all_paradas if p.roteiro_id not in volta_ids]
    paradas_volta = [p for p in all_paradas if p.roteiro_id in volta_ids]

    # Mapa de parada_id -> nome da parada (ida)
    parada_map = {p.id: p.nome for p in paradas_ida}

    # Passageiros ativos
    passageiros = rot.passageiros.filter_by(ativo=True).order_by(Passageiro.nome).all()

    dados = {
        'roteiros': [{
            'nome': r.nome,
            'ordem': r.ordem,
            'distancia_km': r.distancia_km,
            'duracao_minutos': r.duracao_minutos,
            'total_passageiros': r.total_passageiros,
            'capacidade_veiculo': r.capacidade_veiculo,
            'horario_saida': r.horario_saida.strftime('%H:%M') if r.horario_saida else None,
            'horario_chegada_destino': r.horario_chegada_destino.strftime('%H:%M') if r.horario_chegada_destino else None,
            'polyline_encoded': r.polyline_encoded,
        } for r in roteiros_ida],
        'paradas': [{
            'nome': p.nome,
            'endereco_referencia': p.endereco_referencia,
            'lat': p.lat,
            'lng': p.lng,
            'ordem': p.ordem,
            'roteiro_nome': next((r.nome for r in roteiros_ida if r.id == p.roteiro_id), None),
            'total_passageiros': p.total_passageiros,
            'horario_chegada': p.horario_chegada.strftime('%H:%M') if p.horario_chegada else None,
            'horario_partida': p.horario_partida.strftime('%H:%M') if p.horario_partida else None,
        } for p in paradas_ida],
        'passageiros': [{
            'nome': ps.nome,
            'endereco': ps.endereco_completo() or '-',
            'bairro': ps.bairro or '-',
            'parada_nome': parada_map.get(ps.parada_id, '-'),
            'distancia_ate_parada': ps.distancia_ate_parada,
            'tempo_no_veiculo': ps.tempo_no_veiculo,
        } for ps in passageiros],
        'roteiros_volta': [{
            'nome': r.nome,
            'ordem': r.ordem,
            'distancia_km': r.distancia_km,
            'duracao_minutos': r.duracao_minutos,
            'total_passageiros': r.total_passageiros,
            'capacidade_veiculo': r.capacidade_veiculo,
            'horario_saida': r.horario_saida.strftime('%H:%M') if r.horario_saida else None,
            'polyline_encoded': r.polyline_encoded,
        } for r in roteiros_volta],
        'paradas_volta': [{
            'nome': p.nome,
            'endereco_referencia': p.endereco_referencia,
            'lat': p.lat,
            'lng': p.lng,
            'ordem': p.ordem,
            'roteiro_nome': next((r.nome for r in roteiros_volta if r.id == p.roteiro_id), None),
            'total_passageiros': p.total_passageiros,
            'horario_chegada': p.horario_chegada.strftime('%H:%M') if p.horario_chegada else None,
            'horario_partida': p.horario_partida.strftime('%H:%M') if p.horario_partida else None,
        } for p in paradas_volta],
        'horario_saida_retorno': rot.horario_saida_retorno.strftime('%H:%M') if rot.horario_saida_retorno else None,
        'total_rotas_volta': rot.total_rotas_volta,
        'distancia_total_km_volta': rot.distancia_total_km_volta,
        'duracao_total_minutos_volta': rot.duracao_total_minutos_volta,
    }

    simulacao = Simulacao(
        roteirizacao_id=rot.id,
        nome=nome,
        distancia_maxima_caminhada=rot.distancia_maxima_caminhada,
        tempo_maximo_viagem=rot.tempo_maximo_viagem,
        horario_chegada=rot.horario_chegada,
        capacidade_veiculo=rot.capacidade_veiculo,
        total_rotas=rot.total_rotas,
        total_paradas=rot.total_paradas,
        distancia_total_km=rot.distancia_total_km,
        duracao_total_minutos=rot.duracao_total_minutos,
        dados_json=json.dumps(dados, ensure_ascii=False)
    )
    db.session.add(simulacao)
    db.session.flush()


# ============================================
# GERAR RETORNO (VOLTA)
# ============================================

@roteirizador_bp.route('/<int:id>/gerar_retorno', methods=['POST'])
@roteirizador_required
def gerar_retorno(id):
    rot = Roteirizacao.query.get_or_404(id)
    rutils.init_api_key(current_app.config['GOOGLE_MAPS_API_KEY'])

    if rot.status not in ('otimizado', 'finalizado'):
        flash('A roteirização precisa estar otimizada primeiro.', 'warning')
        return redirect(url_for('roteirizador.visualizar', id=id))

    # Obter horário de saída do retorno
    horario_str = request.form.get('horario_saida_retorno', '')
    if not horario_str:
        flash('Informe o horário de saída do destino.', 'danger')
        return redirect(url_for('roteirizador.visualizar', id=id))

    try:
        h, m = horario_str.split(':')
        horario_saida = time(int(h), int(m))
    except (ValueError, AttributeError):
        flash('Horário inválido.', 'danger')
        return redirect(url_for('roteirizador.visualizar', id=id))

    rot.horario_saida_retorno = horario_saida

    # Calcular timestamp de partida para trânsito (volta)
    departure_ts_volta = rutils._prox_dia_util_timestamp(horario_saida)

    # Limpar roteiros de volta existentes e suas paradas
    roteiros_volta = rot.roteiros.filter_by(tipo='volta').all()
    for rv in roteiros_volta:
        PontoParada.query.filter_by(roteiro_id=rv.id).delete()
        db.session.delete(rv)
    db.session.flush()

    # Buscar roteiros de ida
    roteiros_ida = rot.roteiros.filter_by(ativo=True, tipo='ida').order_by(RoteiroPlanejado.ordem).all()

    if not roteiros_ida:
        flash('Nenhuma rota de ida encontrada.', 'danger')
        return redirect(url_for('roteirizador.visualizar', id=id))

    total_dist_volta = 0
    max_dur_volta = 0
    num_volta = 0

    for r_idx, roteiro_ida in enumerate(roteiros_ida, start=1):
        # Pegar paradas da ida para este roteiro
        paradas_ida = roteiro_ida.paradas.filter_by(ativo=True).order_by(PontoParada.ordem).all()

        if not paradas_ida:
            continue

        # Preparar dados para otimização
        paradas_data = [{'id': p.id, 'lat': p.lat, 'lng': p.lng} for p in paradas_ida]

        # Otimizar rota de volta (destino como origem)
        resultado = rutils.otimizar_rota_google_volta(paradas_data, rot.destino_lat, rot.destino_lng, departure_ts_volta)

        if not resultado or 'error' in resultado:
            error_detail = resultado.get('error', 'Resposta vazia') if resultado else 'Sem resposta da API'
            flash(f'Erro ao otimizar volta {r_idx}: {error_detail}', 'danger')
            continue

        # Calcular horários progressivos
        dwell = current_app.config.get('ROTEIRIZADOR_DWELL_TIME', 60)
        schedule = rutils.calcular_horarios_volta(resultado['legs'], horario_saida, dwell)

        # Criar roteiro de volta
        roteiro_volta = RoteiroPlanejado(
            roteirizacao_id=id,
            nome=f'Volta {r_idx}',
            ordem=r_idx,
            tipo='volta',
            distancia_km=resultado['total_distance_km'],
            duracao_minutos=resultado['total_duration_min'],
            polyline_encoded=resultado['polyline'],
            horario_saida=horario_saida,
            horario_chegada_destino=None,
            capacidade_veiculo=roteiro_ida.capacidade_veiculo,
            total_passageiros=roteiro_ida.total_passageiros
        )
        db.session.add(roteiro_volta)
        db.session.flush()

        # Criar paradas de volta (cópias com novos horários)
        ordem_otimizada = resultado['waypoint_order']

        for seq_local, orig_idx in enumerate(ordem_otimizada):
            if orig_idx < len(paradas_ida):
                parada_ida = paradas_ida[orig_idx]
                parada_volta = PontoParada(
                    roteirizacao_id=id,
                    roteiro_id=roteiro_volta.id,
                    nome=f'Parada V{seq_local + 1}',
                    endereco_referencia=parada_ida.endereco_referencia,
                    lat=parada_ida.lat,
                    lng=parada_ida.lng,
                    ordem=seq_local + 1,
                    total_passageiros=parada_ida.total_passageiros,
                )
                if seq_local < len(schedule):
                    parada_volta.horario_chegada = schedule[seq_local]['chegada']
                    parada_volta.horario_partida = schedule[seq_local]['partida']

                db.session.add(parada_volta)

        total_dist_volta += resultado['total_distance_km']
        if resultado['total_duration_min'] > max_dur_volta:
            max_dur_volta = resultado['total_duration_min']
        num_volta += 1

    rot.total_rotas_volta = num_volta
    rot.distancia_total_km_volta = round(total_dist_volta, 2)
    rot.duracao_total_minutos_volta = round(max_dur_volta)
    db.session.commit()

    flash(f'Retorno gerado: {num_volta} rota(s) de volta, {round(total_dist_volta, 1)} km total.', 'success')
    return redirect(url_for('roteirizador.visualizar', id=id))


# ============================================
# FINALIZAR
# ============================================

@roteirizador_bp.route('/<int:id>/finalizar', methods=['POST'])
@roteirizador_required
def finalizar(id):
    rot = Roteirizacao.query.get_or_404(id)

    if rot.status != 'otimizado':
        flash('A roteirização precisa estar otimizada para ser finalizada.', 'warning')
        return redirect(url_for('roteirizador.visualizar', id=id))

    rot.status = 'finalizado'

    # Vincular passageiros base à rota finalizada
    for passageiro in rot.passageiros.filter_by(ativo=True).all():
        if passageiro.passageiro_base_id:
            pb = PassageiroBase.query.get(passageiro.passageiro_base_id)
            if pb:
                pb.roteirizacao_vinculada_id = rot.id

    db.session.commit()

    flash('Roteirização finalizada com sucesso!', 'success')
    return redirect(url_for('roteirizador.visualizar', id=id))


@roteirizador_bp.route('/<int:id>/reabrir', methods=['POST'])
@roteirizador_required
def reabrir(id):
    rot = Roteirizacao.query.get_or_404(id)

    if rot.status != 'finalizado':
        flash('Apenas roteirizações finalizadas podem ser reabertas.', 'warning')
        return redirect(url_for('roteirizador.visualizar', id=id))

    rot.status = 'otimizado'
    db.session.commit()

    flash('Roteirização reaberta. Você pode editar e recalcular.', 'info')
    return redirect(url_for('roteirizador.visualizar', id=id))


# ============================================
# EDITAR MAPA (tela dedicada)
# ============================================

@roteirizador_bp.route('/<int:id>/editar_mapa')
@roteirizador_required
def editar_mapa(id):
    rot = Roteirizacao.query.get_or_404(id)

    if rot.status not in ('otimizado', 'finalizado'):
        flash('Otimize a rota antes de editar o mapa.', 'warning')
        return redirect(url_for('roteirizador.visualizar', id=id))

    # Separar roteiros ida e volta
    all_roteiros = rot.roteiros.filter_by(ativo=True).order_by(RoteiroPlanejado.ordem).all()
    roteiros_ida = [r for r in all_roteiros if r.tipo != 'volta']
    roteiros_volta = [r for r in all_roteiros if r.tipo == 'volta']

    paradas = rot.paradas.filter_by(ativo=True).order_by(PontoParada.roteiro_id, PontoParada.ordem).all()
    volta_ids = {r.id for r in roteiros_volta}
    paradas_ida = [p for p in paradas if p.roteiro_id not in volta_ids]
    paradas_volta = [p for p in paradas if p.roteiro_id in volta_ids]

    passageiros = rot.passageiros.filter(
        Passageiro.ativo == True,
        Passageiro.lat.isnot(None)
    ).all()

    api_key = current_app.config['GOOGLE_MAPS_API_KEY']

    # Rotas existentes (finalizadas) do mesmo cliente+turno
    rotas_existentes = []
    if rot.cliente_id and rot.turno_id:
        outras = Roteirizacao.query.filter(
            Roteirizacao.cliente_id == rot.cliente_id,
            Roteirizacao.turno_id == rot.turno_id,
            Roteirizacao.status == 'finalizado',
            Roteirizacao.ativo == True,
            Roteirizacao.id != rot.id
        ).all()
        for outra in outras:
            for r in outra.roteiros.filter_by(ativo=True, tipo='ida').all():
                if r.polyline_encoded:
                    rotas_existentes.append({
                        'nome': f'{outra.nome} - {r.nome}',
                        'polyline': r.polyline_encoded,
                        'roteirizacao_id': outra.id,
                    })

    # Passageiros disponíveis (não vinculados) do mesmo cliente+turno
    passageiros_disponiveis = []
    if rot.cliente_id and rot.turno_id:
        passageiros_disponiveis = PassageiroBase.query.filter(
            PassageiroBase.cliente_id == rot.cliente_id,
            PassageiroBase.turno_id == rot.turno_id,
            PassageiroBase.roteirizacao_vinculada_id.is_(None),
            PassageiroBase.ativo == True,
            PassageiroBase.geocode_status == 'sucesso',
            PassageiroBase.lat.isnot(None)
        ).order_by(PassageiroBase.nome).all()

    return render_template('roteirizador/editar_mapa.html',
                           rot=rot,
                           roteiros_ida=roteiros_ida,
                           roteiros_volta=roteiros_volta,
                           paradas_ida=paradas_ida,
                           paradas_volta=paradas_volta,
                           passageiros=passageiros,
                           passageiros_disponiveis=passageiros_disponiveis,
                           tem_volta=len(roteiros_volta) > 0,
                           api_key=api_key,
                           rotas_existentes=rotas_existentes)


# ============================================
# SALVAR ROTA EDITADA (drag-and-drop no mapa)
# ============================================

@roteirizador_bp.route('/<int:id>/salvar_rota_editada', methods=['POST'])
@roteirizador_required
def salvar_rota_editada(id):
    rot = Roteirizacao.query.get_or_404(id)
    data = request.get_json()

    if not data:
        return jsonify({'ok': False, 'msg': 'Dados inválidos'}), 400

    roteiro_id = data.get('roteiro_id')
    polyline = data.get('polyline', '')
    legs = data.get('legs', [])
    waypoints = data.get('waypoints', [])

    roteiro = RoteiroPlanejado.query.get(roteiro_id)
    if not roteiro or roteiro.roteirizacao_id != id:
        return jsonify({'ok': False, 'msg': 'Roteiro não encontrado'}), 404

    # Atualizar polyline e métricas do roteiro
    roteiro.polyline_encoded = polyline

    total_dist_m = sum(l.get('distance_m', 0) for l in legs)
    total_dur_s = sum(l.get('duration_s', 0) for l in legs)
    roteiro.distancia_km = round(total_dist_m / 1000, 2)
    roteiro.duracao_minutos = round(total_dur_s / 60)

    # Recalcular horários com os novos legs (ida vs volta)
    dwell = current_app.config.get('ROTEIRIZADOR_DWELL_TIME', 60)
    if roteiro.tipo == 'volta' and rot.horario_saida_retorno:
        schedule = rutils.calcular_horarios_volta(legs, rot.horario_saida_retorno, dwell)
        if schedule:
            roteiro.horario_saida = rot.horario_saida_retorno
    else:
        schedule = rutils.calcular_horarios(legs, rot.horario_chegada, dwell)
        if schedule:
            roteiro.horario_saida = schedule[0]['chegada']

    # Atualizar paradas na ordem recebida dos waypoints
    paradas = roteiro.paradas.filter_by(ativo=True).order_by(PontoParada.ordem).all()

    # waypoints contém {lat, lng, parada_id} na nova ordem
    for seq, wp in enumerate(waypoints, start=1):
        parada_id = wp.get('parada_id')
        parada = PontoParada.query.get(parada_id) if parada_id else None
        if parada and parada.roteirizacao_id == id:
            parada.lat = wp['lat']
            parada.lng = wp['lng']
            parada.ordem = seq
            if seq - 1 < len(schedule):
                parada.horario_chegada = schedule[seq - 1]['chegada']
                parada.horario_partida = schedule[seq - 1]['partida']

            # Recalcular tempo no veículo
            if parada.horario_partida:
                tempo_veiculo = rutils.calcular_tempo_veiculo(
                    seq, parada.horario_partida, rot.horario_chegada
                )
                for passageiro in parada.passageiros.filter_by(ativo=True).all():
                    passageiro.tempo_no_veiculo = tempo_veiculo

    # Recalcular totais da roteirização a partir de todas as rotas
    todos_roteiros = rot.roteiros.filter_by(ativo=True).all()
    rot.distancia_total_km = round(sum(r.distancia_km or 0 for r in todos_roteiros), 2)
    rot.duracao_total_minutos = round(max((r.duracao_minutos or 0) for r in todos_roteiros))
    db.session.commit()

    return jsonify({'ok': True, 'msg': 'Rota atualizada com sucesso!'})


# ============================================
# MOVER PARADA ENTRE ROTAS
# ============================================

@roteirizador_bp.route('/<int:id>/mover_parada', methods=['POST'])
@roteirizador_required
def mover_parada(id):
    rot = Roteirizacao.query.get_or_404(id)
    data = request.get_json()

    if not data:
        return jsonify({'ok': False, 'msg': 'Dados inválidos'}), 400

    parada_id = data.get('parada_id')
    roteiro_destino_id = data.get('roteiro_destino_id')

    parada = PontoParada.query.get(parada_id)
    if not parada or parada.roteirizacao_id != id:
        return jsonify({'ok': False, 'msg': 'Parada não encontrada'}), 404

    roteiro_destino = RoteiroPlanejado.query.get(roteiro_destino_id)
    if not roteiro_destino or roteiro_destino.roteirizacao_id != id:
        return jsonify({'ok': False, 'msg': 'Roteiro destino não encontrado'}), 404

    roteiro_origem = RoteiroPlanejado.query.get(parada.roteiro_id)
    if not roteiro_origem:
        return jsonify({'ok': False, 'msg': 'Roteiro origem não encontrado'}), 404

    # Validar mesmo tipo (ida com ida, volta com volta)
    if roteiro_origem.tipo != roteiro_destino.tipo:
        return jsonify({'ok': False, 'msg': 'Só é possível mover entre rotas do mesmo tipo'}), 400

    roteiro_origem_id = parada.roteiro_id

    # Mover parada para roteiro destino
    parada.roteiro_id = roteiro_destino_id

    # Flush para que queries reflitam a mudança de roteiro_id
    db.session.flush()

    # Colocar no final do roteiro destino
    max_ordem = db.session.query(db.func.max(PontoParada.ordem)).filter(
        PontoParada.roteiro_id == roteiro_destino_id,
        PontoParada.ativo == True,
        PontoParada.id != parada.id
    ).scalar() or 0
    parada.ordem = max_ordem + 1

    # Resequenciar paradas no roteiro de origem
    paradas_origem = PontoParada.query.filter(
        PontoParada.roteiro_id == roteiro_origem_id,
        PontoParada.ativo == True
    ).order_by(PontoParada.ordem).all()
    for seq, p in enumerate(paradas_origem, start=1):
        p.ordem = seq

    # Recalcular total_passageiros nos roteiros
    roteiro_origem.total_passageiros = sum(
        p.total_passageiros or 0 for p in PontoParada.query.filter(
            PontoParada.roteiro_id == roteiro_origem_id,
            PontoParada.ativo == True
        ).all()
    )
    roteiro_destino.total_passageiros = sum(
        p.total_passageiros or 0 for p in PontoParada.query.filter(
            PontoParada.roteiro_id == roteiro_destino_id,
            PontoParada.ativo == True
        ).all()
    )

    db.session.commit()

    return jsonify({
        'ok': True,
        'msg': 'Parada movida com sucesso!',
        'parada_id': parada_id,
        'roteiro_origem_id': roteiro_origem_id,
        'roteiro_destino_id': roteiro_destino_id,
        'total_pass_origem': roteiro_origem.total_passageiros,
        'total_pass_destino': roteiro_destino.total_passageiros
    })


# ============================================
# MOVER PASSAGEIRO PARA OUTRA ROTA
# ============================================

@roteirizador_bp.route('/<int:id>/mover_passageiro', methods=['POST'])
@roteirizador_required
def mover_passageiro(id):
    rot = Roteirizacao.query.get_or_404(id)
    data = request.get_json()

    if not data:
        return jsonify({'ok': False, 'msg': 'Dados inválidos'}), 400

    passageiro_id = data.get('passageiro_id')
    roteiro_destino_id = data.get('roteiro_destino_id')

    passageiro = Passageiro.query.get(passageiro_id)
    if not passageiro or passageiro.roteirizacao_id != id:
        return jsonify({'ok': False, 'msg': 'Passageiro não encontrado'}), 404

    roteiro_destino = RoteiroPlanejado.query.get(roteiro_destino_id)
    if not roteiro_destino or roteiro_destino.roteirizacao_id != id:
        return jsonify({'ok': False, 'msg': 'Rota destino não encontrada'}), 404

    parada_origem_id = passageiro.parada_id
    parada_origem = PontoParada.query.get(parada_origem_id) if parada_origem_id else None
    roteiro_origem_id = parada_origem.roteiro_id if parada_origem else None

    if roteiro_origem_id == roteiro_destino_id:
        return jsonify({'ok': False, 'msg': 'Passageiro já está nesta rota'}), 400

    # Criar nova parada na posição do passageiro na rota destino
    from kml_utils import haversine

    # Número sequencial global para nome da parada
    max_num = db.session.query(db.func.max(PontoParada.ordem)).filter(
        PontoParada.roteirizacao_id == id,
        PontoParada.ativo == True
    ).scalar() or 0

    # Buscar paradas ativas da rota destino para calcular melhor posição
    paradas_destino = PontoParada.query.filter(
        PontoParada.roteiro_id == roteiro_destino_id,
        PontoParada.ativo == True
    ).order_by(PontoParada.ordem).all()

    # Encontrar a melhor posição de inserção (menor desvio)
    melhor_pos = len(paradas_destino)  # Default: final
    menor_custo = float('inf')

    for i in range(len(paradas_destino) + 1):
        custo = 0
        if i > 0:
            prev = paradas_destino[i - 1]
            custo += haversine(prev.lat, prev.lng, passageiro.lat, passageiro.lng)
        if i < len(paradas_destino):
            nxt = paradas_destino[i]
            custo += haversine(passageiro.lat, passageiro.lng, nxt.lat, nxt.lng)
            if i > 0:
                prev = paradas_destino[i - 1]
                custo -= haversine(prev.lat, prev.lng, nxt.lat, nxt.lng)
        else:
            # Custo de ir até o destino
            custo += haversine(passageiro.lat, passageiro.lng, rot.destino_lat, rot.destino_lng)
            if i > 0:
                prev = paradas_destino[i - 1]
                custo -= haversine(prev.lat, prev.lng, rot.destino_lat, rot.destino_lng)

        if custo < menor_custo:
            menor_custo = custo
            melhor_pos = i

    # Definir ordem: inserir na posição ótima
    if paradas_destino and melhor_pos < len(paradas_destino):
        nova_ordem = paradas_destino[melhor_pos].ordem
        # Empurrar as paradas seguintes
        for p in paradas_destino[melhor_pos:]:
            p.ordem = p.ordem + 1
    elif paradas_destino:
        nova_ordem = paradas_destino[-1].ordem + 1
    else:
        nova_ordem = 1

    nova_parada = PontoParada(
        roteirizacao_id=id,
        nome=f'Parada {max_num + 1}',
        endereco_referencia=passageiro.endereco_completo() or passageiro.endereco_formatado,
        lat=passageiro.lat,
        lng=passageiro.lng,
        roteiro_id=roteiro_destino_id,
        ordem=nova_ordem,
        total_passageiros=1,
        ativo=True
    )
    db.session.add(nova_parada)
    db.session.flush()  # Para obter o ID da nova parada

    # Atualizar passageiro para a nova parada
    passageiro.parada_id = nova_parada.id
    passageiro.distancia_ate_parada = 0  # Parada criada na posição do passageiro

    db.session.flush()

    # Recalcular total_passageiros na parada de origem
    if parada_origem:
        parada_origem.total_passageiros = parada_origem.passageiros.filter_by(ativo=True).count()
        # Se a parada de origem ficou sem passageiros, desativar
        if parada_origem.total_passageiros == 0:
            parada_origem.ativo = False

    # Recalcular total_passageiros nos roteiros
    if roteiro_origem_id:
        roteiro_origem = RoteiroPlanejado.query.get(roteiro_origem_id)
        if roteiro_origem:
            roteiro_origem.total_passageiros = sum(
                p.total_passageiros or 0 for p in PontoParada.query.filter(
                    PontoParada.roteiro_id == roteiro_origem_id,
                    PontoParada.ativo == True
                ).all()
            )

    roteiro_destino.total_passageiros = sum(
        p.total_passageiros or 0 for p in PontoParada.query.filter(
            PontoParada.roteiro_id == roteiro_destino_id,
            PontoParada.ativo == True
        ).all()
    )

    db.session.commit()

    # Paradas atualizadas da rota destino (com ordens recalculadas)
    paradas_destino_atualizadas = [{
        'id': p.id, 'ordem': p.ordem
    } for p in PontoParada.query.filter(
        PontoParada.roteiro_id == roteiro_destino_id,
        PontoParada.ativo == True
    ).order_by(PontoParada.ordem).all()]

    return jsonify({
        'ok': True,
        'msg': 'Passageiro movido com sucesso!',
        'passageiro_id': passageiro_id,
        'parada_origem_id': parada_origem_id,
        'parada_origem_vazia': parada_origem.total_passageiros == 0 if parada_origem else True,
        'nova_parada': {
            'id': nova_parada.id,
            'lat': nova_parada.lat,
            'lng': nova_parada.lng,
            'nome': nova_parada.nome,
            'ordem': nova_parada.ordem,
            'total': nova_parada.total_passageiros,
            'roteiroId': roteiro_destino_id
        },
        'paradas_destino_atualizadas': paradas_destino_atualizadas,
        'roteiro_origem_id': roteiro_origem_id,
        'roteiro_destino_id': roteiro_destino_id,
        'total_pass_origem': roteiro_destino.total_passageiros if roteiro_origem_id is None else RoteiroPlanejado.query.get(roteiro_origem_id).total_passageiros if roteiro_origem_id else 0,
        'total_pass_destino': roteiro_destino.total_passageiros
    })


# ============================================
# ALOCAR PASSAGEIRO DISPONÍVEL A UMA ROTA
# ============================================

@roteirizador_bp.route('/<int:id>/alocar_passageiro', methods=['POST'])
@roteirizador_required
def alocar_passageiro(id):
    rot = Roteirizacao.query.get_or_404(id)
    data = request.get_json()

    if not data:
        return jsonify({'ok': False, 'msg': 'Dados inválidos'}), 400

    passageiro_base_id = data.get('passageiro_base_id')
    roteiro_id = data.get('roteiro_id')
    parada_id = data.get('parada_id')  # Opcional
    criar_nova = data.get('criar_nova', False)  # Só cria nova parada se explicitamente pedido

    # Validar PassageiroBase
    pb = PassageiroBase.query.get(passageiro_base_id)
    if not pb or pb.roteirizacao_vinculada_id is not None:
        return jsonify({'ok': False, 'msg': 'Passageiro não disponível'}), 404
    if not pb.lat or not pb.lng:
        return jsonify({'ok': False, 'msg': 'Passageiro não geocodificado'}), 400

    # Validar roteiro
    roteiro = RoteiroPlanejado.query.get(roteiro_id)
    if not roteiro or roteiro.roteirizacao_id != id:
        return jsonify({'ok': False, 'msg': 'Rota não encontrada'}), 404

    # Criar Passageiro na roteirização (cópia dos dados do PassageiroBase)
    passageiro = Passageiro(
        roteirizacao_id=id,
        passageiro_base_id=pb.id,
        nome=pb.nome,
        endereco=pb.endereco,
        numero=pb.numero,
        bairro=pb.bairro,
        cidade=pb.cidade,
        estado=pb.estado,
        cep=pb.cep,
        complemento=pb.complemento,
        telefone=pb.telefone,
        observacoes=pb.observacoes,
        lat=pb.lat,
        lng=pb.lng,
        endereco_formatado=pb.endereco_formatado,
        geocode_status='sucesso',
    )
    db.session.add(passageiro)
    db.session.flush()

    nova_parada_data = None
    parada_atualizada_data = None

    from kml_utils import haversine

    # Se não tem parada_id e não pediu criar nova, buscar a parada mais próxima automaticamente
    if not parada_id and not criar_nova:
        paradas_roteiro = PontoParada.query.filter(
            PontoParada.roteiro_id == roteiro_id,
            PontoParada.ativo == True
        ).all()
        if paradas_roteiro:
            menor_dist = float('inf')
            parada_mais_proxima = None
            for par in paradas_roteiro:
                d = haversine(pb.lat, pb.lng, par.lat, par.lng)
                if d < menor_dist:
                    menor_dist = d
                    parada_mais_proxima = par
            if parada_mais_proxima:
                parada_id = parada_mais_proxima.id

    if parada_id:
        # Alocar em parada existente
        parada = PontoParada.query.get(parada_id)
        if not parada or parada.roteirizacao_id != id:
            db.session.rollback()
            return jsonify({'ok': False, 'msg': 'Parada não encontrada'}), 404

        passageiro.parada_id = parada.id
        passageiro.distancia_ate_parada = haversine(pb.lat, pb.lng, parada.lat, parada.lng)
        parada.total_passageiros = (parada.total_passageiros or 0) + 1
        parada_atualizada_data = {
            'id': parada.id,
            'total': parada.total_passageiros
        }
    else:
        # Criar nova parada na posição do passageiro (somente quando explicitamente pedido)

        max_num = db.session.query(db.func.max(PontoParada.ordem)).filter(
            PontoParada.roteirizacao_id == id,
            PontoParada.ativo == True
        ).scalar() or 0

        # Buscar paradas ativas da rota para calcular melhor posição de inserção
        paradas_roteiro = PontoParada.query.filter(
            PontoParada.roteiro_id == roteiro_id,
            PontoParada.ativo == True
        ).order_by(PontoParada.ordem).all()

        melhor_pos = len(paradas_roteiro)
        menor_custo = float('inf')

        for i in range(len(paradas_roteiro) + 1):
            custo = 0
            if i > 0:
                prev = paradas_roteiro[i - 1]
                custo += haversine(prev.lat, prev.lng, pb.lat, pb.lng)
            if i < len(paradas_roteiro):
                nxt = paradas_roteiro[i]
                custo += haversine(pb.lat, pb.lng, nxt.lat, nxt.lng)
                if i > 0:
                    prev = paradas_roteiro[i - 1]
                    custo -= haversine(prev.lat, prev.lng, nxt.lat, nxt.lng)
            else:
                custo += haversine(pb.lat, pb.lng, rot.destino_lat, rot.destino_lng)
                if i > 0:
                    prev = paradas_roteiro[i - 1]
                    custo -= haversine(prev.lat, prev.lng, rot.destino_lat, rot.destino_lng)

            if custo < menor_custo:
                menor_custo = custo
                melhor_pos = i

        if paradas_roteiro and melhor_pos < len(paradas_roteiro):
            nova_ordem = paradas_roteiro[melhor_pos].ordem
            for p in paradas_roteiro[melhor_pos:]:
                p.ordem = p.ordem + 1
        elif paradas_roteiro:
            nova_ordem = paradas_roteiro[-1].ordem + 1
        else:
            nova_ordem = 1

        nova_parada = PontoParada(
            roteirizacao_id=id,
            nome=f'Parada {max_num + 1}',
            endereco_referencia=pb.endereco_completo() or pb.endereco_formatado,
            lat=pb.lat,
            lng=pb.lng,
            roteiro_id=roteiro_id,
            ordem=nova_ordem,
            total_passageiros=1,
            ativo=True
        )
        db.session.add(nova_parada)
        db.session.flush()

        passageiro.parada_id = nova_parada.id
        passageiro.distancia_ate_parada = 0

        nova_parada_data = {
            'id': nova_parada.id,
            'lat': nova_parada.lat,
            'lng': nova_parada.lng,
            'nome': nova_parada.nome,
            'ordem': nova_parada.ordem,
            'total': nova_parada.total_passageiros,
            'roteiroId': roteiro_id
        }

    # Vincular PassageiroBase à roteirização
    pb.roteirizacao_vinculada_id = rot.id

    # Atualizar totais
    roteiro.total_passageiros = sum(
        p.total_passageiros or 0 for p in PontoParada.query.filter(
            PontoParada.roteiro_id == roteiro_id,
            PontoParada.ativo == True
        ).all()
    )
    rot.total_passageiros = rot.passageiros.filter_by(ativo=True).count()

    db.session.commit()

    # Paradas atualizadas
    paradas_atualizadas = [{
        'id': p.id, 'ordem': p.ordem
    } for p in PontoParada.query.filter(
        PontoParada.roteiro_id == roteiro_id,
        PontoParada.ativo == True
    ).order_by(PontoParada.ordem).all()]

    return jsonify({
        'ok': True,
        'msg': f'Passageiro "{pb.nome}" alocado com sucesso!',
        'passageiro': {
            'id': passageiro.id,
            'lat': passageiro.lat,
            'lng': passageiro.lng,
            'nome': passageiro.nome,
            'endereco': passageiro.endereco or '',
            'paradaId': passageiro.parada_id,
            'roteiroId': roteiro_id
        },
        'nova_parada': nova_parada_data,
        'parada_atualizada': parada_atualizada_data,
        'paradas_atualizadas': paradas_atualizadas,
        'roteiro_id': roteiro_id,
        'total_pass_roteiro': roteiro.total_passageiros
    })


# ============================================
# SALVAR POLYLINES RECALCULADAS (após mover paradas/passageiros)
# ============================================

@roteirizador_bp.route('/<int:id>/salvar_polylines', methods=['POST'])
@roteirizador_required
def salvar_polylines(id):
    rot = Roteirizacao.query.get_or_404(id)
    data = request.get_json()

    if not data or 'rotas' not in data:
        return jsonify({'ok': False, 'msg': 'Dados inválidos'}), 400

    dwell = current_app.config.get('ROTEIRIZADOR_DWELL_TIME', 60)

    for rota_data in data['rotas']:
        roteiro_id = rota_data.get('roteiro_id')
        polyline = rota_data.get('polyline', '')
        legs = rota_data.get('legs', [])

        roteiro = RoteiroPlanejado.query.get(roteiro_id)
        if not roteiro or roteiro.roteirizacao_id != id:
            continue

        roteiro.polyline_encoded = polyline

        total_dist_m = sum(l.get('distance_m', 0) for l in legs)
        total_dur_s = sum(l.get('duration_s', 0) for l in legs)
        roteiro.distancia_km = round(total_dist_m / 1000, 2)
        roteiro.duracao_minutos = round(total_dur_s / 60)

        # Recalcular horários
        if roteiro.tipo == 'volta' and rot.horario_saida_retorno:
            schedule = rutils.calcular_horarios_volta(legs, rot.horario_saida_retorno, dwell)
            if schedule:
                roteiro.horario_saida = rot.horario_saida_retorno
        else:
            schedule = rutils.calcular_horarios(legs, rot.horario_chegada, dwell)
            if schedule:
                roteiro.horario_saida = schedule[0]['chegada']

        # Atualizar posição e horários das paradas
        waypoints = rota_data.get('waypoints', [])
        paradas = roteiro.paradas.filter_by(ativo=True).order_by(PontoParada.ordem).all()

        if waypoints:
            # Waypoints do drag-and-drop: atualizar posição e ordem
            for seq, wp in enumerate(waypoints, start=1):
                parada_id = wp.get('parada_id')
                parada = PontoParada.query.get(parada_id) if parada_id else None
                if parada and parada.roteirizacao_id == id:
                    parada.lat = wp['lat']
                    parada.lng = wp['lng']
                    parada.ordem = seq
                    if seq - 1 < len(schedule):
                        parada.horario_chegada = schedule[seq - 1]['chegada']
                        parada.horario_partida = schedule[seq - 1]['partida']
                    if parada.horario_partida:
                        tempo_veiculo = rutils.calcular_tempo_veiculo(
                            seq, parada.horario_partida, rot.horario_chegada
                        )
                        for passageiro in parada.passageiros.filter_by(ativo=True).all():
                            passageiro.tempo_no_veiculo = tempo_veiculo
        else:
            # Sem waypoints: só atualizar horários
            for seq, parada in enumerate(paradas):
                if seq < len(schedule):
                    parada.horario_chegada = schedule[seq]['chegada']
                    parada.horario_partida = schedule[seq]['partida']
                    if parada.horario_partida:
                        tempo_veiculo = rutils.calcular_tempo_veiculo(
                            seq + 1, parada.horario_partida, rot.horario_chegada
                        )
                        for passageiro in parada.passageiros.filter_by(ativo=True).all():
                            passageiro.tempo_no_veiculo = tempo_veiculo

    # Recalcular totais
    todos_roteiros = rot.roteiros.filter_by(ativo=True).all()
    rot.distancia_total_km = round(sum(r.distancia_km or 0 for r in todos_roteiros), 2)
    rot.duracao_total_minutos = round(max((r.duracao_minutos or 0) for r in todos_roteiros))

    db.session.commit()
    return jsonify({'ok': True, 'msg': 'Rotas salvas com sucesso!'})


# ============================================
# SALVAR COMO SIMULAÇÃO (a partir do editor de mapa)
# ============================================

@roteirizador_bp.route('/<int:id>/salvar_simulacao_mapa', methods=['POST'])
@roteirizador_required
def salvar_simulacao_mapa(id):
    rot = Roteirizacao.query.get_or_404(id)

    # Primeiro salvar polylines se enviadas
    data = request.get_json()
    if data and 'rotas' in data:
        dwell = current_app.config.get('ROTEIRIZADOR_DWELL_TIME', 60)
        for rota_data in data['rotas']:
            roteiro_id = rota_data.get('roteiro_id')
            polyline = rota_data.get('polyline', '')
            legs = rota_data.get('legs', [])

            roteiro = RoteiroPlanejado.query.get(roteiro_id)
            if not roteiro or roteiro.roteirizacao_id != id:
                continue

            roteiro.polyline_encoded = polyline
            total_dist_m = sum(l.get('distance_m', 0) for l in legs)
            total_dur_s = sum(l.get('duration_s', 0) for l in legs)
            roteiro.distancia_km = round(total_dist_m / 1000, 2)
            roteiro.duracao_minutos = round(total_dur_s / 60)

            if roteiro.tipo == 'volta' and rot.horario_saida_retorno:
                schedule = rutils.calcular_horarios_volta(legs, rot.horario_saida_retorno, dwell)
                if schedule:
                    roteiro.horario_saida = rot.horario_saida_retorno
            else:
                schedule = rutils.calcular_horarios(legs, rot.horario_chegada, dwell)
                if schedule:
                    roteiro.horario_saida = schedule[0]['chegada']

            paradas = roteiro.paradas.filter_by(ativo=True).order_by(PontoParada.ordem).all()
            for seq, parada in enumerate(paradas):
                if seq < len(schedule):
                    parada.horario_chegada = schedule[seq]['chegada']
                    parada.horario_partida = schedule[seq]['partida']

        todos_roteiros = rot.roteiros.filter_by(ativo=True).all()
        rot.distancia_total_km = round(sum(r.distancia_km or 0 for r in todos_roteiros), 2)
        rot.duracao_total_minutos = round(max((r.duracao_minutos or 0) for r in todos_roteiros))

    # Salvar como simulação
    _salvar_simulacao(rot)
    db.session.commit()

    return jsonify({'ok': True, 'msg': 'Simulação salva com sucesso!'})


# ============================================
# RELATÓRIO
# ============================================

@roteirizador_bp.route('/<int:id>/relatorio')
@roteirizador_required
def relatorio(id):
    rot = Roteirizacao.query.get_or_404(id)

    all_roteiros = rot.roteiros.filter_by(ativo=True).order_by(RoteiroPlanejado.ordem).all()
    roteiros = [r for r in all_roteiros if r.tipo != 'volta']
    roteiros_volta = [r for r in all_roteiros if r.tipo == 'volta']

    all_paradas = rot.paradas.filter_by(ativo=True).order_by(PontoParada.roteiro_id, PontoParada.ordem).all()
    volta_ids = {r.id for r in roteiros_volta}
    paradas = [p for p in all_paradas if p.roteiro_id not in volta_ids]
    paradas_volta = [p for p in all_paradas if p.roteiro_id in volta_ids]

    passageiros = rot.passageiros.filter_by(ativo=True).order_by(Passageiro.nome).all()

    api_key = current_app.config['GOOGLE_MAPS_API_KEY']
    tipos_veiculo = TipoVeiculo.query.filter_by(ativo=True).order_by(TipoVeiculo.capacidade).all()

    return render_template('roteirizador/relatorio.html',
                           rot=rot,
                           roteiros=roteiros,
                           roteiros_volta=roteiros_volta,
                           paradas=paradas,
                           paradas_volta=paradas_volta,
                           passageiros=passageiros,
                           api_key=api_key,
                           tipos_veiculo=tipos_veiculo)


@roteirizador_bp.route('/<int:id>/relatorio/simulacao/<int:sim_id>')
@roteirizador_required
def relatorio_simulacao(id, sim_id):
    rot = Roteirizacao.query.get_or_404(id)
    sim = Simulacao.query.get_or_404(sim_id)

    if sim.roteirizacao_id != rot.id:
        flash('Simulação não pertence a esta roteirização.', 'danger')
        return redirect(url_for('roteirizador.visualizar', id=id))

    dados = json.loads(sim.dados_json)
    tipos_veiculo = TipoVeiculo.query.filter_by(ativo=True).order_by(TipoVeiculo.capacidade).all()
    passageiros = rot.passageiros.filter_by(ativo=True).order_by(Passageiro.nome).all()
    api_key = current_app.config['GOOGLE_MAPS_API_KEY']

    return render_template('roteirizador/relatorio_simulacao.html',
                           rot=rot,
                           sim=sim,
                           dados=dados,
                           passageiros=passageiros,
                           api_key=api_key,
                           tipos_veiculo=tipos_veiculo)


# ============================================
# EXPORTAR KML
# ============================================

@roteirizador_bp.route('/<int:id>/exportar/kml', methods=['POST'])
@roteirizador_required
def exportar_kml(id):
    rot = Roteirizacao.query.get_or_404(id)
    roteiros = rot.roteiros.filter_by(ativo=True).order_by(RoteiroPlanejado.ordem).all()

    if not roteiros:
        flash('Nenhuma rota otimizada para exportar.', 'danger')
        return redirect(url_for('roteirizador.visualizar', id=id))

    # Gerar KML para o primeiro roteiro (ou todos concatenados)
    roteiro = roteiros[0]
    paradas = roteiro.paradas.filter_by(ativo=True).order_by(PontoParada.ordem).all()

    paradas_data = [{
        'nome': p.nome,
        'lat': p.lat,
        'lng': p.lng,
        'ordem': p.ordem,
        'horario_chegada': p.horario_chegada,
        'total_passageiros': p.total_passageiros
    } for p in paradas]

    destino = {
        'endereco': rot.destino_endereco,
        'lat': rot.destino_lat,
        'lng': rot.destino_lng
    }

    kml_content = rutils.gerar_kml_roteiro(
        rot.nome,
        paradas_data,
        destino,
        roteiro.polyline_encoded
    )

    # Salvar e enviar
    filename = f"roteiro_{rot.id}_{rot.nome.replace(' ', '_')}.kml"
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], f"{uuid.uuid4().hex}_{filename}")

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(kml_content)

    rot.arquivo_kml = os.path.basename(filepath)
    rot.arquivo_kml_nome = filename
    db.session.commit()

    return send_file(filepath, as_attachment=True, download_name=filename, mimetype='application/vnd.google-earth.kml+xml')


# ============================================
# EXPORTAR CSV
# ============================================

@roteirizador_bp.route('/<int:id>/exportar/csv', methods=['POST'])
@roteirizador_required
def exportar_csv(id):
    rot = Roteirizacao.query.get_or_404(id)
    passageiros = rot.passageiros.filter_by(ativo=True).order_by(Passageiro.nome).all()

    output = io.StringIO()
    output.write('\ufeff')  # BOM para Excel
    writer = csv.writer(output, delimiter=';')

    # Header
    writer.writerow([
        'Passageiro', 'Endereço', 'Bairro', 'Cidade', 'UF',
        'Parada', 'Endereço Parada', 'Ordem',
        'Horário Parada', 'Distância Caminhada (m)', 'Tempo no Veículo (min)'
    ])

    for p in passageiros:
        parada_nome = ''
        parada_end = ''
        parada_ordem = ''
        parada_horario = ''

        if p.parada:
            parada_nome = p.parada.nome or ''
            parada_end = p.parada.endereco_referencia or ''
            parada_ordem = p.parada.ordem or ''
            if p.parada.horario_chegada:
                parada_horario = p.parada.horario_chegada.strftime('%H:%M')

        writer.writerow([
            p.nome,
            p.endereco_completo(),
            p.bairro or '',
            p.cidade or '',
            p.estado or '',
            parada_nome,
            parada_end,
            parada_ordem,
            parada_horario,
            round(p.distancia_ate_parada, 0) if p.distancia_ate_parada else '',
            p.tempo_no_veiculo or ''
        ])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        as_attachment=True,
        download_name=f'relatorio_{rot.nome.replace(" ", "_")}.csv',
        mimetype='text/csv'
    )


# ============================================
# EXCLUIR (soft delete)
# ============================================

@roteirizador_bp.route('/<int:id>/excluir', methods=['POST'])
@roteirizador_required
def excluir(id):
    rot = Roteirizacao.query.get_or_404(id)
    rot.ativo = False
    db.session.commit()
    flash('Roteirização excluída.', 'success')
    return redirect(url_for('roteirizador.lista'))


# ============================================
# SIMULAÇÕES
# ============================================

@roteirizador_bp.route('/<int:id>/simulacoes')
@roteirizador_required
def simulacoes(id):
    rot = Roteirizacao.query.get_or_404(id)
    sims = rot.simulacoes.order_by(Simulacao.criado_em.desc()).all()
    tipos_veiculo = TipoVeiculo.query.filter_by(ativo=True).order_by(TipoVeiculo.capacidade).all()
    return render_template('roteirizador/simulacoes.html', rot=rot, simulacoes=sims, tipos_veiculo=tipos_veiculo)


@roteirizador_bp.route('/<int:id>/simulacao/<int:sim_id>/aplicar', methods=['POST'])
@roteirizador_required
def aplicar_simulacao(id, sim_id):
    rot = Roteirizacao.query.get_or_404(id)
    sim = Simulacao.query.get_or_404(sim_id)

    if sim.roteirizacao_id != rot.id:
        flash('Simulação não pertence a esta roteirização.', 'danger')
        return redirect(url_for('roteirizador.visualizar', id=id))

    # Salvar estado atual antes de aplicar (se otimizado)
    if rot.status in ('otimizado', 'finalizado') and rot.total_rotas:
        _salvar_simulacao(rot)

    # Limpar dados atuais (roteiros + paradas de volta)
    # Primeiro deletar paradas de volta (são cópias, não originais)
    roteiros_volta_atuais = rot.roteiros.filter_by(tipo='volta').all()
    for rv in roteiros_volta_atuais:
        PontoParada.query.filter_by(roteiro_id=rv.id).delete()
    RoteiroPlanejado.query.filter_by(roteirizacao_id=id).delete()
    for p in rot.paradas.filter_by(ativo=True).all():
        p.roteiro_id = None
        p.ordem = None
        p.horario_chegada = None
        p.horario_partida = None

    # Restaurar parâmetros
    rot.distancia_maxima_caminhada = sim.distancia_maxima_caminhada
    rot.tempo_maximo_viagem = sim.tempo_maximo_viagem
    rot.horario_chegada = sim.horario_chegada
    rot.capacidade_veiculo = sim.capacidade_veiculo
    rot.total_rotas = sim.total_rotas
    rot.total_paradas = sim.total_paradas
    rot.distancia_total_km = sim.distancia_total_km
    rot.duracao_total_minutos = sim.duracao_total_minutos

    # Restaurar rotas e paradas do JSON
    dados = json.loads(sim.dados_json)

    roteiro_map = {}
    for rd in dados.get('roteiros', []):
        roteiro = RoteiroPlanejado(
            roteirizacao_id=id,
            nome=rd['nome'],
            ordem=rd['ordem'],
            tipo='ida',
            distancia_km=rd['distancia_km'],
            duracao_minutos=rd['duracao_minutos'],
            total_passageiros=rd['total_passageiros'],
            capacidade_veiculo=rd['capacidade_veiculo'],
            polyline_encoded=rd.get('polyline_encoded'),
        )
        if rd.get('horario_saida'):
            h, m = rd['horario_saida'].split(':')
            roteiro.horario_saida = time(int(h), int(m))
        if rd.get('horario_chegada_destino'):
            h, m = rd['horario_chegada_destino'].split(':')
            roteiro.horario_chegada_destino = time(int(h), int(m))
        db.session.add(roteiro)
        db.session.flush()
        roteiro_map[rd['nome']] = roteiro.id

    # Restaurar atribuições das paradas (ida)
    for pd in dados.get('paradas', []):
        parada = rot.paradas.filter_by(nome=pd['nome'], lat=pd['lat'], lng=pd['lng']).first()
        if parada and pd.get('roteiro_nome') in roteiro_map:
            parada.roteiro_id = roteiro_map[pd['roteiro_nome']]
            parada.ordem = pd['ordem']
            if pd.get('horario_chegada'):
                h, m = pd['horario_chegada'].split(':')
                parada.horario_chegada = time(int(h), int(m))
            if pd.get('horario_partida'):
                h, m = pd['horario_partida'].split(':')
                parada.horario_partida = time(int(h), int(m))

    # Restaurar rotas de volta (se existirem no JSON)
    volta_roteiro_map = {}
    for rd in dados.get('roteiros_volta', []):
        roteiro = RoteiroPlanejado(
            roteirizacao_id=id,
            nome=rd['nome'],
            ordem=rd['ordem'],
            tipo='volta',
            distancia_km=rd['distancia_km'],
            duracao_minutos=rd['duracao_minutos'],
            total_passageiros=rd['total_passageiros'],
            capacidade_veiculo=rd['capacidade_veiculo'],
            polyline_encoded=rd.get('polyline_encoded'),
        )
        if rd.get('horario_saida'):
            h, m = rd['horario_saida'].split(':')
            roteiro.horario_saida = time(int(h), int(m))
        db.session.add(roteiro)
        db.session.flush()
        volta_roteiro_map[rd['nome']] = roteiro.id

    # Restaurar paradas de volta (criar novas)
    for pd in dados.get('paradas_volta', []):
        if pd.get('roteiro_nome') in volta_roteiro_map:
            parada_volta = PontoParada(
                roteirizacao_id=id,
                roteiro_id=volta_roteiro_map[pd['roteiro_nome']],
                nome=pd['nome'],
                endereco_referencia=pd.get('endereco_referencia'),
                lat=pd['lat'],
                lng=pd['lng'],
                ordem=pd['ordem'],
                total_passageiros=pd.get('total_passageiros', 0),
            )
            if pd.get('horario_chegada'):
                h, m = pd['horario_chegada'].split(':')
                parada_volta.horario_chegada = time(int(h), int(m))
            if pd.get('horario_partida'):
                h, m = pd['horario_partida'].split(':')
                parada_volta.horario_partida = time(int(h), int(m))
            db.session.add(parada_volta)

    # Restaurar métricas de volta
    if dados.get('horario_saida_retorno'):
        h, m = dados['horario_saida_retorno'].split(':')
        rot.horario_saida_retorno = time(int(h), int(m))
    rot.total_rotas_volta = dados.get('total_rotas_volta')
    rot.distancia_total_km_volta = dados.get('distancia_total_km_volta')
    rot.duracao_total_minutos_volta = dados.get('duracao_total_minutos_volta')

    rot.status = 'otimizado'
    db.session.commit()

    flash(f'Simulação "{sim.nome}" aplicada com sucesso!', 'success')
    return redirect(url_for('roteirizador.visualizar', id=id))


@roteirizador_bp.route('/<int:id>/simulacao/<int:sim_id>/excluir', methods=['POST'])
@roteirizador_required
def excluir_simulacao(id, sim_id):
    sim = Simulacao.query.get_or_404(sim_id)
    if sim.roteirizacao_id != id:
        flash('Simulação não pertence a esta roteirização.', 'danger')
        return redirect(url_for('roteirizador.visualizar', id=id))

    db.session.delete(sim)
    db.session.commit()
    flash(f'Simulação "{sim.nome}" excluída.', 'success')
    return redirect(url_for('roteirizador.visualizar', id=id))
