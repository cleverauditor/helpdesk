"""
Script para testar comparação de arquivos KML.

Uso:
  python testar_comparacao.py                         # lista arquivos e compara os da rota com a última auditoria
  python testar_comparacao.py planejado.kml exec.kml  # compara dois arquivos específicos do diretório uploads/
"""
import sys
import os
import hashlib

# Adicionar diretório do projeto ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kml_utils import extrair_coordenadas_kml, comparar_kml, calcular_distancia_total, haversine

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')


def listar_arquivos():
    """Lista todos os KML/KMZ no diretório de uploads com hash para identificar duplicados."""
    arquivos = []
    for f in sorted(os.listdir(UPLOAD_DIR)):
        if f.lower().endswith(('.kml', '.kmz')):
            caminho = os.path.join(UPLOAD_DIR, f)
            tamanho = os.path.getsize(caminho)
            with open(caminho, 'rb') as fh:
                md5 = hashlib.md5(fh.read()).hexdigest()[:12]
            coords = extrair_coordenadas_kml(caminho)
            arquivos.append({
                'nome': f,
                'tamanho': tamanho,
                'md5': md5,
                'coords': len(coords),
                'km': round(calcular_distancia_total(coords), 2) if coords else 0
            })
    return arquivos


def comparar_e_exibir(path_planejado, path_executado):
    """Executa a comparação e exibe resultados detalhados."""
    print('=' * 60)
    print('COMPARACAO DE ARQUIVOS KML')
    print('=' * 60)

    # Verificar se são idênticos
    with open(path_planejado, 'rb') as f1, open(path_executado, 'rb') as f2:
        h1 = hashlib.md5(f1.read()).hexdigest()
        h2 = hashlib.md5(f2.read()).hexdigest()

    if h1 == h2:
        print()
        print('  *** ATENCAO: Os dois arquivos sao IDENTICOS! ***')
        print('  *** A aderencia sera 100% porque eh o mesmo arquivo. ***')
        print()

    # Extrair coordenadas
    coords_p = extrair_coordenadas_kml(path_planejado)
    coords_e = extrair_coordenadas_kml(path_executado)

    print(f'\nArquivo PLANEJADO: {os.path.basename(path_planejado)}')
    print(f'  Coordenadas: {len(coords_p)}')
    if coords_p:
        print(f'  Primeiro ponto: lat={coords_p[0][0]:.6f}, lon={coords_p[0][1]:.6f}')
        print(f'  Ultimo ponto:   lat={coords_p[-1][0]:.6f}, lon={coords_p[-1][1]:.6f}')
        print(f'  KM total: {calcular_distancia_total(coords_p):.2f} km')
    else:
        print('  *** SEM COORDENADAS - comparacao impossivel ***')

    print(f'\nArquivo EXECUTADO: {os.path.basename(path_executado)}')
    print(f'  Coordenadas: {len(coords_e)}')
    if coords_e:
        print(f'  Primeiro ponto: lat={coords_e[0][0]:.6f}, lon={coords_e[0][1]:.6f}')
        print(f'  Ultimo ponto:   lat={coords_e[-1][0]:.6f}, lon={coords_e[-1][1]:.6f}')
        print(f'  KM total: {calcular_distancia_total(coords_e):.2f} km')
    else:
        print('  *** SEM COORDENADAS - comparacao impossivel ***')

    # Executar comparação
    print('\n' + '=' * 60)
    print('RESULTADO')
    print('=' * 60)

    resultado = comparar_kml(path_planejado, path_executado)

    km_plan = resultado['km_planejado']
    km_exec = resultado['km_percorrido']
    aderencia = resultado['aderencia_percentual']
    desvio = resultado['desvio_maximo_metros']
    fora = resultado['pontos_fora_rota']

    print(f'  KM Planejado:       {km_plan if km_plan is not None else "N/A"} km')
    print(f'  KM Percorrido:      {km_exec if km_exec is not None else "N/A"} km')
    print(f'  Desvio Maximo:      {desvio if desvio is not None else "N/A"} metros')
    print(f'  Pontos Fora Rota:   {fora if fora is not None else "N/A"}')
    print(f'  ADERENCIA:          {aderencia if aderencia is not None else "N/A"}%')

    if aderencia is not None:
        if aderencia >= 90:
            print('  Status:             OK')
        elif aderencia >= 70:
            print('  Status:             ATENCAO - desvios moderados')
        else:
            print('  Status:             CRITICO - rota muito diferente')

    print('=' * 60)


def main():
    if len(sys.argv) == 3:
        # Modo: dois arquivos passados como argumento
        arq1 = sys.argv[1]
        arq2 = sys.argv[2]

        # Se não são caminhos completos, procurar em uploads/
        if not os.path.isabs(arq1):
            arq1 = os.path.join(UPLOAD_DIR, arq1)
        if not os.path.isabs(arq2):
            arq2 = os.path.join(UPLOAD_DIR, arq2)

        if not os.path.exists(arq1):
            print(f'Arquivo nao encontrado: {arq1}')
            sys.exit(1)
        if not os.path.exists(arq2):
            print(f'Arquivo nao encontrado: {arq2}')
            sys.exit(1)

        comparar_e_exibir(arq1, arq2)

    else:
        # Modo: listar arquivos e comparar da base de dados
        print('=' * 60)
        print('ARQUIVOS KML NO DIRETORIO DE UPLOADS')
        print('=' * 60)

        arquivos = listar_arquivos()
        if not arquivos:
            print('Nenhum arquivo KML encontrado em uploads/')
            sys.exit(0)

        # Agrupar por hash para mostrar duplicados
        por_hash = {}
        for i, arq in enumerate(arquivos):
            if arq['md5'] not in por_hash:
                por_hash[arq['md5']] = []
            por_hash[arq['md5']].append((i, arq))

        for md5, grupo in por_hash.items():
            duplicado = ' (TODOS IDENTICOS)' if len(grupo) > 1 else ''
            print(f'\n  Hash: {md5}{duplicado}')
            for i, arq in grupo:
                print(f'    [{i+1}] {arq["nome"]}')
                print(f'        {arq["tamanho"]} bytes | {arq["coords"]} coordenadas | {arq["km"]} km')

        # Comparar usando dados do banco
        print('\n' + '=' * 60)
        print('COMPARACAO DA BASE DE DADOS')
        print('=' * 60)

        try:
            from app import create_app
            from models import Rota, Auditoria
            app = create_app()
            with app.app_context():
                rotas = Rota.query.all()
                for rota in rotas:
                    print(f'\n--- Rota: {rota.tag} (id={rota.id}) ---')
                    if not rota.arquivo_kml:
                        print('  Sem arquivo KML planejado cadastrado!')
                        continue

                    path_plan = os.path.join(UPLOAD_DIR, rota.arquivo_kml)
                    if not os.path.exists(path_plan):
                        print(f'  Arquivo planejado NAO EXISTE: {rota.arquivo_kml}')
                        continue

                    auditorias = rota.auditorias.order_by(Auditoria.id.desc()).limit(3).all()
                    if not auditorias:
                        print('  Nenhuma auditoria realizada.')
                        continue

                    for a in auditorias:
                        path_exec = os.path.join(UPLOAD_DIR, a.arquivo_kml)
                        print(f'\n  Auditoria #{a.id} ({a.data_auditoria}):')

                        if not os.path.exists(path_exec):
                            print(f'    Arquivo executado NAO EXISTE: {a.arquivo_kml}')
                            continue

                        comparar_e_exibir(path_plan, path_exec)

        except Exception as e:
            print(f'\nErro ao acessar banco de dados: {e}')
            print('Voce pode rodar manualmente:')
            print('  python testar_comparacao.py arquivo1.kml arquivo2.kml')


if __name__ == '__main__':
    main()
