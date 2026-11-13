import re

# token de acorde: fundamental A-G, acidente opcional e qualidades usuais
# (m, maj, min, dim, aug, sus, add, M, +, -, º), dígitos, parênteses e baixo
# invertido (/G#). Palavras comuns como "Deus" ou "Casa" não casam.
CHORD_TOKEN_RE = re.compile(
    r"^[A-G](?:#|b)?(?:(?:maj|min|sus|add|dim|aug|m|M|\+|-|º|°|[0-9])|\([^)]*\))*"
    r"(?:/[A-G](?:#|b)?[0-9]*)?$"
)

# marcador de paginação dos blocos de tablatura do CifraClub ("Parte 1 de 9")
TAB_PART_RE = re.compile(r"^parte\s+\d+\s+de\s+\d+$", re.IGNORECASE)

def _chordish(token: str) -> bool:
    """Token é acorde, mesmo com parênteses soltos colados (ex.: '(Em6')."""
    return bool(CHORD_TOKEN_RE.match(token) or CHORD_TOKEN_RE.match(token.strip("()")))

def cifra_normalizer(lines: list[str]) -> list[str]:
    #eliminate chord lines: casa cada token inteiro contra CHORD_TOKEN_RE;
    #a heurística antiga quebrava acordes como F#m7(5-/11) em pseudo-palavras
    #("5-", "11") e deixava a linha passar como letra
    new_lines = []
    th = 0.8
    for line in lines:
        # tokens só de pontuação (ex.: "(" e ")" em "( Em6 Em6/B )") são
        # neutros: não contam nem como acorde nem como palavra de letra
        tokens = [t for t in line.split() if re.search(r"\w", t)]
        if not tokens:
            continue
        chordish = sum(1 for t in tokens if _chordish(t))
        if chordish / len(tokens) < th:
            new_lines.append(line.replace("\n", ""))

    lines = new_lines

    #eliminate between square brackets []
    new_lines = []
    for line in lines:
        if "[" in line and "]" in line:
            continue
        new_lines.append(line)

    lines = new_lines

    # eliminate tab lines (many - char) and tab part markers ("Parte 1 de 9")
    new_lines = []
    for line in lines:
        if line.count("-") > 5:
            continue
        if TAB_PART_RE.match(line.strip()):
            continue
        new_lines.append(line)

    lines = new_lines

    new_lines = []
    for line in lines:
        new_lines.append(re.sub(r"\s+", " ", line).strip().lower())

    return new_lines

def cifra_scrolling_index(norm_lines: list[str], text_to_index: list[str], **kwargs) -> dict:
    indexed_text = {}
    if kwargs:
        indexed_text['title'] = kwargs['title']
        indexed_text['author'] = kwargs['author']
        i=0
    else:
        indexed_text['title'] = text[0]
        indexed_text['author'] = text[1]
        i=3

    j = 0
    text_content  = []
    for i, norm_line in enumerate(norm_lines):
        tti = text_to_index[j:]
        for line in tti:
            if norm_line in re.sub(r"\s+", " ", line).strip().lower():
                text_content.append(( 'l' + str(i) , line.replace("\n", "")))
                j += 1
                break
            else:
                text_content.append(('<BR>', "<pre>" + line.replace("\n", "") + "</pre>"))
                j += 1
            

    indexed_text['content'] = text_content
    indexed_text['content_length'] = len(text_content)
    indexed_text['index_count'] = len(norm_lines)

    return indexed_text

def text_scrolling_index(text: list[str], **kwargs) -> list[str]:
    indexed_text = {}
    text_content = []
    if kwargs:
        indexed_text['title'] = kwargs['title']
        indexed_text['author'] = kwargs['author']
        i=0
    else:
        indexed_text['title'] = text[0]
        indexed_text['author'] = text[1]
        i=3
    ln = 2
    for line in text[i:]:
        if line != "\n":
            if "<b>" not in line.lower():
                text_content.append(( 'l' +str(ln) , line.replace("\n", "")))
                ln += 1
            else:
                text_content.append(('<BR>', "<pre>" + line.replace("\n", "") + "</pre>"))
        else:
            text_content.append(( '<BR>', '<BR>' ))
    indexed_text['content'] = text_content
    indexed_text['content_length'] = len(text_content)
    indexed_text['index_count'] = ln - 1
    return indexed_text

def text_normalizer(text: list[str]) -> list[str]:
    """
    Normaliza o texto dividindo as linhas em listas separatdas
    """
    noromalized = []
    for line in text:
        if line == "\n":
            continue
        line = line.replace("\n", "")
        line = re.sub(r'[^\w\s]', '', line)  # Remove punctuation
        line = re.sub(r'\s+', ' ', line).strip()  # Remove extra whitespace
        line = line.lower()  # Convert to lowercase
        noromalized.append(line)
    return noromalized