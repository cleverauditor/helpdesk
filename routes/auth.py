from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')

        user = User.query.filter_by(email=email).first()

        if user and user.check_senha(senha):
            if not user.ativo:
                flash('Sua conta está desativada. Entre em contato com o administrador.', 'danger')
                return render_template('login.html')

            login_user(user, remember=request.form.get('lembrar'))
            next_page = request.args.get('next')
            flash(f'Bem-vindo, {user.nome}!', 'success')
            return redirect(next_page or url_for('dashboard.index'))
        else:
            flash('Email ou senha inválidos.', 'danger')

    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Você saiu do sistema.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/registro', methods=['GET', 'POST'])
def registro():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')
        confirmar_senha = request.form.get('confirmar_senha', '')
        tipo = request.form.get('tipo', 'cliente_externo')

        # Validações
        errors = []
        if not nome or len(nome) < 3:
            errors.append('Nome deve ter pelo menos 3 caracteres.')
        if not email or '@' not in email:
            errors.append('Email inválido.')
        if len(senha) < 6:
            errors.append('Senha deve ter pelo menos 6 caracteres.')
        if senha != confirmar_senha:
            errors.append('Senhas não conferem.')
        if User.query.filter_by(email=email).first():
            errors.append('Este email já está cadastrado.')
        if tipo not in ['cliente_interno', 'cliente_externo']:
            tipo = 'cliente_externo'

        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('registro.html')

        user = User(
            nome=nome,
            email=email,
            tipo=tipo,
            departamento=request.form.get('departamento', '').strip(),
            telefone=request.form.get('telefone', '').strip()
        )
        user.set_senha(senha)

        db.session.add(user)
        db.session.commit()

        flash('Conta criada com sucesso! Faça login para continuar.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('registro.html')


@auth_bp.route('/perfil', methods=['GET', 'POST'])
@login_required
def perfil():
    if request.method == 'POST':
        current_user.nome = request.form.get('nome', '').strip()
        current_user.departamento = request.form.get('departamento', '').strip()
        current_user.telefone = request.form.get('telefone', '').strip()

        nova_senha = request.form.get('nova_senha', '')
        if nova_senha:
            if len(nova_senha) < 6:
                flash('Nova senha deve ter pelo menos 6 caracteres.', 'danger')
                return render_template('perfil.html')
            if nova_senha != request.form.get('confirmar_senha', ''):
                flash('Senhas não conferem.', 'danger')
                return render_template('perfil.html')
            current_user.set_senha(nova_senha)

        db.session.commit()
        flash('Perfil atualizado com sucesso!', 'success')

    return render_template('perfil.html')
