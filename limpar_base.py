#!/usr/bin/env python3
"""
Script para limpar base de dados antes de produção.
Remove todos os chamados e usuários não-admin.
"""

from app import app
from models import db, User, Ticket, TicketHistory, Attachment, Category

def limpar_base():
    with app.app_context():
        print("=== Limpeza da Base de Dados ===\n")

        # Mostrar o que será removido
        total_tickets = Ticket.query.count()
        total_historico = TicketHistory.query.count()
        total_anexos = Attachment.query.count()
        usuarios_remover = User.query.filter(User.tipo != 'admin').all()
        admins = User.query.filter(User.tipo == 'admin').all()

        print(f"Chamados a remover: {total_tickets}")
        print(f"Históricos a remover: {total_historico}")
        print(f"Anexos a remover: {total_anexos}")
        print(f"Usuários a remover: {len(usuarios_remover)}")
        print(f"Admins a manter: {len(admins)}")

        for admin in admins:
            print(f"  - {admin.nome} ({admin.email})")

        print("\n" + "="*40)
        confirma = input("Confirma limpeza? (digite 'SIM' para confirmar): ")

        if confirma != 'SIM':
            print("Operação cancelada.")
            return

        print("\nLimpando...")

        # 1. Remover anexos
        Attachment.query.delete()
        print("- Anexos removidos")

        # 2. Remover histórico
        TicketHistory.query.delete()
        print("- Histórico removido")

        # 3. Remover chamados
        Ticket.query.delete()
        print("- Chamados removidos")

        # 4. Remover usuários não-admin
        User.query.filter(User.tipo != 'admin').delete()
        print("- Usuários não-admin removidos")

        # Commit
        db.session.commit()

        print("\n=== Limpeza concluída! ===")
        print(f"Admins mantidos: {User.query.filter(User.tipo == 'admin').count()}")
        print(f"Total de chamados: {Ticket.query.count()}")

if __name__ == '__main__':
    limpar_base()
