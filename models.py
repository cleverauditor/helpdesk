from datetime import datetime, timedelta, time, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# Timezone de Brasília (UTC-3)
TIMEZONE_BRASIL = timezone(timedelta(hours=-3))


def agora_brasil():
    """Retorna a data/hora atual no timezone de Brasília (UTC-3)"""
    return datetime.now(TIMEZONE_BRASIL).replace(tzinfo=None)

# Tabela de associação muitos-para-muitos: Atendente <-> Categoria
atendente_categoria = db.Table('atendente_categoria',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id'), primary_key=True),
    db.Column('categoria_id', db.Integer, db.ForeignKey('categories.id'), primary_key=True)
)

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
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    # Relacionamentos
    tickets_criados = db.relationship('Ticket', backref='cliente', lazy='dynamic',
                                       foreign_keys='Ticket.cliente_id')
    tickets_atendidos = db.relationship('Ticket', backref='atendente', lazy='dynamic',
                                         foreign_keys='Ticket.atendente_id')
    historicos = db.relationship('TicketHistory', backref='usuario', lazy='dynamic')
    # Categorias que o atendente pode visualizar/atender
    categorias = db.relationship('Category', secondary=atendente_categoria, lazy='dynamic',
                                  backref=db.backref('atendentes', lazy='dynamic'))

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

    def pode_ver_categoria(self, categoria_id):
        """Verifica se o atendente pode ver chamados de uma categoria"""
        if self.is_admin():
            return True
        if not self.is_atendente():
            return False
        # Se não tem categorias atribuídas, pode ver todas
        if self.categorias.count() == 0:
            return True
        return self.categorias.filter_by(id=categoria_id).first() is not None

    def get_categorias_ids(self):
        """Retorna lista de IDs das categorias do atendente"""
        return [c.id for c in self.categorias.all()]

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
    # status: aberto, em_andamento, fechado
    prioridade = db.Column(db.String(20), default='media')
    # prioridade: baixa, media, alta, critica

    cliente_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    atendente_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    categoria_id = db.Column(db.Integer, db.ForeignKey('categories.id'))

    criado_em = db.Column(db.DateTime, default=agora_brasil)
    atualizado_em = db.Column(db.DateTime, default=agora_brasil, onupdate=agora_brasil)
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
        if agora_brasil() > self.sla_resposta_limite:
            return 'violado'
        return 'pendente'

    def sla_resolucao_status(self):
        """Verifica status do SLA de resolução considerando horas úteis"""
        if not self.sla_resolucao_limite:
            return 'pendente'
        # Usar fechado_em para verificar SLA (resolvido_em mantido para compatibilidade)
        data_conclusao = self.fechado_em or self.resolvido_em
        if data_conclusao:
            return 'ok' if data_conclusao <= self.sla_resolucao_limite else 'violado'
        if agora_brasil() > self.sla_resolucao_limite:
            return 'violado'
        return 'pendente'

    def horas_uteis_restantes(self):
        """Retorna as horas úteis restantes até o SLA de resolução"""
        if not self.sla_resolucao_limite:
            return 0
        if self.resolvido_em:
            return 0
        return max(0, calcular_horas_uteis_entre(agora_brasil(), self.sla_resolucao_limite))

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
    criado_em = db.Column(db.DateTime, default=agora_brasil)

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
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    usuario = db.relationship('User', backref='anexos')

    def __repr__(self):
        return f'<Attachment {self.nome_arquivo}>'


# ============================================
# MÓDULO DE AUDITORIA DE ROTAS
# ============================================

class Cliente(db.Model):
    """Cadastro de clientes/empresas para auditoria de rotas"""
    __tablename__ = 'clientes'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    razao_social = db.Column(db.String(200))
    cnpj = db.Column(db.String(20), unique=True)
    endereco = db.Column(db.String(300))
    cidade = db.Column(db.String(100))
    estado = db.Column(db.String(2))
    telefone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    contato = db.Column(db.String(100))  # Nome do contato principal
    observacoes = db.Column(db.Text)

    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)
    atualizado_em = db.Column(db.DateTime, default=agora_brasil, onupdate=agora_brasil)

    # Relacionamentos
    rotas = db.relationship('Rota', backref='cliente', lazy='dynamic')

    def __repr__(self):
        return f'<Cliente {self.nome}>'


class TurnoPadrao(db.Model):
    """Turnos pré-definidos que podem ser associados às rotas"""
    __tablename__ = 'turnos_padrao'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    horario_inicio = db.Column(db.Time, nullable=False)
    horario_termino = db.Column(db.Time, nullable=False)
    descricao = db.Column(db.Text)
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    def horario_formatado(self):
        """Retorna horário formatado (ex: 06:00 - 14:00)"""
        return f'{self.horario_inicio.strftime("%H:%M")} - {self.horario_termino.strftime("%H:%M")}'

    def __repr__(self):
        return f'<TurnoPadrao {self.nome}>'


class Modal(db.Model):
    """Tipos de veículos/modais de transporte"""
    __tablename__ = 'modais'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False, unique=True)
    descricao = db.Column(db.Text)
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    # Relacionamentos
    rotas = db.relationship('Rota', backref='modal', lazy='dynamic')

    def __repr__(self):
        return f'<Modal {self.nome}>'


class Rota(db.Model):
    """Cadastro de rotas de transporte"""
    __tablename__ = 'rotas'

    id = db.Column(db.Integer, primary_key=True)
    tag = db.Column(db.String(50), nullable=False, unique=True)
    nome = db.Column(db.String(200))

    # Arquivo KML da rota planejada
    arquivo_kml = db.Column(db.String(500))
    arquivo_kml_nome = db.Column(db.String(255))

    # Relacionamento com cliente (empresa)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=False)

    # Dados operacionais
    km_atual = db.Column(db.Float, default=0)
    modal_id = db.Column(db.Integer, db.ForeignKey('modais.id'))
    data_implantacao = db.Column(db.Date)

    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)
    atualizado_em = db.Column(db.DateTime, default=agora_brasil, onupdate=agora_brasil)

    # Relacionamentos
    turnos = db.relationship('RotaTurno', backref='rota', lazy='dynamic',
                             order_by='RotaTurno.horario_inicio')
    historicos = db.relationship('RotaHistory', backref='rota', lazy='dynamic',
                                  order_by='RotaHistory.criado_em.desc()')
    auditorias = db.relationship('Auditoria', backref='rota', lazy='dynamic',
                                  order_by='Auditoria.criado_em.desc()')

    def __repr__(self):
        return f'<Rota {self.tag}>'


class RotaTurno(db.Model):
    """Turnos de operação de uma rota"""
    __tablename__ = 'rota_turnos'

    id = db.Column(db.Integer, primary_key=True)
    rota_id = db.Column(db.Integer, db.ForeignKey('rotas.id'), nullable=False)

    nome = db.Column(db.String(100))
    horario_inicio = db.Column(db.Time, nullable=False)
    horario_termino = db.Column(db.Time, nullable=False)
    tempo_trajeto_minutos = db.Column(db.Integer)

    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    def tempo_trajeto_formatado(self):
        """Retorna tempo de trajeto formatado (ex: 1h 30min)"""
        if not self.tempo_trajeto_minutos:
            return '-'
        horas = self.tempo_trajeto_minutos // 60
        minutos = self.tempo_trajeto_minutos % 60
        if horas > 0:
            return f'{horas}h {minutos}min' if minutos else f'{horas}h'
        return f'{minutos}min'

    def __repr__(self):
        return f'<RotaTurno {self.rota.tag} - {self.horario_inicio}>'


class RotaHistory(db.Model):
    """Histórico de alterações em rotas"""
    __tablename__ = 'rota_history'

    id = db.Column(db.Integer, primary_key=True)
    rota_id = db.Column(db.Integer, db.ForeignKey('rotas.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    acao = db.Column(db.String(50), nullable=False)
    # ações: criado, editado, turno_adicionado, turno_alterado, turno_removido,
    #        km_atualizado, modal_alterado, kml_atualizado, auditoria_realizada

    descricao = db.Column(db.Text)
    valor_anterior = db.Column(db.String(500))
    valor_novo = db.Column(db.String(500))
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    # Relacionamento
    usuario = db.relationship('User', backref='historicos_rota')

    def __repr__(self):
        return f'<RotaHistory {self.id} - {self.acao}>'


class Auditoria(db.Model):
    """Registro de auditorias realizadas em rotas"""
    __tablename__ = 'auditorias'

    id = db.Column(db.Integer, primary_key=True)
    rota_id = db.Column(db.Integer, db.ForeignKey('rotas.id'), nullable=False)

    # Arquivo KML importado (rota executada pelo rastreador)
    arquivo_kml = db.Column(db.String(500), nullable=False)
    arquivo_kml_nome = db.Column(db.String(255))

    # Quem realizou a auditoria
    atendente_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    # Dados da auditoria
    data_auditoria = db.Column(db.Date, default=lambda: agora_brasil().date())
    observacoes = db.Column(db.Text)

    # Métricas da comparação KML
    km_percorrido = db.Column(db.Float)
    km_planejado = db.Column(db.Float)
    desvio_maximo_metros = db.Column(db.Float)
    aderencia_percentual = db.Column(db.Float)
    pontos_fora_rota = db.Column(db.Integer)

    criado_em = db.Column(db.DateTime, default=agora_brasil)

    # Relacionamentos
    atendente = db.relationship('User', backref='auditorias_realizadas')

    def __repr__(self):
        return f'<Auditoria {self.id} - Rota {self.rota.tag}>'


# ============================================
# MÓDULO DE AUDITORIA DE COMBUSTÍVEL
# ============================================

class CombustivelAnalise(db.Model):
    """Registro de análises de combustível importadas"""
    __tablename__ = 'combustivel_analises'

    id = db.Column(db.Integer, primary_key=True)
    nome_arquivo = db.Column(db.String(255), nullable=False)
    empresa = db.Column(db.String(200))
    periodo_inicio = db.Column(db.Date)
    periodo_fim = db.Column(db.Date)

    total_litros = db.Column(db.Float)
    total_km = db.Column(db.Float)
    media_kml = db.Column(db.Float)
    total_registros = db.Column(db.Integer)
    total_veiculos = db.Column(db.Integer)
    total_alertas = db.Column(db.Integer)

    usuario_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    # Relacionamentos
    registros = db.relationship('CombustivelRegistro', backref='analise', lazy='dynamic',
                                cascade='all, delete-orphan')
    usuario = db.relationship('User', backref='analises_combustivel')

    def __repr__(self):
        return f'<CombustivelAnalise {self.id} - {self.nome_arquivo}>'


class CombustivelRegistro(db.Model):
    """Registros individuais de abastecimento"""
    __tablename__ = 'combustivel_registros'

    id = db.Column(db.Integer, primary_key=True)
    analise_id = db.Column(db.Integer, db.ForeignKey('combustivel_analises.id'), nullable=False)

    prefixo = db.Column(db.String(20))
    data = db.Column(db.Date)
    hora = db.Column(db.String(10))
    tanque = db.Column(db.Integer)
    bomba = db.Column(db.Integer)
    litros = db.Column(db.Float)
    hodometro_inicio = db.Column(db.Float)
    hodometro_fim = db.Column(db.Float)
    km = db.Column(db.Float)
    km_acumulado = db.Column(db.Float)
    kml = db.Column(db.Float)
    modelo = db.Column(db.String(100))
    garagem = db.Column(db.String(10))
    flag = db.Column(db.String(5))

    # Campos de análise
    alerta = db.Column(db.Boolean, default=False)
    tipo_alerta = db.Column(db.String(100))
    descricao_alerta = db.Column(db.Text)

    def __repr__(self):
        return f'<CombustivelRegistro {self.prefixo} {self.data}>'


class CombustivelMediaPadrao(db.Model):
    """Médias padrão de Km/L por modelo de veículo (referência para análise)"""
    __tablename__ = 'combustivel_medias_padrao'

    id = db.Column(db.Integer, primary_key=True)
    modelo = db.Column(db.String(100), nullable=False, unique=True)
    categoria = db.Column(db.String(50))  # onibus, micro, van, sprinter
    media_kml_referencia = db.Column(db.Float, nullable=False)
    kml_minimo_aceitavel = db.Column(db.Float)
    kml_maximo_aceitavel = db.Column(db.Float)
    observacoes = db.Column(db.Text)
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)
    atualizado_em = db.Column(db.DateTime, default=agora_brasil, onupdate=agora_brasil)

    def __repr__(self):
        return f'<CombustivelMediaPadrao {self.modelo} - {self.media_kml_referencia} Km/L>'


# ============================================
# INDICADORES DIRETORIA
# ============================================

class IndicadorCategoria(db.Model):
    __tablename__ = 'indicador_categoria'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False, unique=True)
    ordem = db.Column(db.Integer, default=0)
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    indicadores = db.relationship('Indicador', backref='categoria', lazy='dynamic',
                                  order_by='Indicador.ordem')

    def __repr__(self):
        return f'<IndicadorCategoria {self.nome}>'


class Indicador(db.Model):
    __tablename__ = 'indicador'

    id = db.Column(db.Integer, primary_key=True)
    categoria_id = db.Column(db.Integer, db.ForeignKey('indicador_categoria.id'), nullable=False)
    nome = db.Column(db.String(200), nullable=False)
    descricao = db.Column(db.Text)
    responsavel_geracao = db.Column(db.String(200))
    responsavel_conferencia = db.Column(db.String(200))
    ordem = db.Column(db.Integer, default=0)
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    registros = db.relationship('IndicadorRegistro', backref='indicador', lazy='dynamic',
                                order_by='IndicadorRegistro.mes_referencia.desc()')

    def __repr__(self):
        return f'<Indicador {self.nome}>'


class IndicadorRegistro(db.Model):
    __tablename__ = 'indicador_registro'

    id = db.Column(db.Integer, primary_key=True)
    indicador_id = db.Column(db.Integer, db.ForeignKey('indicador.id'), nullable=False)
    mes_referencia = db.Column(db.Date, nullable=False)
    valor_texto = db.Column(db.Text)
    status = db.Column(db.String(20), default='pendente')  # pendente, preenchido, conferido
    preenchido_por_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    conferido_por_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    data_preenchimento = db.Column(db.DateTime)
    data_conferencia = db.Column(db.DateTime)
    observacoes = db.Column(db.Text)
    criado_em = db.Column(db.DateTime, default=agora_brasil)
    atualizado_em = db.Column(db.DateTime, default=agora_brasil, onupdate=agora_brasil)

    preenchido_por = db.relationship('User', foreign_keys=[preenchido_por_id])
    conferido_por = db.relationship('User', foreign_keys=[conferido_por_id])

    def __repr__(self):
        return f'<IndicadorRegistro {self.indicador.nome} - {self.mes_referencia}>'
