import re

def cifra_normalizer(lines: list[str]) -> list[str]:
    #eliminate chord lines
    #chord_regex = r"^[A-G][ABCDEFGabcdefgm#°+-/()M123456789]"
    chord_regex = r"^[A-G]"
    new_lines = []
    for line in lines:
        cs = 0
        notcs = 0
        th = 0.8
        for word in line.replace("/", " ").replace("(", "").replace(")", "").split():
            if re.match(chord_regex, word):
                cs += 1
            else:
                notcs += 1
        if cs + notcs == 0:
            continue
        if cs / (cs + notcs) < th:
            new_lines.append(line.replace("\n", ""))

    lines = new_lines

    #eliminate between square brackets []
    new_lines = []
    for line in lines:
        if "[" in line and "]" in line:
            continue
        new_lines.append(line)

    lines = new_lines

    # eliminate tab lines (many - char)
    new_lines = []
    for line in lines:
        if line.count("-") > 5:
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