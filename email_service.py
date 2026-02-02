import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import current_app, render_template_string
import threading

def log_email(msg):
    """Escreve log em arquivo para debug"""
    log_path = os.path.join(os.path.dirname(__file__), 'email_debug.log')
    with open(log_path, 'a') as f:
        from datetime import datetime
        f.write(f'{datetime.now()} - {msg}\n')


def send_email_async(app, msg, recipients):
    """Envia email de forma assíncrona"""
    with app.app_context():
        try:
            log_email(f'Enviando para: {recipients}')
            log_email(f'Servidor: {current_app.config["MAIL_SERVER"]}')
            log_email(f'Usuario: {current_app.config["MAIL_USERNAME"]}')
            with smtplib.SMTP(current_app.config['MAIL_SERVER'],
                            current_app.config['MAIL_PORT']) as server:
                server.starttls()
                if current_app.config['MAIL_USERNAME']:
                    server.login(current_app.config['MAIL_USERNAME'],
                               current_app.config['MAIL_PASSWORD'])
                server.send_message(msg)
                log_email('Enviado com sucesso!')
        except Exception as e:
            log_email(f'ERRO: {e}')
            current_app.logger.error(f'Erro ao enviar email: {e}')


def send_email(subject, recipients, html_body, text_body=None):
    """Envia email"""
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = current_app.config['MAIL_DEFAULT_SENDER']
        msg['To'] = ', '.join(recipients) if isinstance(recipients, list) else recipients

        if text_body:
            msg.attach(MIMEText(text_body, 'plain'))
        msg.attach(MIMEText(html_body, 'html'))

        # Envia em thread separada para não bloquear
        thread = threading.Thread(
            target=send_email_async,
            args=(current_app._get_current_object(), msg, recipients)
        )
        thread.start()
        return True
    except Exception as e:
        current_app.logger.error(f'Erro ao preparar email: {e}')
        return False


def notify_new_ticket(ticket):
    """Notifica atendentes sobre novo chamado"""
    from models import User
    atendentes = User.query.filter(
        User.tipo.in_(['admin', 'atendente']),
        User.ativo == True
    ).all()

    if not atendentes:
        return

    recipients = [a.email for a in atendentes]
    subject = f'[Atendimento MaxVia] Novo Chamado #{ticket.id} - {ticket.titulo}'

    html_body = f'''
    <h2>Novo Chamado Aberto</h2>
    <p><strong>Chamado:</strong> #{ticket.id}</p>
    <p><strong>Título:</strong> {ticket.titulo}</p>
    <p><strong>Prioridade:</strong> {ticket.prioridade.upper()}</p>
    <p><strong>Cliente:</strong> {ticket.cliente.nome}</p>
    <p><strong>Descrição:</strong></p>
    <p>{ticket.descricao}</p>
    <hr>
    <p>Acesse o sistema para atender este chamado.</p>
    '''

    send_email(subject, recipients, html_body)


def notify_ticket_assigned(ticket):
    """Notifica atendente que foi atribuído ao chamado"""
    if not ticket.atendente:
        return

    subject = f'[Atendimento MaxVia] Chamado #{ticket.id} atribuído a você'

    html_body = f'''
    <h2>Chamado Atribuído</h2>
    <p>O chamado #{ticket.id} foi atribuído a você.</p>
    <p><strong>Título:</strong> {ticket.titulo}</p>
    <p><strong>Prioridade:</strong> {ticket.prioridade.upper()}</p>
    <p><strong>Cliente:</strong> {ticket.cliente.nome}</p>
    <p><strong>SLA Resolução:</strong> {ticket.sla_resolucao_limite.strftime('%d/%m/%Y %H:%M')}</p>
    <hr>
    <p>Acesse o sistema para atender este chamado.</p>
    '''

    send_email(subject, [ticket.atendente.email], html_body)


def notify_status_update(ticket, old_status):
    """Notifica cliente sobre atualização de status"""
    log_email(f'notify_status_update chamado - Ticket #{ticket.id}, Cliente: {ticket.cliente.email}')
    subject = f'[Atendimento MaxVia] Chamado #{ticket.id} - Status Atualizado'

    html_body = f'''
    <h2>Atualização do Chamado</h2>
    <p>O status do seu chamado foi atualizado.</p>
    <p><strong>Chamado:</strong> #{ticket.id} - {ticket.titulo}</p>
    <p><strong>Status Anterior:</strong> {old_status}</p>
    <p><strong>Novo Status:</strong> {ticket.status}</p>
    <hr>
    <p>Acesse o sistema para mais detalhes.</p>
    '''

    send_email(subject, [ticket.cliente.email], html_body)


def notify_sla_warning(ticket):
    """Alerta sobre SLA próximo de vencer"""
    if not ticket.atendente:
        return

    subject = f'[ALERTA] Chamado #{ticket.id} - SLA próximo de vencer!'

    html_body = f'''
    <h2 style="color: #dc3545;">⚠️ Alerta de SLA</h2>
    <p>O SLA do chamado está próximo de vencer!</p>
    <p><strong>Chamado:</strong> #{ticket.id} - {ticket.titulo}</p>
    <p><strong>Prioridade:</strong> {ticket.prioridade.upper()}</p>
    <p><strong>SLA Resolução:</strong> {ticket.sla_resolucao_limite.strftime('%d/%m/%Y %H:%M')}</p>
    <hr>
    <p>Por favor, priorize o atendimento deste chamado.</p>
    '''

    send_email(subject, [ticket.atendente.email], html_body)
