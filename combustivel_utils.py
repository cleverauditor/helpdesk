"""
Utilitários para análise de combustível - Parser de arquivos PRAXIO (ABA - Abastecimento/Quilometragem)
"""
import re
from datetime import datetime
from statistics import median


def parse_float_br(value):
    """Converte float no formato brasileiro (1.234,56) para Python float"""
    if not value or not value.strip():
        return 0.0
    value = value.strip().replace('.', '').replace(',', '.')
    try:
        return float(value)
    except ValueError:
        return 0.0


def parse_arquivo_combustivel(filepath):
    """
    Faz o parse do arquivo TXT do sistema PRAXIO (relatório ABA).
    Retorna dicionário com empresa, período e lista de registros.
    """
    registros = []
    empresa = ''
    periodo_inicio = None
    periodo_fim = None

    # Regex para linhas de dados - começa com prefixo de 7 dígitos
    data_line_re = re.compile(
        r'^(\d{7})\s+'             # Prefixo
        r'(\d{2}/\d{2}/\d{4})\s+'  # Data
        r'(\d{2}:\d{2})\s+'        # Hora
        r'(\w)\s+'                  # Tipo
        r'(\d+)\s+'                 # Tanque
        r'(\d+)\s+'                 # Bomba
        r'([\d.,]+)\s+'             # Combustível (litros)
        r'([\d.,]+)\s+'             # Hodômetro inicial
        r'([\d.,]+)\s+'             # Hodômetro final
        r'(-?[\d.,]+)\s+'           # Km
        r'([\d.,]+)\s+'             # Km acumulado
        r'(-?[\d.,]+)'              # Km/L
    )

    # Regex para Garagem + Modelo no final da linha
    model_re = re.compile(r'(\d{3})\s+(\d{3}-.+?)\s*$')

    try:
        f = open(filepath, 'r', encoding='latin-1')
    except UnicodeDecodeError:
        f = open(filepath, 'r', encoding='utf-8')

    try:
        for line in f:
            line = line.rstrip('\n\r')

            # Extrair empresa
            if 'Empresa inicial:' in line:
                m = re.search(r'Empresa inicial:\s*\d+\s+(.+?)(?:\s{2,}|$)', line)
                if m:
                    empresa = m.group(1).strip()

            # Extrair período
            if 'Datas:' in line:
                m = re.search(
                    r'Datas:\s*(\d{2}/\d{2}/\d{4}).*?a\s+(\d{2}/\d{2}/\d{4})',
                    line
                )
                if m:
                    periodo_inicio = datetime.strptime(m.group(1), '%d/%m/%Y').date()
                    periodo_fim = datetime.strptime(m.group(2), '%d/%m/%Y').date()

            # Parse de linhas de dados
            dm = data_line_re.match(line)
            if not dm:
                continue

            # Extrair modelo do final da linha
            mm = model_re.search(line)
            garagem = mm.group(1) if mm else ''
            modelo = mm.group(2).strip() if mm else ''

            # Verificar flag * (entre Km/L e Garagem)
            resto = line[dm.end():(mm.start() if mm else len(line))]
            flag = '*' if '*' in resto else ''

            registros.append({
                'prefixo': dm.group(1),
                'data': datetime.strptime(dm.group(2), '%d/%m/%Y').date(),
                'hora': dm.group(3),
                'tipo': dm.group(4),
                'tanque': int(dm.group(5)),
                'bomba': int(dm.group(6)),
                'litros': parse_float_br(dm.group(7)),
                'hodometro_inicio': parse_float_br(dm.group(8)),
                'hodometro_fim': parse_float_br(dm.group(9)),
                'km': parse_float_br(dm.group(10)),
                'km_acumulado': parse_float_br(dm.group(11)),
                'kml': parse_float_br(dm.group(12)),
                'flag': flag,
                'garagem': garagem,
                'modelo': modelo,
            })
    finally:
        f.close()

    return {
        'empresa': empresa,
        'periodo_inicio': periodo_inicio,
        'periodo_fim': periodo_fim,
        'registros': registros
    }


def analisar_combustivel(registros):
    """
    Analisa registros de combustível e identifica inconsistências.
    Calcula média/mediana de Km/L por modelo e compara cada registro.

    Retorna dicionário com:
    - resumo: totais gerais
    - modelos: estatísticas por modelo
    - alertas: lista de registros com problemas
    """
    if not registros:
        return {'resumo': {}, 'modelos': {}, 'alertas': []}

    # --- Agrupar por modelo ---
    por_modelo = {}
    for r in registros:
        modelo = r['modelo'] or 'DESCONHECIDO'
        if modelo not in por_modelo:
            por_modelo[modelo] = []
        por_modelo[modelo].append(r)

    # --- Calcular estatísticas por modelo ---
    modelos_stats = {}
    for modelo, regs in por_modelo.items():
        # Filtrar registros válidos para cálculo (km > 0 e kml > 0)
        kml_validos = [r['kml'] for r in regs if r['kml'] > 0 and r['km'] > 0]

        if kml_validos:
            media = sum(kml_validos) / len(kml_validos)
            mediana = median(kml_validos)
            kml_min = min(kml_validos)
            kml_max = max(kml_validos)
        else:
            media = mediana = kml_min = kml_max = 0

        total_litros = sum(r['litros'] for r in regs)
        total_km = sum(r['km'] for r in regs if r['km'] > 0)
        prefixos = set(r['prefixo'] for r in regs)

        modelos_stats[modelo] = {
            'media_kml': round(media, 2),
            'mediana_kml': round(mediana, 2),
            'min_kml': round(kml_min, 2),
            'max_kml': round(kml_max, 2),
            'total_litros': round(total_litros, 2),
            'total_km': round(total_km, 2),
            'total_registros': len(regs),
            'total_veiculos': len(prefixos),
            'kml_geral': round(total_km / total_litros, 2) if total_litros > 0 else 0,
        }

    # --- Também agrupar por prefixo para estatísticas por veículo ---
    por_prefixo = {}
    for r in registros:
        pref = r['prefixo']
        if pref not in por_prefixo:
            por_prefixo[pref] = []
        por_prefixo[pref].append(r)

    # --- Identificar alertas ---
    alertas = []

    for idx, r in enumerate(registros):
        modelo = r['modelo'] or 'DESCONHECIDO'
        stats = modelos_stats.get(modelo, {})
        ref_kml = stats.get('mediana_kml', 0)

        problemas = []

        # 1. Km zero ou negativo
        if r['km'] <= 0:
            problemas.append({
                'tipo': 'KM_INVALIDO',
                'descricao': f"Km {r['km']:.1f} (zero ou negativo)",
                'severidade': 'alta'
            })

        # 2. Hodômetro decrescente
        if r['hodometro_fim'] < r['hodometro_inicio']:
            problemas.append({
                'tipo': 'HODOMETRO_DECRESCENTE',
                'descricao': f"Hodômetro final ({r['hodometro_fim']:.0f}) menor que inicial ({r['hodometro_inicio']:.0f})",
                'severidade': 'alta'
            })

        # 3. Km/L muito abaixo da média do modelo (< 60%)
        if ref_kml > 0 and r['kml'] > 0 and r['km'] > 0:
            percentual = (r['kml'] / ref_kml) * 100

            if percentual < 60:
                problemas.append({
                    'tipo': 'KML_MUITO_BAIXO',
                    'descricao': (
                        f"Km/L {r['kml']:.2f} está {100-percentual:.0f}% abaixo "
                        f"da mediana do modelo ({ref_kml:.2f} Km/L)"
                    ),
                    'severidade': 'alta'
                })
            elif percentual < 75:
                problemas.append({
                    'tipo': 'KML_BAIXO',
                    'descricao': (
                        f"Km/L {r['kml']:.2f} está {100-percentual:.0f}% abaixo "
                        f"da mediana do modelo ({ref_kml:.2f} Km/L)"
                    ),
                    'severidade': 'media'
                })

            # 4. Km/L muito acima da média do modelo (> 150%)
            if percentual > 200:
                problemas.append({
                    'tipo': 'KML_MUITO_ALTO',
                    'descricao': (
                        f"Km/L {r['kml']:.2f} está {percentual-100:.0f}% acima "
                        f"da mediana do modelo ({ref_kml:.2f} Km/L)"
                    ),
                    'severidade': 'alta'
                })
            elif percentual > 150:
                problemas.append({
                    'tipo': 'KML_ALTO',
                    'descricao': (
                        f"Km/L {r['kml']:.2f} está {percentual-100:.0f}% acima "
                        f"da mediana do modelo ({ref_kml:.2f} Km/L)"
                    ),
                    'severidade': 'media'
                })

        # 5. Hodômetro inconsistente com registros anterior/posterior do mesmo veículo
        pref_regs = por_prefixo.get(r['prefixo'], [])
        pref_idx = next((i for i, pr in enumerate(pref_regs) if pr is r), -1)
        if pref_idx > 0:
            anterior = pref_regs[pref_idx - 1]
            if r['hodometro_inicio'] < anterior['hodometro_fim']:
                problemas.append({
                    'tipo': 'HODOMETRO_INCONSISTENTE',
                    'descricao': (
                        f"Hodômetro inicial ({r['hodometro_inicio']:.0f}) menor que "
                        f"o final do abast. anterior ({anterior['hodometro_fim']:.0f})"
                    ),
                    'severidade': 'alta'
                })

        # 6. Flag do sistema PRAXIO
        if r['flag'] == '*':
            # Só adicionar se não houver outro alerta mais específico
            if not problemas:
                problemas.append({
                    'tipo': 'FLAG_SISTEMA',
                    'descricao': 'Marcado pelo sistema PRAXIO (*)',
                    'severidade': 'baixa'
                })

        if problemas:
            alertas.append({
                'indice': idx,
                'registro': r,
                'problemas': problemas,
                'severidade_max': max(
                    p['severidade'] for p in problemas
                ) if problemas else 'baixa'
            })

    # --- Resumo geral ---
    total_litros = sum(r['litros'] for r in registros)
    total_km = sum(r['km'] for r in registros if r['km'] > 0)
    prefixos_unicos = set(r['prefixo'] for r in registros)

    resumo = {
        'total_registros': len(registros),
        'total_veiculos': len(prefixos_unicos),
        'total_modelos': len(por_modelo),
        'total_litros': round(total_litros, 2),
        'total_km': round(total_km, 2),
        'media_kml': round(total_km / total_litros, 2) if total_litros > 0 else 0,
        'total_alertas': len(alertas),
        'alertas_alta': sum(1 for a in alertas if a['severidade_max'] == 'alta'),
        'alertas_media': sum(1 for a in alertas if a['severidade_max'] == 'media'),
        'alertas_baixa': sum(1 for a in alertas if a['severidade_max'] == 'baixa'),
    }

    return {
        'resumo': resumo,
        'modelos': dict(sorted(modelos_stats.items())),
        'alertas': alertas,
    }
