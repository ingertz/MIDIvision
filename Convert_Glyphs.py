import collections
import re

# 글꼴 글자로 그린 악보 읽기
#
# MuseScore 같은 프로그램에서 내보낸 PDF는 음표 머리·쉼표·꼬리·음자리표를 도형이 아니라 "악보 글꼴의 글자"로
# 찍습니다 (오선·기둥·세로줄은 선, 빔은 사각형). 악보 글꼴은 SMuFL 표준을 따르므로 글자 번호만 보면
# 무슨 기호인지 정확히 알 수 있습니다: U+E0A4 = 검은 음표 머리, U+E0A9 = X 머리, U+E4E5 = 4분쉼표 ...
# 윤곽선의 크기로 기호를 짐작하는 방식(Convert_Vector.classify)과 달리 글꼴이 달라도 그대로 통합니다.
#
# 여기서는 글자를 Convert_Vector 가 쓰는 (종류, 자리) 로 바꾸는 일만 합니다. 선과 빔을 읽고 음표로 엮는 것은
# Convert_Vector 가 도형 악보와 똑같이 처리합니다.

# ---------------------------------------------------------------------------------------------
# 글꼴: 글자 코드 -> 유니코드, 글자 너비
# ---------------------------------------------------------------------------------------------
def unicode_map(font):
    """글꼴의 ToUnicode 표 {코드: 문자열}"""
    stream = font.get('/ToUnicode')
    table = {}
    if stream is None:
        return table
    data = stream.get_object().get_data().decode('latin-1')

    def text(hexs):
        raw = bytes.fromhex(hexs)
        return raw.decode('utf-16-be', errors='replace') if len(raw) >= 2 else chr(raw[0])

    for block in re.findall(r'beginbfchar(.*?)endbfchar', data, re.S):
        for src, dst in re.findall(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', block):
            table[int(src, 16)] = text(dst)
    for block in re.findall(r'beginbfrange(.*?)endbfrange', data, re.S):
        for a, b, dst in re.findall(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', block):
            base = text(dst)
            for k, code in enumerate(range(int(a, 16), int(b, 16) + 1)):
                table[code] = base[:-1] + chr(ord(base[-1]) + k)
    return table

def font_tables(page, cache):
    """
    쪽의 글꼴들: {b'/F1': {'base': 글꼴 이름, 'unicode': {코드: 문자열}, 'widths': {코드: 너비(글자 크기의 배수)}, 'bytes': 코드의 바이트 수}}
    cache: 같은 글꼴을 쪽마다 다시 읽지 않도록 넘겨 주는 dict
    """
    out = {}
    resources = page.get('/Resources')
    fonts = resources.get_object().get('/Font') if resources is not None else None
    if fonts is None:
        return out
    fonts = fonts.get_object()
    for name in fonts:
        ref = fonts[name]
        key = getattr(ref, 'idnum', None)
        if key is not None and key in cache:
            out[name.encode('latin-1')] = cache[key]
            continue
        font = ref.get_object()
        info = {'base': str(font.get('/BaseFont', '')).split('+')[-1].lstrip('/'),
                'unicode': {}, 'widths': {}, 'bytes': 2 if str(font.get('/Subtype')) == '/Type0' else 1}
        try:
            info['unicode'] = unicode_map(font)
            widths = font.get('/Widths')
            if widths is not None:
                first = int(font.get('/FirstChar', 0))
                for k, w in enumerate(widths.get_object()):
                    info['widths'][first + k] = float(w.get_object() if hasattr(w, 'get_object') else w) / 1000
        except Exception:
            pass                       # 표를 못 읽은 글꼴의 글자는 모르는 글자로 남음
        if key is not None:
            cache[key] = info
        out[name.encode('latin-1')] = info
    return out

ESCAPES = {b'n': b'\n', b'r': b'\r', b't': b'\t', b'b': b'\b', b'f': b'\f'}

def string_bytes(token):
    """PDF 문자열 토큰 '(...)' 또는 '<...>' -> 바이트"""
    if token[:1] == b'<':
        digits = re.sub(rb'\s', b'', token[1:-1])
        return bytes.fromhex((digits + b'0' * (len(digits) % 2)).decode())
    body = token[1:-1]
    body = re.sub(rb'\\([0-7]{1,3})', lambda m: bytes([int(m.group(1), 8) & 255]), body)
    return re.sub(rb'\\(.)', lambda m: ESCAPES.get(m.group(1), m.group(1)), body, flags=re.S)

# ---------------------------------------------------------------------------------------------
# SMuFL 글자 -> 기호
# ---------------------------------------------------------------------------------------------
# 글자 번호: (종류, 왼쪽, 아래, 오른쪽, 위, 부가 정보).  좌표는 글자 원점 기준, 오선 간격 단위, 위쪽이 +
# 너비는 PDF에 글꼴의 글자 너비가 들어 있으면 그것을 씀 (글꼴마다 조금씩 다름)
HEAD = (0, -0.5, 1.18, 0.5)
REST = (0, -1.0, 1.1, 1.0)        # 쉼표는 가운데 높이와 오른쪽 끝만 쓰므로 원점 위아래로 같은 크기
SMUFL = {
    0xE0A2: ('whole', 0, -0.5, 1.69, 0.5, {}),
    0xE0A3: ('half', *HEAD, {}),
    0xE0A4: ('black', *HEAD, {}),
    0xE0A7: ('whole', 0, -0.5, 1.69, 0.5, {'head': 'x'}),
    0xE0A8: ('half', *HEAD, {'head': 'x'}),
    0xE0A9: ('x', *HEAD, {}),
    0xE0AA: ('x', *HEAD, {}),
    0xE0B1: ('whole', 0, -0.5, 1.69, 0.5, {'head': 'circle-x'}),
    0xE0B2: ('half', *HEAD, {'head': 'circle-x'}),
    0xE0B3: ('x', *HEAD, {'head': 'circle-x'}),
    0xE0BC: ('half', *HEAD, {'head': 'triangle'}),
    0xE0BD: ('half', *HEAD, {'head': 'triangle'}),
    0xE0BE: ('black', *HEAD, {'head': 'triangle'}),
    0xE0D9: ('half', *HEAD, {'head': 'diamond'}),
    0xE0DB: ('black', *HEAD, {'head': 'diamond'}),
    0xE0DD: ('half', *HEAD, {'head': 'diamond'}),
    0xE100: ('slash', 0, -1.0, 1.46, 1.0, {}),
    0xE101: ('slash', 0, -1.0, 1.46, 1.0, {}),
    0xE4E3: ('rest1', 0, -0.5, 1.13, 0.0, {}),
    0xE4E4: ('rest2', 0, 0.0, 1.13, 0.5, {}),
    0xE4E5: ('rest4', *REST, {}),
    0xE4E6: ('rest8', *REST, {}),
    0xE4E7: ('rest16', *REST, {}),
    0xE4E8: ('rest32', *REST, {}),
    0xE240: ('flag', 0, -3.24, 1.06, 0.0, {}),
    0xE241: ('flag', 0, 0.0, 1.22, 3.23, {}),
    0xE242: ('flag2', 0, -3.25, 1.12, 0.0, {}),
    0xE243: ('flag2', 0, 0.0, 1.16, 3.25, {}),
    0xE244: ('flag3', 0, -3.25, 1.04, 0.5, {}),
    0xE245: ('flag3', 0, -0.5, 1.16, 3.25, {}),
    0xE5E7: ('ring', 0, 0.0, 1.0, 1.1, {}),          # 음표 위의 ○ (오픈 하이햇). 금관의 "열기" 기호를 빌려 씀
    0xE1E7: ('dot', 0, -0.2, 0.4, 0.2, {}),
    0xE044: ('dot', 0, -0.2, 0.4, 0.2, {}),          # 도돌이표의 점
    0xE260: ('flat', 0, -0.7, 0.9, 1.76, {}),
    0xE261: ('natural', 0, -1.36, 0.67, 1.36, {}),
    0xE262: ('sharp', 0, -1.4, 1.0, 1.4, {}),
    0xE264: ('dflat', 0, -0.7, 1.64, 1.76, {}),
    0xE050: ('clefG', 0, -2.6, 2.7, 4.4, {}),
    0xE052: ('clefG8', 0, -3.5, 2.7, 4.4, {}),
    0xE062: ('clefF', 0, -2.5, 2.7, 1.0, {}),
    0xE069: ('clefP', 0, -1.0, 1.5, 1.0, {}),
    0xE06A: ('clefP', 0, -1.0, 1.5, 1.0, {}),
    0xE08A: ('timesig', 0, -1.0, 1.7, 1.0, {'value': 'C'}),
    0xE08B: ('timesig', 0, -1.4, 1.7, 1.4, {'value': 'c'}),
    0xE047: ('segno', 0, -1.5, 2.2, 1.5, {}),
    0xE048: ('coda', 0, -1.5, 3.8, 2.0, {}),
    0xE049: ('coda', 0, -1.5, 3.8, 2.0, {}),
    0xE500: ('simile1', 0, -1.0, 2.1, 1.0, {}),
    0xE501: ('simile2', 0, -1.0, 3.1, 1.0, {}),
}
for digit in range(10):
    SMUFL[0xE080 + digit] = ('timesig', 0, -1.0, 1.7, 1.0, {'value': digit})
    SMUFL[0xE880 + digit] = ('tuplet3' if digit == 3 else 'tupletN', 0, 0.0, 1.2, 1.45, {'value': digit})

# 메트로놈 표시의 음표 글자: 박 단위 (4분음표 = 1)
MET_NOTES = {0xECA3: 2.0, 0xECA5: 1.0, 0xECA7: 0.5, 0xE1D3: 2.0, 0xE1D5: 1.0, 0xE1D7: 0.5}
MET_DOTS = (0xECB7, 0xE1E7)

def is_music(code):
    return 0xE000 <= code <= 0xF8FF

def read_glyphs(records, fonts):
    """
    records: Convert_Vector.parse_shapes 가 모은 글자 [(BT 블록 번호, 글꼴 이름, 문자열 토큰들, x, y, 글자 크기)]
    반환: (기호 [(종류, (x0, y0, x1, y1), 부가 정보)], 글 [(문자열, x, y, 크기, 기울임꼴인지)],
           악보 글자 [(글자 번호, x, y, 크기)])
    """
    music, texts = [], collections.OrderedDict()
    for block, name, tokens, x, y, size in records:
        font = fonts.get(name)
        if font is None or size <= 0:
            continue
        raw = b''.join(string_bytes(t) for t in tokens)
        step = font['bytes']
        codes = [int.from_bytes(raw[i:i + step], 'big') for i in range(0, len(raw) - step + 1, step)]
        chars = [font['unicode'].get(code) or (chr(code) if code < 256 and not font['unicode'] else '') for code in codes]
        if len(codes) == 1 and chars[0] and is_music(ord(chars[0][0])):
            music.append((ord(chars[0][0]), x, y, size, font['widths'].get(codes[0])))
        else:
            key = (block, name)
            old = texts.get(key)
            texts[key] = (old[0] + ''.join(chars) if old else ''.join(chars), old[1] if old else x, old[2] if old else y,
                          size, 'italic' in font['base'].lower() or 'oblique' in font['base'].lower())
    # 꾸밈음은 같은 글자를 작게 찍은 것: 그 쪽에서 가장 흔한 음표 머리 크기보다 뚜렷이 작은 글자는 뺌
    sizes = collections.Counter(round(size, 1) for code, x, y, size, w in music if SMUFL.get(code, ('',))[0] in ('black', 'x', 'half'))
    normal = sizes.most_common(1)[0][0] if sizes else None
    marks = []
    for code, x, y, size, width in music:
        entry = SMUFL.get(code)
        if entry is None or (normal and size < normal * 0.85):
            continue
        kind, x0, y0, x1, y1, extra = entry
        sp = size / 4
        right = x + (width * size if width and kind not in ('rest4', 'rest8', 'rest16', 'rest32') else x1 * sp)
        marks.append((kind, (x + x0 * sp, y - y1 * sp, right, y - y0 * sp), extra))
    return marks, list(texts.values()), [(code, x, y, size) for code, x, y, size, w in music]

def staff_space(music):
    """악보 글자의 크기에서 잰 오선 간격 (글자 크기 = 오선 간격 4칸). 음표가 없으면 None"""
    sizes = collections.Counter(round(size, 2) for code, x, y, size in music if SMUFL.get(code, ('',))[0] in ('black', 'x', 'half', 'whole'))
    return sizes.most_common(1)[0][0] / 4 if sizes else None

def tuplet_marks(texts, u):
    """잇단음표 숫자를 글 글꼴의 기울임꼴 숫자로 찍는 프로그램(MuseScore 4)용: [(종류, box, 부가 정보)]"""
    out = []
    for string, x, y, size, italic in texts:
        s = string.strip()
        if italic and s.isdigit() and len(s) == 1 and s != '0' and 0.4 * u < size / 4 < 2.5 * u:
            h = size * 0.7
            out.append(('tuplet3' if s == '3' else 'tupletN', (x, y - h, x + size * 0.5, y), {'value': int(s)}))
    return out

def read_tempo(texts, music, u):
    """메트로놈 표시 "♩ = 152" -> 4분음표 기준 BPM. 없으면 None"""
    notes = [(x, y, MET_NOTES[code]) for code, x, y, size in music if code in MET_NOTES]
    dots = [(x, y) for code, x, y, size in music if code in MET_DOTS]
    best = None
    for string, x, y, size, italic in texts:
        m = re.search(r'=\s*(\d{2,3})(?!\d)', string)
        if not m:
            continue
        near = [(abs(x - nx), nx, ny, beats) for nx, ny, beats in notes if -2 * u < x - nx < 12 * u and abs(y - ny) < 3 * u]
        if not near:
            continue
        _, nx, ny, beats = min(near)
        if any(0 < dx - nx < 3 * u and abs(dy - ny) < 2 * u and dx < x for dx, dy in dots):
            beats *= 1.5
        bpm = int(m.group(1)) * beats
        if 20 <= bpm <= 400 and (best is None or y < best[0]):
            best = (y, bpm)
    return best[1] if best else None

# ---------------------------------------------------------------------------------------------
# 드럼: MuseScore 의 기본 드럼 표기 (줄 위치: 맨 윗줄 0, 아래로 반 칸씩)
# ---------------------------------------------------------------------------------------------
MUSESCORE_DRUMS = {
    ('normal', 7): 36, ('normal', 8): 36, ('normal', 3): 38,
    ('normal', 0): 50, ('normal', 1): 50, ('normal', 2): 47, ('normal', 4): 45, ('normal', 5): 41, ('normal', 6): 41,
    ('x', 3): 37, ('x', -1): 42, ('x', 9): 44, ('x', 1): 46, ('x', -2): 49, ('x', 0): 51, ('x', -3): 57, ('x', 2): 59,
    ('circle-x', -1): 46, ('circle-x', 1): 46,
    ('diamond', 0): 53, ('diamond', 2): 54, ('triangle', 1): 56,
    ('triangle', 0): 51, ('triangle', -1): 51, ('triangle', -2): 51, ('triangle', -3): 51,   # 보표 위의 세모: 라이드
}

def drum_note(position, head, fallback):
    """줄 위치와 머리 모양 -> GM 드럼 음. 기본 표기에 없는 자리는 fallback(일반 밴드스코어 표기)으로"""
    note = MUSESCORE_DRUMS.get((head, position))
    return note if note is not None else fallback()
