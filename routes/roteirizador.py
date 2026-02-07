# ============================================
# ROTEIRIZADOR INTELIGENTE - ROTAS (Blueprint)
# ============================================

import os
import uuid
import csv
import io
import json
from functools import wraps
from datetime import datetime, time

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, current_app, send_file, jsonify
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import (
    db, Category, Roteirizacao, Passageiro, PontoParada, RoteiroPlanejado, Cliente, TipoVeiculo, Simulacao
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
            usuario_id=current_user.id
        )

        # Processar arquivo de passageiros
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
    roteiros = rot.roteiros.filter_by(ativo=True).order_by(RoteiroPlanejado.ordem).all()

    # Contagens de geocode
    total_geo = sum(1 for p in passageiros if p.geocode_status == 'sucesso')
    total_falha = sum(1 for p in passageiros if p.geocode_status == 'falha')
    total_pendente = sum(1 for p in passageiros if p.geocode_status == 'pendente')

    api_key = current_app.config['GOOGLE_MAPS_API_KEY']
    tipos_veiculo = TipoVeiculo.query.filter_by(ativo=True).order_by(TipoVeiculo.capacidade).all()
    simulacoes = rot.simulacoes.order_by(Simulacao.criado_em.desc()).all()

    return render_template('roteirizador/view.html',
                           rot=rot,
                           passageiros=passageiros,
                           paradas=paradas,
                           roteiros=roteiros,
                           total_geo=total_geo,
                           total_falha=total_falha,
                           total_pendente=total_pendente,
                           api_key=api_key,
                           tipos_veiculo=tipos_veiculo,
                           simulacoes=simulacoes)


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
    rutils.init_api_key(current_app.config['GOOGLE_MAPS_API_KEY'])

    # Resetar atribuições dos passageiros (antes de deletar paradas)
    for p in rot.passageiros.filter_by(ativo=True).all():
        p.parada_id = None
        p.distancia_ate_parada = None
        p.tempo_no_veiculo = None
    db.session.flush()
    # Limpar paradas e roteiros anteriores
    PontoParada.query.filter_by(roteirizacao_id=id).delete()
    RoteiroPlanejado.query.filter_by(roteirizacao_id=id).delete()

    # Pegar passageiros geocodificados
    passageiros = rot.passageiros.filter(
        Passageiro.ativo == True,
        Passageiro.geocode_status.in_(['sucesso', 'manual']),
        Passageiro.lat.isnot(None)
    ).all()

    if not passageiros:
        flash('Nenhum passageiro geocodificado para agrupar.', 'danger')
        return redirect(url_for('roteirizador.visualizar', id=id))

    # Preparar dados
    dados = [{'id': p.id, 'lat': p.lat, 'lng': p.lng} for p in passageiros]

    # Clusterizar
    clusters = rutils.clusterizar_passageiros(dados, rot.distancia_maxima_caminhada, rot.destino_lat, rot.destino_lng)

    # Criar pontos de parada
    for i, cluster in enumerate(clusters, start=1):
        # Reverse geocode para referência
        endereco_ref = rutils.reverse_geocode(cluster['centroid_lat'], cluster['centroid_lng'])

        parada = PontoParada(
            roteirizacao_id=id,
            nome=f'Parada {i}',
            endereco_referencia=endereco_ref,
            lat=cluster['centroid_lat'],
            lng=cluster['centroid_lng'],
            total_passageiros=len(cluster['passageiro_ids'])
        )
        db.session.add(parada)
        db.session.flush()

        # Atribuir passageiros à parada
        for pid in cluster['passageiro_ids']:
            p = Passageiro.query.get(pid)
            if p:
                p.parada_id = parada.id
                p.distancia_ate_parada = cluster['distancias'].get(pid, 0)

    rot.total_paradas = len(clusters)
    rot.status = 'clusterizado'
    db.session.commit()

    flash(f'{len(clusters)} pontos de parada criados.', 'success')
    return redirect(url_for('roteirizador.visualizar', id=id))


# ============================================
# OTIMIZAR ROTA
# ============================================

@roteirizador_bp.route('/<int:id>/otimizar', methods=['POST'])
@roteirizador_required
def otimizar(id):
    rot = Roteirizacao.query.get_or_404(id)
    rutils.init_api_key(current_app.config['GOOGLE_MAPS_API_KEY'])

    paradas = rot.paradas.filter_by(ativo=True).all()

    if not paradas:
        flash('Nenhum ponto de parada. Execute a clusterização primeiro.', 'danger')
        return redirect(url_for('roteirizador.visualizar', id=id))

    # Limpar roteiros anteriores
    RoteiroPlanejado.query.filter_by(roteirizacao_id=id).delete()
    for p in paradas:
        p.roteiro_id = None
        p.ordem = None
        p.horario_chegada = None
        p.horario_partida = None

    # Dividir por capacidade se necessário
    clusters_data = []
    for p in paradas:
        clusters_data.append({
            'id': p.id,
            'lat': p.lat,
            'lng': p.lng,
            'centroid_lat': p.lat,
            'centroid_lng': p.lng,
            'passageiro_ids': [px.id for px in p.passageiros.filter_by(ativo=True).all()]
        })

    sub_rotas_capacidade = rutils.dividir_rotas_por_capacidade(clusters_data, rot.capacidade_veiculo)

    # Para cada grupo de capacidade, otimizar e verificar tempo
    sub_rotas_finais = []
    for grupo_clusters in sub_rotas_capacidade:
        paradas_opt = [{'id': c['id'], 'lat': c['lat'], 'lng': c['lng']} for c in grupo_clusters]
        resultado = rutils.otimizar_rota_google(paradas_opt, rot.destino_lat, rot.destino_lng)

        if not resultado:
            sub_rotas_finais.append((grupo_clusters, None))
            continue

        # Dividir por tempo máximo se necessário
        sub_tempo = rutils.dividir_rotas_por_tempo(
            grupo_clusters, resultado, rot.tempo_maximo_viagem,
            rot.destino_lat, rot.destino_lng
        )
        sub_rotas_finais.extend(sub_tempo)

    total_dist = 0
    max_dur = 0
    duracoes_rotas = []
    num_roteiros = 0
    ordem_global = 0  # Numeração sequencial global de paradas (1, 2, 3, 4, 5...)

    for r_idx, (grupo_clusters, resultado) in enumerate(sub_rotas_finais, start=1):
        if not resultado:
            flash(f'Erro ao otimizar rota {r_idx}. Verifique a chave da API.', 'danger')
            continue

        # Criar roteiro planejado
        dwell = current_app.config.get('ROTEIRIZADOR_DWELL_TIME', 60)
        schedule = rutils.calcular_horarios(resultado['legs'], rot.horario_chegada, dwell)

        roteiro = RoteiroPlanejado(
            roteirizacao_id=id,
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

        # Atribuir paradas ao roteiro com ordem otimizada
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

    # Duração = maior rota individual (rotas rodam em paralelo com veículos diferentes)
    max_dur = max(duracoes_rotas) if duracoes_rotas else 0

    rot.total_rotas = num_roteiros
    rot.distancia_total_km = round(total_dist, 2)
    rot.duracao_total_minutos = round(max_dur)
    rot.status = 'otimizado'
    db.session.commit()

    msg_tempo = ''
    if num_roteiros > 1:
        msg_tempo = f' (dividido em {num_roteiros} rotas para respeitar tempo máximo de {rot.tempo_maximo_viagem} min)'
    flash(f'Otimização concluída: {num_roteiros} rota(s), {round(total_dist, 1)} km total.{msg_tempo}', 'success')
    return redirect(url_for('roteirizador.visualizar', id=id))


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
    roteiros = rot.roteiros.filter_by(ativo=True).order_by(RoteiroPlanejado.ordem).all()
    paradas = rot.paradas.filter_by(ativo=True).order_by(PontoParada.roteiro_id, PontoParada.ordem).all()

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
        } for r in roteiros],
        'paradas': [{
            'nome': p.nome,
            'endereco_referencia': p.endereco_referencia,
            'lat': p.lat,
            'lng': p.lng,
            'ordem': p.ordem,
            'roteiro_nome': next((r.nome for r in roteiros if r.id == p.roteiro_id), None),
            'total_passageiros': p.total_passageiros,
            'horario_chegada': p.horario_chegada.strftime('%H:%M') if p.horario_chegada else None,
            'horario_partida': p.horario_partida.strftime('%H:%M') if p.horario_partida else None,
        } for p in paradas],
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

    roteiros = rot.roteiros.filter_by(ativo=True).order_by(RoteiroPlanejado.ordem).all()
    paradas = rot.paradas.filter_by(ativo=True).order_by(PontoParada.roteiro_id, PontoParada.ordem).all()
    passageiros = rot.passageiros.filter(
        Passageiro.ativo == True,
        Passageiro.lat.isnot(None)
    ).all()

    api_key = current_app.config['GOOGLE_MAPS_API_KEY']

    return render_template('roteirizador/editar_mapa.html',
                           rot=rot,
                           roteiros=roteiros,
                           paradas=paradas,
                           passageiros=passageiros,
                           api_key=api_key)


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

    # Recalcular horários com os novos legs
    dwell = current_app.config.get('ROTEIRIZADOR_DWELL_TIME', 60)
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
# RELATÓRIO
# ============================================

@roteirizador_bp.route('/<int:id>/relatorio')
@roteirizador_required
def relatorio(id):
    rot = Roteirizacao.query.get_or_404(id)

    roteiros = rot.roteiros.filter_by(ativo=True).order_by(RoteiroPlanejado.ordem).all()
    paradas = rot.paradas.filter_by(ativo=True).order_by(PontoParada.roteiro_id, PontoParada.ordem).all()
    passageiros = rot.passageiros.filter_by(ativo=True).order_by(Passageiro.nome).all()

    api_key = current_app.config['GOOGLE_MAPS_API_KEY']
    tipos_veiculo = TipoVeiculo.query.filter_by(ativo=True).order_by(TipoVeiculo.capacidade).all()

    return render_template('roteirizador/relatorio.html',
                           rot=rot,
                           roteiros=roteiros,
                           paradas=paradas,
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

    # Limpar dados atuais
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

    # Restaurar atribuições das paradas
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
