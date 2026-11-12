# 🎵 FollowMe

**Teleprompter musical com acompanhamento automático por voz** — um "karaokê reverso": em vez de você seguir a tela, a tela segue você.

O FollowMe escuta você cantar/tocar pelo microfone, transcreve o áudio em tempo real com o [Whisper](https://platform.openai.com/docs/guides/speech-to-text) (OpenAI) e descobre em que linha da letra/cifra você está, rolando a tela automaticamente para acompanhar a música.

## Como funciona

```
Navegador (microfone) ──WebSocket──► Flask-SocketIO
                                          │
                              [fila recordings]  ◄── chunks de áudio .webm
                                          │
                           Thread 1: speech_recognition()
                              └── Whisper API → texto transcrito
                                          │
                              [fila transcriptions]
                                          │
                           Thread 2: find_best_line()
                              └── compara transcrição vs. letra
                                  (distância de Levenshtein)
                                          │
                              emit('index_info') ──► navegador rola até a linha
```

1. O navegador captura o microfone e envia chunks de áudio WebM (base64) via WebSocket.
2. O servidor acumula os chunks (~10 KB) e os envia para transcrição no Whisper.
3. O texto transcrito é comparado com uma **janela deslizante** de linhas da letra usando distância de Levenshtein (`textdistance`).
4. Quando a similaridade é boa o suficiente, o índice da linha é emitido ao front-end, que rola a tela até a posição atual.

## Funcionalidades

- 🎤 Reconhecimento de voz em tempo real (OpenAI Whisper)
- 📜 Rolagem automática da letra/cifra conforme você canta
- 🔎 Busca de cifras no [CifraClub](https://www.cifraclub.com.br/) por artista, música e tom (`/search?artista=...&musica=...&key=...`)
- 🎼 Separação automática de linhas de acordes e de letra
- 🐳 Pronto para deploy em container (Dockerfile para Cloud Run / gunicorn)

## Estrutura do projeto

| Caminho | Descrição |
|---|---|
| `main.py` | Servidor Flask + SocketIO, threads de transcrição e matching |
| `fme/scraping.py` | Normalização de texto e indexação de cifras (CifraClub) |
| `templates/index.html` | Interface web (microfone, busca e rolagem) |
| `static/icons/` | Ícones da interface |
| `musicas/` | Letras locais (música padrão: `procissao.txt`) |
| `Dockerfile` | Imagem para deploy (Python 3.11 + FFmpeg + gunicorn) |

## Requisitos

- Python 3.11+
- [FFmpeg](https://ffmpeg.org/) instalado no sistema (necessário para o `pydub` processar WebM)
- Chave de API da OpenAI

## Instalação

```bash
git clone https://github.com/nikinuk/FollowMe.git
cd FollowMe

python -m venv venv
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

pip install -r requirements.txt
pip install openai pydub  # dependências ainda não listadas no requirements.txt
```

Configure a chave da OpenAI como variável de ambiente:

```powershell
# Windows (PowerShell)
$env:OPENAI_API_KEY = "sua-chave-aqui"
```

```bash
# Linux/Mac
export OPENAI_API_KEY="sua-chave-aqui"
```

## Uso

```bash
python main.py
```

Abra `http://localhost:5000` no navegador:

1. **Buscar uma cifra**: informe artista e música (busca no CifraClub) — ou use a música padrão carregada.
2. **Iniciar**: clique no botão de início e permita o acesso ao microfone.
3. **Cante/toque**: a tela rola automaticamente acompanhando a sua posição na música.

## Parâmetros de ajuste (em `main.py`)

| Constante | Padrão | Descrição |
|---|---|---|
| `BLOCK_SIZE` | `10` | Tamanho da janela de linhas onde a posição atual é procurada |
| `RECUO` | `0` | Linhas de recuo ao reposicionar a janela após um match |
| `SIMILARIDADE_MINIMA` | `18` | Distância máxima (Levenshtein) para aceitar um match e avançar a janela |

## Deploy com Docker

```bash
docker build -t followme .
docker run -p 8080:8080 -e PORT=8080 -e OPENAI_API_KEY="sua-chave" followme
```

O container usa gunicorn e foi pensado para o Google Cloud Run (veja `Intruções_gcloud.txt`).

## Notas e limitações conhecidas

- **Um usuário por vez**: o estado (letra atual, filas, janelas) é global no servidor.
- A música padrão é carregada de `musicas/procissao.txt` na inicialização.
- O projeto já usou [Vosk](https://alphacephei.com/vosk/) para reconhecimento offline (código comentado em `main.py`); hoje usa o Whisper via API.
- A "incerteza" é a distância de Levenshtein bruta — linhas longas tendem a ter distâncias maiores.

## Licença

Projeto pessoal — sem licença definida ainda.
