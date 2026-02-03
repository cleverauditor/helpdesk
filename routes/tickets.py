import os
import uuid
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, send_from_directory
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from models import db, Ticket, TicketHistory, Category, User, Attachment, agora_brasil
from email_service import notify_new_ticket, notify_ticket_assigned, notify_status_update

tickets_bp = Blueprint('tickets', __name__, url_prefix='/tickets')


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']


@tickets_bp.route('/')
@login_required
def lista():
    page = request.args.get('page', 1, type=int)
    per_page = 15

    query = Ticket.query

    # Filtros
    status = request.args.get('status', 'ativos')  # Padrão: apenas ativos
    prioridade = request.args.get('prioridade')
    atendente_id = request.args.get('atendente_id', type=int)
    categoria_id = request.args.get('categoria_id', type=int)
    busca = request.args.get('busca', '').strip()

    if status == 'ativos':
        query = query.filter(Ticket.status.in_(['aberto', 'em_andamento']))
    elif status and status != 'todos':
        query = query.filter(Ticket.status == status)
    if prioridade:
        query = query.filter(Ticket.prioridade == prioridade)
    if atendente_id:
        query = query.filter(Ticket.atendente_id == atendente_id)
    if categoria_id:
        query = query.filter(Ticket.categoria_id == categoria_id)
    if busca:
        query = query.filter(
            db.or_(
                Ticket.titulo.ilike(f'%{busca}%'),
                Ticket.descricao.ilike(f'%{busca}%'),
                Ticket.id == busca if busca.isdigit() else False
            )
        )

    # Clientes só veem seus próprios chamados
    if current_user.is_cliente():
        query = query.filter(Ticket.cliente_id == current_user.id)
    # Atendentes (não admin) só veem chamados das suas categorias
    elif current_user.tipo == 'atendente':
        categorias_ids = current_user.get_categorias_ids()
        if categorias_ids:
            # Filtrar por categorias atribuídas ou tickets sem categoria
            query = query.filter(
                db.or_(
                    Ticket.categoria_id.in_(categorias_ids),
                    Ticket.categoria_id.is_(None)
                )
            )

    # Ordenar por SLA (mais urgentes primeiro), NULL por último
    # Usando COALESCE para compatibilidade com MySQL (não suporta NULLS LAST)
    from sqlalchemy import case
    tickets = query.order_by(
        case((Ticket.sla_resolucao_limite.is_(None), 1), else_=0),
        Ticket.sla_resolucao_limite.asc()
    ).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Filtros disponíveis baseados no tipo de usuário
    if current_user.is_admin():
        atendentes = User.query.filter(User.tipo.in_(['admin', 'atendente']), User.ativo == True).all()
        categorias = Category.query.filter_by(ativo=True).all()
    elif current_user.tipo == 'atendente':
        # Atendente vê apenas ele mesmo no filtro
        atendentes = [current_user]
        # Apenas suas categorias
        categorias_ids = current_user.get_categorias_ids()
        if categorias_ids:
            categorias = Category.query.filter(Category.id.in_(categorias_ids), Category.ativo == True).all()
        else:
            categorias = Category.query.filter_by(ativo=True).all()
    else:
        # Cliente não tem filtros avançados
        atendentes = []
        categorias = []

    return render_template('tickets/list.html',
                          tickets=tickets,
                          atendentes=atendentes,
                          categorias=categorias)


@tickets_bp.route('/criar', methods=['GET', 'POST'])
@login_required
def criar():
    # Filtrar categorias baseado no usuário
    if current_user.is_cliente() and current_user.categorias.count() > 0:
        categorias_permitidas = current_user.categorias.filter_by(ativo=True).all()
    else:
        categorias_permitidas = Category.query.filter_by(ativo=True).all()

    if request.method == 'POST':
        titulo = request.form.get('titulo', '').strip()
        descricao = request.form.get('descricao', '').strip()
        prioridade = request.form.get('prioridade', 'media')
        categoria_id = request.form.get('categoria_id', type=int)

        # Validações
        if not titulo or len(titulo) < 5:
            flash('Título deve ter pelo menos 5 caracteres.', 'danger')
            return render_template('tickets/create.html', categorias=categorias_permitidas)

        if not descricao or len(descricao) < 10:
            flash('Descrição deve ter pelo menos 10 caracteres.', 'danger')
            return render_template('tickets/create.html', categorias=categorias_permitidas)

        # Validar se cliente pode usar esta categoria
        if categoria_id and current_user.is_cliente() and current_user.categorias.count() > 0:
            if not current_user.categorias.filter_by(id=categoria_id).first():
                flash('Categoria não permitida.', 'danger')
                return render_template('tickets/create.html', categorias=categorias_permitidas)

        ticket = Ticket(
            titulo=titulo,
            descricao=descricao,
            prioridade=prioridade,
            categoria_id=categoria_id if categoria_id else None,
            cliente_id=current_user.id,
            criado_em=agora_brasil()
        )
        ticket.calcular_sla()

        db.session.add(ticket)
        db.session.flush()

        # Histórico
        historico = TicketHistory(
            ticket_id=ticket.id,
            usuario_id=current_user.id,
            acao='criado',
            descricao='Chamado criado'
        )
        db.session.add(historico)

        # Upload de anexos
        if 'anexos' in request.files:
            files = request.files.getlist('anexos')
            for file in files:
                if file and file.filename and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    unique_filename = f"{uuid.uuid4().hex}_{filename}"
                    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
                    file.save(filepath)

                    anexo = Attachment(
                        ticket_id=ticket.id,
                        usuario_id=current_user.id,
                        nome_arquivo=filename,
                        caminho=unique_filename,
                        tamanho=os.path.getsize(filepath),
                        tipo_mime=file.content_type
                    )
                    db.session.add(anexo)

        db.session.commit()

        # Notificação
        notify_new_ticket(ticket)

        flash(f'Chamado #{ticket.id} criado com sucesso!', 'success')
        return redirect(url_for('tickets.visualizar', id=ticket.id))

    return render_template('tickets/create.html', categorias=categorias_permitidas)


@tickets_bp.route('/<int:id>')
@login_required
def visualizar(id):
    ticket = Ticket.query.get_or_404(id)

    # Verificar permissão
    if current_user.is_cliente() and ticket.cliente_id != current_user.id:
        flash('Você não tem permissão para visualizar este chamado.', 'danger')
        return redirect(url_for('tickets.lista'))

    # Atendentes só podem ver chamados das suas categorias
    if current_user.tipo == 'atendente' and ticket.categoria_id:
        if not current_user.pode_ver_categoria(ticket.categoria_id):
            flash('Você não tem permissão para visualizar este chamado.', 'danger')
            return redirect(url_for('tickets.lista'))

    atendentes = User.query.filter(User.tipo.in_(['admin', 'atendente']), User.ativo == True).all()
    return render_template('tickets/view.html', ticket=ticket, atendentes=atendentes)


@tickets_bp.route('/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar(id):
    ticket = Ticket.query.get_or_404(id)

    # Verificar permissão
    if not current_user.is_atendente() and ticket.cliente_id != current_user.id:
        flash('Você não tem permissão para editar este chamado.', 'danger')
        return redirect(url_for('tickets.lista'))

    if request.method == 'POST':
        old_status = ticket.status

        ticket.titulo = request.form.get('titulo', '').strip()
        ticket.descricao = request.form.get('descricao', '').strip()

        if current_user.is_atendente():
            ticket.prioridade = request.form.get('prioridade', ticket.prioridade)
            ticket.categoria_id = request.form.get('categoria_id', type=int) or ticket.categoria_id

            new_status = request.form.get('status', ticket.status)
            if new_status != old_status:
                ticket.status = new_status

                if new_status == 'fechado' and not ticket.fechado_em:
                    ticket.fechado_em = agora_brasil()

                # Notificar cliente
                notify_status_update(ticket, old_status)

        # Recalcular SLA se prioridade mudou
        ticket.calcular_sla()

        # Histórico
        historico = TicketHistory(
            ticket_id=ticket.id,
            usuario_id=current_user.id,
            acao='editado',
            descricao=f'Chamado editado. Status: {old_status} -> {ticket.status}'
        )
        db.session.add(historico)
        db.session.commit()

        flash('Chamado atualizado com sucesso!', 'success')
        return redirect(url_for('tickets.visualizar', id=ticket.id))

    categorias = Category.query.filter_by(ativo=True).all()
    return render_template('tickets/edit.html', ticket=ticket, categorias=categorias)


@tickets_bp.route('/<int:id>/atribuir', methods=['POST'])
@login_required
def atribuir(id):
    if not current_user.is_atendente():
        flash('Você não tem permissão para atribuir chamados.', 'danger')
        return redirect(url_for('tickets.lista'))

    ticket = Ticket.query.get_or_404(id)
    atendente_id = request.form.get('atendente_id', type=int)

    old_atendente = ticket.atendente

    if atendente_id:
        atendente = User.query.get(atendente_id)
        if not atendente:
            flash('Atendente não encontrado.', 'danger')
            return redirect(url_for('tickets.visualizar', id=ticket.id))

        ticket.atendente_id = atendente_id
        if ticket.status == 'aberto':
            ticket.status = 'em_andamento'

        # Primeira resposta
        if not ticket.primeira_resposta_em:
            ticket.primeira_resposta_em = agora_brasil()

        # Histórico
        historico = TicketHistory(
            ticket_id=ticket.id,
            usuario_id=current_user.id,
            acao='atribuido',
            descricao=f'Atribuído para {atendente.nome}'
        )
        db.session.add(historico)
        db.session.commit()

        # Notificar atendente
        notify_ticket_assigned(ticket)

        flash(f'Chamado atribuído para {atendente.nome}.', 'success')
    else:
        ticket.atendente_id = None
        db.session.commit()
        flash('Atribuição removida.', 'info')

    return redirect(url_for('tickets.visualizar', id=ticket.id))


@tickets_bp.route('/<int:id>/comentar', methods=['POST'])
@login_required
def comentar(id):
    ticket = Ticket.query.get_or_404(id)

    # Verificar permissão
    if current_user.is_cliente() and ticket.cliente_id != current_user.id:
        flash('Você não tem permissão para comentar neste chamado.', 'danger')
        return redirect(url_for('tickets.lista'))

    comentario = request.form.get('comentario', '').strip()
    tempo_gasto = request.form.get('tempo_gasto', 0, type=int)

    if not comentario:
        flash('Comentário não pode estar vazio.', 'danger')
        return redirect(url_for('tickets.visualizar', id=ticket.id))

    historico = TicketHistory(
        ticket_id=ticket.id,
        usuario_id=current_user.id,
        acao='comentario',
        descricao=comentario,
        tempo_gasto_minutos=tempo_gasto if current_user.is_atendente() else 0
    )
    db.session.add(historico)
    db.session.commit()

    flash('Comentário adicionado!', 'success')
    return redirect(url_for('tickets.visualizar', id=ticket.id))


@tickets_bp.route('/<int:id>/anexar', methods=['POST'])
@login_required
def anexar(id):
    ticket = Ticket.query.get_or_404(id)

    # Verificar permissão
    if current_user.is_cliente() and ticket.cliente_id != current_user.id:
        flash('Você não tem permissão para anexar arquivos neste chamado.', 'danger')
        return redirect(url_for('tickets.lista'))

    if 'anexos' not in request.files:
        flash('Nenhum arquivo selecionado.', 'danger')
        return redirect(url_for('tickets.visualizar', id=ticket.id))

    files = request.files.getlist('anexos')
    arquivos_anexados = []

    for file in files:
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            unique_filename = f"{uuid.uuid4().hex}_{filename}"
            filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(filepath)

            anexo = Attachment(
                ticket_id=ticket.id,
                usuario_id=current_user.id,
                nome_arquivo=filename,
                caminho=unique_filename,
                tamanho=os.path.getsize(filepath),
                tipo_mime=file.content_type
            )
            db.session.add(anexo)
            arquivos_anexados.append(filename)

    if not arquivos_anexados:
        flash('Nenhum arquivo válido foi enviado.', 'danger')
        return redirect(url_for('tickets.visualizar', id=ticket.id))

    # Registrar no histórico
    historico = TicketHistory(
        ticket_id=ticket.id,
        usuario_id=current_user.id,
        acao='anexo',
        descricao=f"Arquivo(s) anexado(s): {', '.join(arquivos_anexados)}"
    )
    db.session.add(historico)
    db.session.commit()

    flash(f'{len(arquivos_anexados)} arquivo(s) anexado(s) com sucesso!', 'success')
    return redirect(url_for('tickets.visualizar', id=ticket.id))


@tickets_bp.route('/<int:id>/status', methods=['POST'])
@login_required
def alterar_status(id):
    ticket = Ticket.query.get_or_404(id)

    if not current_user.is_atendente():
        flash('Você não tem permissão para alterar o status.', 'danger')
        return redirect(url_for('tickets.visualizar', id=ticket.id))

    old_status = ticket.status
    new_status = request.form.get('status')

    if new_status in ['aberto', 'em_andamento', 'fechado']:
        ticket.status = new_status

        if new_status == 'fechado' and not ticket.fechado_em:
            ticket.fechado_em = agora_brasil()

        historico = TicketHistory(
            ticket_id=ticket.id,
            usuario_id=current_user.id,
            acao='status_alterado',
            descricao=f'Status alterado de {old_status} para {new_status}'
        )
        db.session.add(historico)
        db.session.commit()

        # Notificar cliente
        notify_status_update(ticket, old_status)

        flash(f'Status alterado para {new_status}.', 'success')

    return redirect(url_for('tickets.visualizar', id=ticket.id))


@tickets_bp.route('/anexo/<int:id>')
@login_required
def download_anexo(id):
    anexo = Attachment.query.get_or_404(id)
    ticket = anexo.ticket

    # Verificar permissão
    if current_user.is_cliente() and ticket.cliente_id != current_user.id:
        flash('Você não tem permissão para acessar este arquivo.', 'danger')
        return redirect(url_for('tickets.lista'))

    return send_from_directory(
        current_app.config['UPLOAD_FOLDER'],
        anexo.caminho,
        download_name=anexo.nome_arquivo
    )
