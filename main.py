import os
import io
import re
import time
import base64
import unicodedata
import textdistance
from curl_cffi.requests import AsyncSession
from pydub import AudioSegment

from queue import Queue
from threading import Thread
from bs4 import BeautifulSoup
from flask_socketio import SocketIO
from flask import Flask, render_template, request
#from vosk import Model, KaldiRecognizer
from openai import OpenAI  # for making OpenAI API calls

from fme.scraping import cifra_normalizer, cifra_scrolling_index, text_normalizer, text_scrolling_index

#testing with whisper
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

global_audio_segments = []
global_current_audio_size_bytes = 0 # To track raw byte size for decision

# define a wrapper function for seeing how prompts affect transcriptions
def transcribe(audio_filepath, prompt: str) -> str:
    """Given a prompt, transcribe the audio file."""
    transcript = client.audio.transcriptions.create(
        file=open(audio_filepath, "rb"),
        model="whisper-1",
        prompt=prompt,
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
BLOCK_SIZE = 10
RECUO = 0
SIMILARIDADE_MINIMA = 18

recordings = Queue()
transcriptions = Queue()
time_log = []
CarryOn = True

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

def best_line(text_list: list[str], phrase: str, distance_metric=textdistance.levenshtein):
    best_distance = float('inf')  # Initialize with infinity
    bline = -1

    for text_toup in text_list:
        distance = distance_metric(phrase, text_toup[0])
        if distance < best_distance:
            best_distance = distance
            bline = text_toup[1]
            best_text = text_toup[0]

    return bline, best_distance, best_text

def find_best_line(block):
    global CarryOn
    try:
        while CarryOn:
            t0 = time.time()
            phrase = transcriptions.get(block=True, timeout=30)
            new_start_line, incerteza, line = best_line(block, phrase)
            socketio.emit('index_info', {'index': "l" + str(new_start_line), 'incerteza': incerteza, 'minimo': SIMILARIDADE_MINIMA, 'phrase': phrase, 'line': line})
            print("sent data",  "l" + str(new_start_line), incerteza, SIMILARIDADE_MINIMA, phrase, line)
            if incerteza > SIMILARIDADE_MINIMA:
                continue
            block = return_block(normalized_text, new_start_line-RECUO, BLOCK_SIZE)
            time_log.append({'find_best_line': time.time()-t0})
    except KeyboardInterrupt:
        print("KeyboardInterrupt on find_best_line")
    except Exception as e:
        print(e)
    finally:
        print("Exiting")
        #exit system on keyboard interrupt
        CarryOn = False

def speech_recognition(prompt): 
    global CarryOn
    try:
        while CarryOn:
            t0 = time.time()
            rec_frames = recordings.get()
            #rec.AcceptWaveform(rec_frames)
            #result = rec.Result()
            result = transcribe(rec_frames, prompt="")
            text = result.text
            print("transcription:",text)
            if len(text) > 3:
                transcriptions.put(text)
            time_log.append({'speech_recognition': time.time()-t0})
            
    except KeyboardInterrupt:
        print("KeyboardInterrupt on speech_recognition")

def start_threads(block):
    global CarryOn
    CarryOn = True
    try:
        prompts = "\n".join([line[0] for line in starting_block])
        transcribe = Thread(target=speech_recognition, args=(prompts,), daemon=True)
        transcribe.start()
        process = Thread(target=find_best_line, args=(block,), daemon=True)
        process.start()
    except KeyboardInterrupt:
        transcribe.join()
        process.join()
        print("KeyboardInterrupt on start_recording")

def stop_threads():
    global CarryOn
    CarryOn = False
    try:
        transcribe.join()
        #process.join()
        index()
        print("Stopping threads")
    except:
        pass

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
        indexed_text = cifra_scrolling_index(normalized_text, html_text, title=musica, author=artista)
        starting_block = return_block(normalized_text,3,BLOCK_SIZE)

        return render_template('index.html', indexed_text=indexed_text, found_page='true')

    else:
        # CifraClub hoje responde 404 para artista/música inexistentes (sem redirecionamento)
        indexed_text = {'message': 'Não encontramos essa cifra! Confira a grafia ou busque no Cifra Club:',
                        'url': 'https://www.cifraclub.com.br/'}
        return render_template('index.html', indexed_text=indexed_text, found_page='false')
    
if __name__ == '__main__':
    socketio.run(app, debug=True)