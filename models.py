from datetime import datetime, timedelta, time
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# Configuração de horário administrativo
HORA_INICIO = time(8, 0)   # 08:00
HORA_FIM = time(17, 0)     # 17:00
HORAS_POR_DIA = 9          # 9 horas úteis por dia


def eh_dia_util(dt):
    """Verifica se é dia útil (segunda a sexta)"""
    return dt.weekday() < 5  # 0=Segunda, 4=Sexta


def eh_horario_util(dt):
    """Verifica se está dentro do horário administrativo"""
    return eh_dia_util(dt) and HORA_INICIO <= dt.time() < HORA_FIM


def proximo_inicio_expediente(dt):
    """Retorna o próximo início de expediente a partir de dt"""
    # Se for fim de semana, avança para segunda
    while not eh_dia_util(dt):
        dt = dt + timedelta(days=1)
    return dt.replace(hour=HORA_INICIO.hour, minute=HORA_INICIO.minute, second=0, microsecond=0)


def adicionar_horas_uteis(dt, horas):
    """
    Adiciona horas úteis a uma data/hora considerando horário administrativo.
    Horário: 08:00 às 17:00 (seg-sex)
    """
    if horas <= 0:
        return dt

    # Se estiver fora do expediente, ajustar para próximo início
    if not eh_dia_util(dt):
        dt = proximo_inicio_expediente(dt)
    elif dt.time() < HORA_INICIO:
        dt = dt.replace(hour=HORA_INICIO.hour, minute=HORA_INICIO.minute, second=0, microsecond=0)
    elif dt.time() >= HORA_FIM:
        dt = proximo_inicio_expediente(dt + timedelta(days=1))

    horas_restantes = horas

    while horas_restantes > 0:
        # Calcular horas disponíveis até o fim do expediente
        fim_expediente = dt.replace(hour=HORA_FIM.hour, minute=HORA_FIM.minute, second=0, microsecond=0)
        horas_ate_fim = (fim_expediente - dt).total_seconds() / 3600

        if horas_restantes <= horas_ate_fim:
            # Cabe no dia atual
            dt = dt + timedelta(hours=horas_restantes)
            horas_restantes = 0
        else:
            # Consome o resto do dia e avança
            horas_restantes -= horas_ate_fim
            dt = proximo_inicio_expediente(dt + timedelta(days=1))

    return dt


def calcular_horas_uteis_entre(dt_inicio, dt_fim):
    """
    Calcula a quantidade de horas úteis entre duas datas.
    Retorna valor negativo se dt_fim < dt_inicio (SLA violado).
    """
    if dt_fim <= dt_inicio:
        # Calcular quanto tempo passou além do limite
        return -calcular_horas_uteis_entre(dt_fim, dt_inicio)

    horas = 0
    dt = dt_inicio

    # Ajustar início se fora do expediente
    if not eh_dia_util(dt):
        dt = proximo_inicio_expediente(dt)
    elif dt.time() < HORA_INICIO:
        dt = dt.replace(hour=HORA_INICIO.hour, minute=HORA_INICIO.minute, second=0, microsecond=0)
    elif dt.time() >= HORA_FIM:
        dt = proximo_inicio_expediente(dt + timedelta(days=1))

    while dt < dt_fim:
        if not eh_dia_util(dt):
            dt = proximo_inicio_expediente(dt)
            continue

        fim_expediente = dt.replace(hour=HORA_FIM.hour, minute=HORA_FIM.minute, second=0, microsecond=0)

        if dt_fim <= fim_expediente:
            # Termina no mesmo dia
            horas += (dt_fim - dt).total_seconds() / 3600
            break
        else:
            # Conta até o fim do expediente e avança para o próximo dia
            horas += (fim_expediente - dt).total_seconds() / 3600
            dt = proximo_inicio_expediente(dt + timedelta(days=1))

    return horas

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    senha_hash = db.Column(db.String(256), nullable=False)
    tipo = db.Column(db.String(20), nullable=False, default='cliente_externo')
    # tipos: admin, atendente, cliente_interno, cliente_externo
    empresa = db.Column(db.String(150))  # Para clientes externos
    departamento = db.Column(db.String(100))
    telefone = db.Column(db.String(20))
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.now)

    # Relacionamentos
    tickets_criados = db.relationship('Ticket', backref='cliente', lazy='dynamic',
                                       foreign_keys='Ticket.cliente_id')
    tickets_atendidos = db.relationship('Ticket', backref='atendente', lazy='dynamic',
                                         foreign_keys='Ticket.atendente_id')
    historicos = db.relationship('TicketHistory', backref='usuario', lazy='dynamic')

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

    def is_admin(self):
        return self.tipo == 'admin'

    def is_atendente(self):
        return self.tipo in ['admin', 'atendente']

    def is_cliente(self):
        return self.tipo in ['cliente_interno', 'cliente_externo']

    def __repr__(self):
        return f'<User {self.email}>'


class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False, unique=True)
    descricao = db.Column(db.Text)
    ativo = db.Column(db.Boolean, default=True)

    tickets = db.relationship('Ticket', backref='categoria', lazy='dynamic')

    def __repr__(self):
        return f'<Category {self.nome}>'


class SLAConfig(db.Model):
    __tablename__ = 'sla_configs'

    id = db.Column(db.Integer, primary_key=True)
    prioridade = db.Column(db.String(20), nullable=False, unique=True)
    tempo_resposta_horas = db.Column(db.Integer, nullable=False)
    tempo_resolucao_horas = db.Column(db.Integer, nullable=False)

    @staticmethod
    def get_sla(prioridade):
        sla = SLAConfig.query.filter_by(prioridade=prioridade).first()
        if sla:
            return sla
        # Default values
        defaults = {
            'critica': (1, 4),
            'alta': (2, 8),
            'media': (4, 24),
            'baixa': (8, 48)
        }
        resp, resol = defaults.get(prioridade, (8, 48))
        return type('SLA', (), {'tempo_resposta_horas': resp, 'tempo_resolucao_horas': resol})()

    def __repr__(self):
        return f'<SLAConfig {self.prioridade}>'


class Ticket(db.Model):
    __tablename__ = 'tickets'

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    descricao = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='aberto')
    # status: aberto, em_andamento, aguardando, resolvido, fechado
    prioridade = db.Column(db.String(20), default='media')
    # prioridade: baixa, media, alta, critica

    cliente_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    atendente_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    categoria_id = db.Column(db.Integer, db.ForeignKey('categories.id'))

    criado_em = db.Column(db.DateTime, default=datetime.now)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    primeira_resposta_em = db.Column(db.DateTime)
    resolvido_em = db.Column(db.DateTime)
    fechado_em = db.Column(db.DateTime)

    sla_resposta_limite = db.Column(db.DateTime)
    sla_resolucao_limite = db.Column(db.DateTime)

    # Relacionamentos
    historicos = db.relationship('TicketHistory', backref='ticket', lazy='dynamic',
                                  order_by='TicketHistory.criado_em.desc()')
    anexos = db.relationship('Attachment', backref='ticket', lazy='dynamic')

    def calcular_sla(self):
        """Calcula os limites de SLA considerando horário administrativo (08:00-17:00, seg-sex)"""
        sla = SLAConfig.get_sla(self.prioridade)
        self.sla_resposta_limite = adicionar_horas_uteis(self.criado_em, sla.tempo_resposta_horas)
        self.sla_resolucao_limite = adicionar_horas_uteis(self.criado_em, sla.tempo_resolucao_horas)

    def sla_resposta_status(self):
        """Verifica status do SLA de resposta considerando horas úteis"""
        if not self.sla_resposta_limite:
            return 'pendente'
        if self.primeira_resposta_em:
            return 'ok' if self.primeira_resposta_em <= self.sla_resposta_limite else 'violado'
        if datetime.now() > self.sla_resposta_limite:
            return 'violado'
        return 'pendente'

    def sla_resolucao_status(self):
        """Verifica status do SLA de resolução considerando horas úteis"""
        if not self.sla_resolucao_limite:
            return 'pendente'
        if self.resolvido_em:
            return 'ok' if self.resolvido_em <= self.sla_resolucao_limite else 'violado'
        if datetime.now() > self.sla_resolucao_limite:
            return 'violado'
        return 'pendente'

    def horas_uteis_restantes(self):
        """Retorna as horas úteis restantes até o SLA de resolução"""
        if not self.sla_resolucao_limite:
            return 0
        if self.resolvido_em:
            return 0
        return max(0, calcular_horas_uteis_entre(datetime.now(), self.sla_resolucao_limite))

    def tempo_total_atendimento(self):
        """Retorna tempo total de atendimento em minutos"""
        return db.session.query(
            db.func.sum(TicketHistory.tempo_gasto_minutos)
        ).filter(TicketHistory.ticket_id == self.id).scalar() or 0

    def __repr__(self):
        return f'<Ticket #{self.id} - {self.titulo}>'


class TicketHistory(db.Model):
    __tablename__ = 'ticket_history'

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    acao = db.Column(db.String(50), nullable=False)
    # acao: criado, atribuido, status_alterado, comentario, resolvido, fechado
    descricao = db.Column(db.Text)
    tempo_gasto_minutos = db.Column(db.Integer, default=0)
    criado_em = db.Column(db.DateTime, default=datetime.now)

    def __repr__(self):
        return f'<TicketHistory {self.id} - {self.acao}>'


class Attachment(db.Model):
    __tablename__ = 'attachments'

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    nome_arquivo = db.Column(db.String(255), nullable=False)
    caminho = db.Column(db.String(500), nullable=False)
    tamanho = db.Column(db.Integer)
    tipo_mime = db.Column(db.String(100))
    criado_em = db.Column(db.DateTime, default=datetime.now)

    usuario = db.relationship('User', backref='anexos')

    def __repr__(self):
        return f'<Attachment {self.nome_arquivo}>'
