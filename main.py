import os
import io
import re
import sys
import time
import base64
import unicodedata
import textdistance
import dotenv
from curl_cffi.requests import AsyncSession
from pydub import AudioSegment

# Console do Windows usa cp1252; sem isso, prints de transcrições com
# caracteres especiais (ex.: ♪) derrubam as threads com UnicodeEncodeError
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from queue import Queue, Empty
from threading import Thread
from bs4 import BeautifulSoup
from flask_socketio import SocketIO
from flask import Flask, render_template, request
#from vosk import Model, KaldiRecognizer
from openai import OpenAI  # for making OpenAI API calls

from fme.scraping import cifra_normalizer, cifra_scrolling_index, text_normalizer, text_scrolling_index

#testing with whisper
dotenv.load_dotenv()
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

global_audio_segments = []
global_current_audio_size_bytes = 0 # To track raw byte size for decision

# define a wrapper function for seeing how prompts affect transcriptions
def transcribe(audio_filepath, prompt: str):
    """Given a prompt, transcribe the audio file."""
    transcript = client.audio.transcriptions.create(
        file=open(audio_filepath, "rb"),
        model="whisper-1",
        prompt=prompt,
        # determinístico e fixado em pt-BR: reduz alucinações e evita que o
        # Whisper detecte o idioma errado em trechos instrumentais/ruidosos
        temperature=0,
        language="pt",
        # verbose_json devolve segmentos com no_speech_prob e avg_logprob,
        # permitindo descartar "letras falsas" geradas em trechos instrumentais
        response_format="verbose_json",
    )
    return transcript

# flask and socketio starting
app = Flask(__name__)
app.config['SECRET_KEY'] = 'agrae5tergae4565675ytaghdasfg'
socketio = SocketIO(app, cors_allowed_origins="*")

# global vars
audio_long = b''
count_samples = 1
CHANNELS = 1
FRAME_RATE = 16000
RECORD_SECONDS = 5
# janela de busca enxuta: limita o salto máximo a 4 linhas e deixa fora de
# alcance ecos do prompt (o Whisper às vezes alucina linhas da música que
# ainda não foram cantadas, e elas casam com raw 0.0 em qualquer ocorrência)
BLOCK_SIZE = 5
RECUO = 0
# incerteza normalizada: distância de Levenshtein / tamanho da maior string (0 = igual, 1 = nada a ver)
# calibrado com casos reais: matches corretos <= 0.43, errados/alucinações >= 0.63
SIMILARIDADE_MINIMA = 0.5
# penalidade por linha saltada: avançar para a linha seguinte é grátis; saltos
# maiores exigem matches cada vez melhores (evita que um falso positivo distante
# descarrile a posição). Voltar custa o dobro: a música quase sempre anda pra frente
PENALIDADE_SALTO = 0.05
# prompt dinâmico: quantas linhas da janela de comparação atual são enviadas
# como contexto ao Whisper (vocabulário esperado nos próximos trechos)
LINHAS_PROMPT = int(os.environ.get("LINHAS_PROMPT", 3))
# segmentos com no_speech_prob acima deste limite são tratados como
# instrumental/silêncio e descartados (evita que o prompt vire "letra falsa")
NO_SPEECH_PROB_MAX = float(os.environ.get("NO_SPEECH_PROB_MAX", 0.6))
# pasta local onde as cifras processadas são salvas/recuperadas
MUSICAS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "musicas")

recordings = Queue()
transcriptions = Queue()
time_log = []
CarryOn = True
# prompt dinâmico compartilhado entre as threads: find_best_line atualiza
# conforme a janela avança e speech_recognition lê a cada transcrição
# (atribuição de string é atômica sob o GIL, dispensa lock)
current_prompt = ""

def build_prompt(block) -> str:
    """Monta o prompt do Whisper com as primeiras LINHAS_PROMPT linhas da janela."""
    return "\n".join([line[0] for line in block[:LINHAS_PROMPT]])

# Lançat modelo Vosk
#path_modelo = r"models/vosk-model-small-pt-0.3"
#model = Model(path_modelo)
#rec = KaldiRecognizer(model, FRAME_RATE)
#rec.SetWords(False) #False means no probability word by word



async def fetch_url(url):
    # curl_cffi imita o fingerprint TLS do Chrome (CifraClub bloqueia clientes HTTP comuns com 403)
    async with AsyncSession(impersonate="chrome") as session:
        response = await session.get(url)
        return response.text, response.status_code, str(response.url)

def list_text(line: str) -> str:
    """
    Normaliza o texto dividindo as linhas em listas separatdas
    """
    line = str(line)
    line = re.sub(r'[^\w\s]', '', line)  # Remove punctuation
    line = re.sub(r'\s+', ' ', line).strip()  # Remove extra whitespace
    line = line.lower()  # Convert to lowercase
    line = remove_accents(line)

    return line

def remove_accents(input_str):
    """Removes accents from a string using Unicode normalization."""
    nfkd_form = unicodedata.normalize('NFKD', input_str)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)])

def normalize_entry(line: str) -> str:
    """
    Normaliza o texto dividindo as linhas em listas separatdas
    """
    line = line.replace("\n", "")
    line = re.sub(r'[^\w\s]', '', line)  # Remove punctuation
    line = re.sub(r'\s+', ' ', line).strip()  # Remove extra whitespace
    line = line.replace(" ", "-")
    line = line.lower()  # Convert to lowercase
    line = remove_accents(line)

    return line

def return_block(list: list[str], first_line: int, length: int) -> list[str]:
    """
    Returns a block from of len length starting from first line
    """

    #zerar first_line
    if first_line < 0:
        first_line = 0

    #evitar extrapolar index
    if first_line + length > len(list):
        length = len(list) - first_line

    # Create a new list to store the block of lines
    block = []

    # Iterate over the specified range of lines
    for i in range(first_line, first_line + length):
        # Append the current line to the block list
        block.append((list[i],i))

    # Return the block list
    return block

def partial_distance(a: str, b: str, metric=textdistance.levenshtein) -> float:
    """
    Distância parcial normalizada (0..1): desliza a string menor sobre a maior
    e retorna a melhor janela. Essencial porque o áudio é fatiado em blocos e o
    Whisper frequentemente transcreve só um fragmento da linha cantada.
    """
    s, l = (a, b) if len(a) <= len(b) else (b, a)
    if not s:
        return 1.0
    n = len(s)
    return min(metric.normalized_distance(s, l[i:i + n]) for i in range(0, len(l) - n + 1 or 1))

def best_line(text_list: list[str], phrase: str, current_line: int = -1, distance_metric=textdistance.levenshtein):
    best_score = float('inf')  # Initialize with infinity
    candidates = []

    for text_toup in text_list:
        # normaliza a linha como a transcrição (acentos/pontuação), senão
        # 'solidão,' vs 'solidao' cria um piso artificial de distância
        line_norm = list_text(text_toup[0])
        # distância parcial: tolera fragmentos de linha e normaliza pelo tamanho
        distance = partial_distance(phrase, line_norm, distance_metric)
        # penaliza saltos: ficar na linha atual ou ir à seguinte é grátis;
        # cada linha saltada encarece o candidato (voltar custa o dobro)
        if text_toup[1] > current_line:
            gap_penalty = PENALIDADE_SALTO * (text_toup[1] - current_line - 1)
        else:
            gap_penalty = 2 * PENALIDADE_SALTO * (current_line - text_toup[1])
        score = distance + gap_penalty
        if score < best_score:
            best_score = score
            candidates = [text_toup]
        elif score == best_score:
            # linhas repetidas (refrões) empatam; guarda todas as ocorrências
            candidates.append(text_toup)

    if not candidates:
        return -1, best_score, ""

    # em caso de empate, prioriza a ocorrência mais próxima a partir da posição
    # ATUAL (inclusive): fragmentos repetidos da mesma linha ficam parados, e a
    # música só anda quando uma linha distinta à frente casa melhor; se não
    # houver candidato na atual/à frente, fica na ocorrência mais próxima atrás
    ahead = [c for c in candidates if c[1] >= current_line]
    if ahead:
        best_text, bline = min(ahead, key=lambda c: c[1])
    else:
        best_text, bline = max(candidates, key=lambda c: c[1])

    return bline, best_score, best_text

def find_best_line(block):
    global CarryOn, current_prompt
    # posição atual na música; começa na primeira linha da janela inicial
    current_line = block[0][1] if block else -1
    while CarryOn:
        try:
            # timeout curto apenas para reavaliar CarryOn; silêncio longo
            # (intro/solo) não pode derrubar a thread
            phrase = transcriptions.get(block=True, timeout=2)
        except Empty:
            continue
        try:
            t0 = time.time()
            # normaliza a transcrição (pontuação/caixa/acentos) para comparar
            # em pé de igualdade com as linhas já normalizadas da letra
            phrase = list_text(phrase)
            # fragmentos muito curtos casariam com qualquer linha no partial matching
            if len(phrase) < 6:
                continue
            new_start_line, incerteza, line = best_line(block, phrase, current_line)
            incerteza = round(incerteza, 2)
            socketio.emit('index_info', {'index': "l" + str(new_start_line), 'incerteza': incerteza, 'minimo': SIMILARIDADE_MINIMA, 'phrase': phrase, 'line': line})
            print("sent data",  "l" + str(new_start_line), incerteza, SIMILARIDADE_MINIMA, phrase, line)
            if incerteza > SIMILARIDADE_MINIMA:
                continue
            current_line = new_start_line
            block = return_block(normalized_text, new_start_line-RECUO, BLOCK_SIZE)
            # janela avançou: renova o contexto enviado ao Whisper
            current_prompt = build_prompt(block)
            time_log.append({'find_best_line': time.time()-t0})
        except Exception as e:
            # loga e segue; um erro pontual não deve parar o seguimento
            print("Erro em find_best_line:", e)
    print("find_best_line encerrada")

def speech_recognition(): 
    global CarryOn
    while CarryOn:
        try:
            rec_frames = recordings.get(block=True, timeout=2)
        except Empty:
            continue
        try:
            t0 = time.time()
            # o prompt dinâmico com as próximas linhas da música guia o Whisper
            # (vocabulário e idioma), reduzindo alucinações em silêncio/ruído
            result = transcribe(rec_frames, prompt=current_prompt)
            # filtra segmento a segmento: no_speech_prob alto = instrumental,
            # o texto é alucinação (geralmente eco do prompt) e deve ser descartado
            kept = []
            for seg in (result.segments or []):
                if seg.no_speech_prob > NO_SPEECH_PROB_MAX:
                    print(f"segmento descartado (no_speech_prob={seg.no_speech_prob:.2f}, avg_logprob={seg.avg_logprob:.2f}):{seg.text}")
                else:
                    print(f"segmento aceito (no_speech_prob={seg.no_speech_prob:.2f}, avg_logprob={seg.avg_logprob:.2f}):{seg.text}")
                    kept.append(seg.text)
            text = " ".join(kept).strip()
            print("transcription:",text)
            if len(text) > 3:
                transcriptions.put(text)
            time_log.append({'speech_recognition': time.time()-t0})
        except Exception as e:
            print("Erro em speech_recognition:", e)
    print("speech_recognition encerrada")

# referências das threads ativas para impedir instâncias duplicadas
transcribe_thread = None
process_thread = None

def start_threads(block):
    global CarryOn, transcribe_thread, process_thread, current_prompt
    # cliques repetidos em START criavam threads duplicadas competindo pelas
    # mesmas filas, cada uma com sua própria janela -> saltos erráticos
    if transcribe_thread is not None and transcribe_thread.is_alive():
        print("Threads já em execução; START ignorado")
        return
    CarryOn = True
    current_prompt = build_prompt(block)
    transcribe_thread = Thread(target=speech_recognition, daemon=True)
    transcribe_thread.start()
    process_thread = Thread(target=find_best_line, args=(block,), daemon=True)
    process_thread.start()

def stop_threads():
    global CarryOn
    CarryOn = False
    print("Stopping threads")

# linhas HTML da última cifra carregada (acordes marcados com <b>), usadas
# pela rota /save_cifra; None enquanto nenhuma cifra foi buscada/aberta
current_html_text = None

# Abrir arquivo texto "Procissão.txt" separando linha a linha
with open("musicas/procissao.txt", "r", encoding="utf-8") as f:
    text = f.readlines()

normalized_text = text_normalizer(text)
indexed_text = text_scrolling_index(text)

starting_block = return_block(normalized_text,3,BLOCK_SIZE)

@socketio.on('launch_threads')
def launch_threads(data):
    if data == 'go':
        global starting_block
        print("launch_threads")
        start_threads(starting_block)
    else:
        stop_threads()
        #empty queues
        while not recordings.empty():
            recordings.get()
        while not transcriptions.empty():
            transcriptions.get()
        print("Queues emptied")

@socketio.on('connect')
def handle_connect():
    print("Client connected")
    
@socketio.on('audio_data')
def handle_audio_data(data):

    global global_audio_segments
    global global_current_audio_size_bytes

    try:
        # Decode base64 audio data
        audio_bytes_base64 = data['audio']
        audio_bytes_decoded = base64.b64decode(audio_bytes_base64)

        audio_chunk = AudioSegment.from_file(io.BytesIO(audio_bytes_decoded), format="webm")

        global_audio_segments.append(audio_chunk)
        global_current_audio_size_bytes += len(audio_bytes_decoded) # Tracking original byte size from browser

        if global_current_audio_size_bytes < 10000:
            print(f"Accumulating audio: Current size {global_current_audio_size_bytes} bytes. Continuing...")
            return

        print("Threshold reached. Combining and saving audio...")

        combined_audio = AudioSegment.empty() # Start with an empty segment
        for segment in global_audio_segments:
            combined_audio += segment

        webm_path = 'audio.webm'
        combined_audio.export(webm_path, format="webm")
        print(f"Successfully saved combined audio to {webm_path}")

        global_audio_segments = []
        global_current_audio_size_bytes = 0

        try:
            recordings.put(webm_path)
        except Exception as e:
            print(f"Error reading webm file: {str(e)}")
            socketio.emit('error', f"Error reading WAV file: {str(e)}")
            return

    except Exception as e:
        print(f"Error processing audio data: {str(e)}")
        socketio.emit('error', str(e))
        return

@app.route('/')
def index():
    return render_template('index.html', indexed_text=indexed_text, found_page='true')

@app.route('/search', methods=['GET'])
async def search():
    global starting_block
    global normalized_text
    global indexed_text
    global current_html_text

    artista = request.args.get('artista')
    musica = request.args.get('musica')
    key = request.args.get('key')

    print(artista, musica, key)
    if key:
        url_cifraclub = "https://www.cifraclub.com.br/" + artista + "/" + musica + "/#key=" + key
    else:
        url_cifraclub = "https://www.cifraclub.com.br/" + artista + "/" + musica + "/"
    result, status, return_url = await fetch_url(url_cifraclub)
    print(url_cifraclub)
    print(return_url)
    if status == 200:
        if str(return_url) != str(url_cifraclub):
            if str(return_url) == "https://www.cifraclub.com.br/" + artista + "/":
                # Artista encontrado, musica nao
                indexed_text = {'message':'Encontramos o seu artista, mas não a sua música! Veja o que tem no cifra club:', 'url': return_url}
                return render_template('index.html', indexed_text=indexed_text, found_page='false')
            elif str(return_url) == "https://www.cifraclub.com.br/":
                # Artista nao encontrado
                indexed_text = {'message':'Não encontramos o seu artista! Veja o que tem no cifra club:', 'url': return_url}
                return render_template('index.html', indexed_text=indexed_text, found_page='false')

        dom = BeautifulSoup(result, "html.parser")
        body = dom.body
        pre = body.find("pre")

        # CifraClub injeta zero-width spaces (\u200b) que quebram a detecção de acordes
        pre_text = pre.get_text().replace("\u200b", "")
        normalized_text = cifra_normalizer(pre_text.split('\n'))
        # decode_contents() retorna o HTML interno sem a tag <pre ...> (que agora tem atributos);
        # remove também as <div>s de linha que o CifraClub passou a usar
        inner_html = pre.decode_contents().replace("\u200b", "")
        inner_html = re.sub(r'</?div[^>]*>', '', inner_html)
        html_text = inner_html.split('\n')
        current_html_text = html_text
        indexed_text = cifra_scrolling_index(normalized_text, html_text, title=musica, author=artista)
        # janela inicial a partir da linha 0: começar em 3 tornava as três
        # primeiras linhas da música inalcançáveis no início do seguimento
        starting_block = return_block(normalized_text, 0, BLOCK_SIZE)

        return render_template('index.html', indexed_text=indexed_text, found_page='true')

    else:
        # CifraClub hoje responde 404 para artista/música inexistentes (sem redirecionamento)
        indexed_text = {'message': 'Não encontramos essa cifra! Confira a grafia ou busque no Cifra Club:',
                        'url': 'https://www.cifraclub.com.br/'}
        return render_template('index.html', indexed_text=indexed_text, found_page='false')
    
@app.route('/save_cifra', methods=['POST'])
def save_cifra():
    """Salva a cifra processada atual em um .txt editável dentro de musicas/."""
    if current_html_text is None or not isinstance(indexed_text, dict) or 'title' not in indexed_text:
        return {'ok': False, 'error': 'Nenhuma cifra carregada para salvar. Busque uma cifra primeiro.'}, 400

    title = str(indexed_text.get('title', '')).strip()
    author = str(indexed_text.get('author', '')).strip()
    filename = normalize_entry(author) + "__" + normalize_entry(title) + ".txt"
    path = os.path.join(MUSICAS_DIR, filename)
    try:
        # mesmo formato do musicas/procissao.txt: título, autor, linha em
        # branco e o conteúdo (acordes preservados nas tags <b>)
        with open(path, "w", encoding="utf-8") as f:
            f.write(title + "\n" + author + "\n\n")
            f.write("\n".join(line.rstrip("\r") for line in current_html_text))
            f.write("\n")
    except OSError as e:
        return {'ok': False, 'error': f'Erro ao salvar arquivo: {e}'}, 500
    return {'ok': True, 'arquivo': filename}

@app.route('/saved_cifras', methods=['GET'])
def saved_cifras():
    """Lista as cifras salvas em musicas/ (título e autor nas duas primeiras linhas)."""
    cifras = []
    for name in sorted(os.listdir(MUSICAS_DIR)):
        if not name.endswith(".txt"):
            continue
        try:
            with open(os.path.join(MUSICAS_DIR, name), "r", encoding="utf-8") as f:
                file_title = f.readline().strip()
                file_author = f.readline().strip()
        except OSError:
            continue
        cifras.append({'arquivo': name, 'title': file_title, 'author': file_author})
    return {'cifras': cifras}

@app.route('/load_cifra', methods=['GET'])
def load_cifra():
    """Recarrega uma cifra salva, reprocessando o texto como na busca original."""
    global starting_block
    global normalized_text
    global indexed_text
    global current_html_text

    # basename barra path traversal (ex.: ..\..\arquivo)
    arquivo = os.path.basename(request.args.get('arquivo', ''))
    path = os.path.join(MUSICAS_DIR, arquivo)
    if not arquivo.endswith('.txt') or not os.path.isfile(path):
        indexed_text = {'message': 'Cifra salva não encontrada.', 'url': '/'}
        return render_template('index.html', indexed_text=indexed_text, found_page='false')

    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    title = lines[0].strip() if lines else arquivo
    author = lines[1].strip() if len(lines) > 1 else ''
    html_text = lines[3:]
    # remove as tags <b> dos acordes para normalizar como texto puro,
    # reaproveitando o mesmo pipeline da busca no CifraClub
    plain_text = [re.sub(r'<[^>]+>', '', line) for line in html_text]
    normalized_text = cifra_normalizer(plain_text)
    indexed_text = cifra_scrolling_index(normalized_text, html_text, title=title, author=author)
    current_html_text = html_text
    starting_block = return_block(normalized_text, 0, BLOCK_SIZE)

    return render_template('index.html', indexed_text=indexed_text, found_page='true')

if __name__ == '__main__':
    socketio.run(app, debug=True)