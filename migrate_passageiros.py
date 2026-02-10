"""
Script de migração para adicionar tabelas e colunas do módulo de passageiros.
Executar uma vez após o deploy: python migrate_passageiros.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from models import db

app = create_app()
with app.app_context():
    # Criar tabelas novas (cliente_turnos, passageiros_base)
    db.create_all()
    print("Tabelas novas criadas (cliente_turnos, passageiros_base).")

    # Adicionar colunas novas em tabelas existentes
    alteracoes = [
        ("roteirizacoes", "turno_id", "INTEGER REFERENCES cliente_turnos(id)"),
        ("passageiros", "passageiro_base_id", "INTEGER REFERENCES passageiros_base(id)"),
    ]

    for tabela, coluna, tipo in alteracoes:
        try:
            db.session.execute(db.text(
                f'ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}'
            ))
            db.session.commit()
            print(f"  + Coluna {tabela}.{coluna} adicionada.")
        except Exception as e:
            db.session.rollback()
            if 'duplicate column' in str(e).lower() or 'already exists' in str(e).lower():
                print(f"  = Coluna {tabela}.{coluna} já existe.")
            else:
                print(f"  ! Erro em {tabela}.{coluna}: {e}")

    print("\nMigração concluída.")
