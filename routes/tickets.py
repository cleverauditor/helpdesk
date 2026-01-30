import os
import uuid
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, send_from_directory
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from models import db, Ticket, TicketHistory, Category, User, Attachment
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
    status = request.args.get('status')
    prioridade = request.args.get('prioridade')
    atendente_id = request.args.get('atendente_id', type=int)
    categoria_id = request.args.get('categoria_id', type=int)
    busca = request.args.get('busca', '').strip()

    if status:
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

    # Ordenar por SLA (mais urgentes primeiro), NULL por último
    tickets = query.order_by(
        Ticket.sla_resolucao_limite.asc().nullslast()
    ).paginate(
        page=page, per_page=per_page, error_out=False
    )

    atendentes = User.query.filter(User.tipo.in_(['admin', 'atendente']), User.ativo == True).all()
    categorias = Category.query.filter_by(ativo=True).all()

    return render_template('tickets/list.html',
                          tickets=tickets,
                          atendentes=atendentes,
                          categorias=categorias)


@tickets_bp.route('/criar', methods=['GET', 'POST'])
@login_required
def criar():
    if request.method == 'POST':
        titulo = request.form.get('titulo', '').strip()
        descricao = request.form.get('descricao', '').strip()
        prioridade = request.form.get('prioridade', 'media')
        categoria_id = request.form.get('categoria_id', type=int)

        # Validações
        if not titulo or len(titulo) < 5:
            flash('Título deve ter pelo menos 5 caracteres.', 'danger')
            return render_template('tickets/create.html',
                                  categorias=Category.query.filter_by(ativo=True).all())

        if not descricao or len(descricao) < 10:
            flash('Descrição deve ter pelo menos 10 caracteres.', 'danger')
            return render_template('tickets/create.html',
                                  categorias=Category.query.filter_by(ativo=True).all())

        ticket = Ticket(
            titulo=titulo,
            descricao=descricao,
            prioridade=prioridade,
            categoria_id=categoria_id if categoria_id else None,
            cliente_id=current_user.id,
            criado_em=datetime.now()
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

    categorias = Category.query.filter_by(ativo=True).all()
    return render_template('tickets/create.html', categorias=categorias)


@tickets_bp.route('/<int:id>')
@login_required
def visualizar(id):
    ticket = Ticket.query.get_or_404(id)

    # Verificar permissão
    if current_user.is_cliente() and ticket.cliente_id != current_user.id:
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

                if new_status == 'resolvido' and not ticket.resolvido_em:
                    ticket.resolvido_em = datetime.now()
                elif new_status == 'fechado' and not ticket.fechado_em:
                    ticket.fechado_em = datetime.now()

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
            ticket.primeira_resposta_em = datetime.now()

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

    # Upload de anexos no comentário
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

    flash('Comentário adicionado!', 'success')
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

    if new_status in ['aberto', 'em_andamento', 'aguardando', 'resolvido', 'fechado']:
        ticket.status = new_status

        if new_status == 'resolvido' and not ticket.resolvido_em:
            ticket.resolvido_em = datetime.now()
        elif new_status == 'fechado' and not ticket.fechado_em:
            ticket.fechado_em = datetime.now()

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
