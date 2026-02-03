"""
Utilitários para processamento e comparação de arquivos KML.
"""
import xml.etree.ElementTree as ET
from math import radians, sin, cos, sqrt, atan2
import zipfile
import os
import re


def haversine(lat1, lon1, lat2, lon2):
    """
    Calcula a distância em metros entre dois pontos geográficos
    usando a fórmula de Haversine.
    """
    R = 6371000  # Raio da Terra em metros

    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)
    delta_lat = radians(lat2 - lat1)
    delta_lon = radians(lon2 - lon1)

    a = sin(delta_lat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c


def extrair_coordenadas_kml(filepath):
    """
    Extrai coordenadas de um arquivo KML ou KMZ.
    Retorna lista de tuplas (latitude, longitude).
    """
    if not filepath or not os.path.exists(filepath):
        return []

    coordenadas = []

    try:
        # Verificar se é KMZ (arquivo compactado)
        if filepath.lower().endswith('.kmz'):
            with zipfile.ZipFile(filepath, 'r') as kmz:
                # Procurar pelo arquivo doc.kml dentro do KMZ
                for name in kmz.namelist():
                    if name.endswith('.kml'):
                        with kmz.open(name) as kml_file:
                            content = kml_file.read()
                            coordenadas = _parse_kml_content(content)
                            if coordenadas:
                                break
        else:
            # Arquivo KML normal
            with open(filepath, 'rb') as f:
                content = f.read()
                coordenadas = _parse_kml_content(content)

    except Exception as e:
        print(f"Erro ao extrair coordenadas: {e}")
        return []

    return coordenadas


def _parse_kml_content(content):
    """
    Parse do conteúdo XML do KML.
    Usa regex para extrair coordenadas, mais robusto que XML parsing.
    """
    coordenadas = []

    try:
        content_str = content.decode('utf-8', errors='ignore')

        # Método 1: Usar regex para extrair coordenadas diretamente
        # Isso é mais robusto que parsing XML quando há namespaces complexos
        coords_pattern = r'<coordinates[^>]*>(.*?)</coordinates>'
        matches = re.findall(coords_pattern, content_str, re.DOTALL | re.IGNORECASE)

        for match in matches:
            coords_text = match.strip()
            # Formato: lon,lat,alt ou lon,lat separados por espaços, tabs ou quebras de linha
            # Dividir por qualquer whitespace
            coord_pairs = re.split(r'\s+', coords_text)

            for coord in coord_pairs:
                coord = coord.strip()
                if not coord:
                    continue
                parts = coord.split(',')
                if len(parts) >= 2:
                    try:
                        lon = float(parts[0])
                        lat = float(parts[1])
                        # Validar coordenadas
                        if -180 <= lon <= 180 and -90 <= lat <= 90:
                            coordenadas.append((lat, lon))
                    except ValueError:
                        continue

        # Método 2: Se não encontrou com regex, tenta parsing XML
        if not coordenadas:
            coordenadas = _parse_kml_xml(content_str)

    except Exception as e:
        print(f"Erro ao fazer parse do KML: {e}")

    return coordenadas


def _parse_kml_xml(content_str):
    """
    Tenta fazer parsing XML do KML removendo namespaces.
    """
    coordenadas = []

    try:
        # Remover todas as declarações de namespace
        content_clean = re.sub(r'\sxmlns[^=]*="[^"]*"', '', content_str)
        # Remover prefixos de namespace nas tags
        content_clean = re.sub(r'<(/?)[\w]+:', r'<\1', content_clean)

        root = ET.fromstring(content_clean)

        # Procurar por tags <coordinates> em qualquer lugar do documento
        for elem in root.iter():
            if elem.tag.lower() == 'coordinates' or elem.tag.lower().endswith('coordinates'):
                if elem.text:
                    coords_text = elem.text.strip()
                    coord_pairs = re.split(r'\s+', coords_text)

                    for coord in coord_pairs:
                        coord = coord.strip()
                        if not coord:
                            continue
                        parts = coord.split(',')
                        if len(parts) >= 2:
                            try:
                                lon = float(parts[0])
                                lat = float(parts[1])
                                if -180 <= lon <= 180 and -90 <= lat <= 90:
                                    coordenadas.append((lat, lon))
                            except ValueError:
                                continue

    except Exception as e:
        # Silenciosamente ignorar erros de XML - já tentamos regex
        pass

    return coordenadas


def calcular_distancia_total(coordenadas):
    """
    Calcula a distância total percorrida em quilômetros.
    """
    if len(coordenadas) < 2:
        return 0

    distancia_total = 0
    for i in range(len(coordenadas) - 1):
        lat1, lon1 = coordenadas[i]
        lat2, lon2 = coordenadas[i + 1]
        distancia_total += haversine(lat1, lon1, lat2, lon2)

    return distancia_total / 1000  # Converter para km


def distancia_ponto_para_linha(ponto, linha_coords, tolerancia_metros=100):
    """
    Calcula a menor distância de um ponto para uma linha (sequência de coordenadas).
    Retorna a distância em metros.
    """
    if not linha_coords:
        return float('inf')

    lat_ponto, lon_ponto = ponto
    menor_distancia = float('inf')

    for lat, lon in linha_coords:
        dist = haversine(lat_ponto, lon_ponto, lat, lon)
        if dist < menor_distancia:
            menor_distancia = dist

    return menor_distancia


def comparar_kml(kml_planejado_path, kml_executado_path, tolerancia_metros=100):
    """
    Compara dois arquivos KML (planejado vs executado) e retorna métricas.

    Args:
        kml_planejado_path: Caminho do arquivo KML da rota planejada
        kml_executado_path: Caminho do arquivo KML da rota executada
        tolerancia_metros: Distância máxima em metros para considerar um ponto "dentro" da rota

    Returns:
        dict com métricas:
        - km_planejado: Distância total da rota planejada em km
        - km_percorrido: Distância total percorrida em km
        - desvio_maximo_metros: Maior distância de um ponto executado para a rota planejada
        - aderencia_percentual: Percentual de pontos dentro da tolerância
        - pontos_fora_rota: Quantidade de pontos fora da tolerância
    """
    resultado = {
        'km_planejado': None,
        'km_percorrido': None,
        'desvio_maximo_metros': None,
        'aderencia_percentual': None,
        'pontos_fora_rota': None
    }

    # Extrair coordenadas do arquivo executado
    coords_executado = extrair_coordenadas_kml(kml_executado_path)
    if not coords_executado:
        print(f"Nenhuma coordenada encontrada no arquivo executado: {kml_executado_path}")
        return resultado

    # Calcular km percorrido
    resultado['km_percorrido'] = round(calcular_distancia_total(coords_executado), 2)

    # Se não tem arquivo planejado, retorna apenas o km percorrido
    if not kml_planejado_path or not os.path.exists(kml_planejado_path):
        # Sem arquivo planejado, considerar 100% de aderência
        resultado['aderencia_percentual'] = 100.0
        resultado['desvio_maximo_metros'] = 0
        resultado['pontos_fora_rota'] = 0
        return resultado

    # Extrair coordenadas do arquivo planejado
    coords_planejado = extrair_coordenadas_kml(kml_planejado_path)
    if not coords_planejado:
        print(f"Nenhuma coordenada encontrada no arquivo planejado: {kml_planejado_path}")
        # Sem coordenadas planejadas, considerar 100% de aderência
        resultado['aderencia_percentual'] = 100.0
        resultado['desvio_maximo_metros'] = 0
        resultado['pontos_fora_rota'] = 0
        return resultado

    # Calcular km planejado
    resultado['km_planejado'] = round(calcular_distancia_total(coords_planejado), 2)

    # Calcular desvios
    pontos_fora = 0
    desvio_maximo = 0

    for ponto in coords_executado:
        dist = distancia_ponto_para_linha(ponto, coords_planejado)

        if dist > tolerancia_metros:
            pontos_fora += 1

        if dist > desvio_maximo:
            desvio_maximo = dist

    # Calcular métricas
    resultado['desvio_maximo_metros'] = round(desvio_maximo, 2)
    resultado['pontos_fora_rota'] = pontos_fora

    # Aderência: percentual de pontos dentro da tolerância
    pontos_dentro = len(coords_executado) - pontos_fora
    if len(coords_executado) > 0:
        resultado['aderencia_percentual'] = round((pontos_dentro / len(coords_executado)) * 100, 2)
    else:
        resultado['aderencia_percentual'] = 0

    return resultado


def extrair_tempo_trajeto(filepath):
    """
    Extrai o tempo de trajeto de um arquivo KML baseado nos timestamps.
    Retorna o tempo em minutos ou None se não encontrar timestamps.
    """
    if not filepath or not os.path.exists(filepath):
        return None

    try:
        if filepath.lower().endswith('.kmz'):
            with zipfile.ZipFile(filepath, 'r') as kmz:
                for name in kmz.namelist():
                    if name.endswith('.kml'):
                        with kmz.open(name) as kml_file:
                            content = kml_file.read()
                            return _extrair_tempo_do_conteudo(content)
        else:
            with open(filepath, 'rb') as f:
                content = f.read()
                return _extrair_tempo_do_conteudo(content)
    except Exception as e:
        print(f"Erro ao extrair tempo de trajeto: {e}")
        return None

    return None


def _extrair_tempo_do_conteudo(content):
    """
    Extrai timestamps do conteúdo KML e calcula tempo de trajeto.
    """
    from datetime import datetime

    content_str = content.decode('utf-8', errors='ignore')
    timestamps = []

    # Padrão 1: <when> tags (usado em gx:Track)
    when_pattern = r'<when[^>]*>(.*?)</when>'
    matches = re.findall(when_pattern, content_str, re.DOTALL | re.IGNORECASE)

    for match in matches:
        ts = _parse_timestamp(match.strip())
        if ts:
            timestamps.append(ts)

    # Padrão 2: <TimeStamp><when> (usado em Placemarks)
    timestamp_pattern = r'<TimeStamp[^>]*>.*?<when[^>]*>(.*?)</when>.*?</TimeStamp>'
    matches = re.findall(timestamp_pattern, content_str, re.DOTALL | re.IGNORECASE)

    for match in matches:
        ts = _parse_timestamp(match.strip())
        if ts:
            timestamps.append(ts)

    # Padrão 3: <gx:TimeStamp> ou atributos de tempo
    gx_pattern = r'<gx:TimeStamp[^>]*>.*?<when[^>]*>(.*?)</when>.*?</gx:TimeStamp>'
    matches = re.findall(gx_pattern, content_str, re.DOTALL | re.IGNORECASE)

    for match in matches:
        ts = _parse_timestamp(match.strip())
        if ts:
            timestamps.append(ts)

    if len(timestamps) >= 2:
        timestamps.sort()
        tempo_total = (timestamps[-1] - timestamps[0]).total_seconds() / 60
        return round(tempo_total)

    return None


def _parse_timestamp(ts_str):
    """
    Faz parse de uma string de timestamp em vários formatos.
    """
    from datetime import datetime

    formatos = [
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S.%fZ',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%S.%f',
        '%Y-%m-%d %H:%M:%S',
        '%Y/%m/%d %H:%M:%S',
    ]

    # Remover timezone offset se presente (+00:00, -03:00, etc)
    ts_str = re.sub(r'[+-]\d{2}:\d{2}$', '', ts_str)

    for fmt in formatos:
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue

    return None


def validar_kml(filepath):
    """
    Valida se um arquivo KML/KMZ é válido e contém coordenadas.
    Retorna tuple (is_valid, message).
    """
    if not filepath:
        return False, "Arquivo não especificado"

    if not os.path.exists(filepath):
        return False, "Arquivo não encontrado"

    coords = extrair_coordenadas_kml(filepath)

    if not coords:
        return False, "Arquivo KML não contém coordenadas válidas"

    return True, f"Arquivo válido com {len(coords)} pontos"


def analisar_kml(filepath):
    """
    Analisa um arquivo KML e retorna todas as informações disponíveis.
    Retorna dict com km, tempo_minutos, coordenadas.
    """
    resultado = {
        'km': None,
        'tempo_minutos': None,
        'coordenadas': 0
    }

    coords = extrair_coordenadas_kml(filepath)
    if coords:
        resultado['coordenadas'] = len(coords)
        resultado['km'] = round(calcular_distancia_total(coords), 2)

    tempo = extrair_tempo_trajeto(filepath)
    if tempo:
        resultado['tempo_minutos'] = tempo

    return resultado
