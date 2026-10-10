import collections
import io
import zipfile
import xml.etree.ElementTree as ET
from fractions import Fraction

try:
    import numpy as np
    from PIL import Image
except ImportError:  # 없으면 X 음표 인식만 건너뜀 (괄호 읽기는 그대로 동작)
    np = None

# Audiveris 프로젝트(.omr)의 페이지 데이터를 직접 읽어서, MusicXML로는 얻지 못하는 정보를 보충합니다.
#
# 1. 드럼 보표의 X 모양 음표 (하이햇 / 심벌)
#    Audiveris는 킥·스네어·탐의 검은 음표는 읽지만 X 모양 음표는 거의 못 읽습니다.
#    (밴드스코어는 X를 보표 맨 윗줄 바로 위에 덧줄 없이 적는데, 그 자리에서는 음표 머리를 찾지 않음)
#    그래서 프로젝트에 들어 있는 흑백 페이지 그림에서 X 모양을 직접 찾고,
#    Audiveris가 계산해 둔 마디 경계와 박 위치에 맞춰 언제 치는 음인지 정합니다.
#
# 2. 1번·2번 괄호가 시작하는 마디
#    MusicXML에서는 괄호가 어느 보표 위에 있었는지 알기 어려운데, TAB 줄이나 8va 선이 아래쪽 보표에서
#    괄호로 수없이 잘못 읽힙니다. 페이지 데이터에는 보표 번호가 있으므로 맨 위 보표의 괄호만 고릅니다.

X_NOTE_QUARTERS = Fraction(1, 4)   # 찾은 X 음표의 길이 (16분음표)
TIME_GRID = Fraction(1, 4)         # 박 위치를 못 맞춘 X는 16분음표 단위로 맞춤

def can_find_cymbals():
    return np is not None

def find_x_heads(page, top, bottom, left, right, interline):
    """
    page(검은 점이 True인 2차원 배열)의 지정한 띠 안에서 X 모양의 중심 좌표 [(x, y)]를 찾습니다.

    X의 생김새: 가운데에서 네 대각선 방향으로 팔이 뻗어 있고, 좌우는 비어 있음.
    위아래는 기둥(음표 기둥)이나 가로줄(오선/덧줄)이 지나갈 수 있어서 검사하지 않음
    """
    reach = max(4, int(round(interline * 0.42)))       # 팔 길이
    pad = reach + 2
    top, bottom = max(pad, top), min(page.shape[0] - pad, bottom)
    left, right = max(pad, left), min(page.shape[1] - pad, right)
    if bottom <= top or right <= left:
        return []

    def shifted(dy, dx):
        return page[top + dy:bottom + dy, left + dx:right + dx]

    arms = np.zeros((bottom - top, right - left), dtype=np.int16)
    for k in range(2, reach + 1):
        for sy in (-1, 1):
            for sx in (-1, 1):
                # 선 굵기와 기울기 차이를 감안해 가로로 한 칸씩 여유를 둠
                arms += shifted(sy * k, sx * k) | shifted(sy * k, sx * k - 1) | shifted(sy * k, sx * k + 1)
    sides = np.zeros_like(arms)
    for k in range(4, reach + 2):
        for sx in (-1, 1):
            for dy in (-1, 0, 1):
                sides += shifted(dy, sx * k)
    hits = shifted(0, 0) & (arms >= 4 * (reach - 1) - 2) & (sides <= 1)

    # 한 X에서 여러 점이 걸리므로 가까운 점끼리 묶어 중심을 구함
    centers = []
    for y, x in sorted(zip(*np.nonzero(hits)), key=lambda p: (p[1], p[0])):
        for group in reversed(centers[-6:]):
            if abs(group[0] / group[2] - x) <= 3 and abs(group[1] / group[2] - y) <= 3:
                group[0] += x
                group[1] += y
                group[2] += 1
                break
        else:
            centers.append([x, y, 1])
    return [(left + sx / n, top + sy / n) for sx, sy, n in centers]

def staff_position_note(y, lines, interline, drum_note_from_position):
    """X의 세로 위치 -> GM 드럼 음표. 맨 윗줄이 높은음자리표의 F5 자리, 반 칸 위가 G5(하이햇)"""
    steps_above_top = int(round((lines[0] - y) / (interline / 2)))
    position = 5 * 7 + 3 + steps_above_top           # F5의 음이름 순번 + 위로 몇 칸
    return drum_note_from_position('CDEFGAB'[position % 7], position // 7, 'x')

def stack_cymbals(stack, found, head_xs, lines, interline, tenth, usual_duration, drum_note_from_position):
    """
    한 마디 안의 X 음표 [(마디 안 위치(4분음표 단위), GM 드럼 음표, 가로 위치)]
    가로 위치는 MusicXML의 default-x와 같은 기준(마디 왼쪽 끝에서 음표 머리 왼쪽까지, 오선 간격 = 10)
    tenth: 그 단위 1이 몇 화소인지
    """
    start, end = int(stack.get('left')), int(stack.get('right'))
    tolerance = interline * 0.6
    # 박 위치 기준점 [(가로 좌표, 마디 안 시간)]: Audiveris가 모든 파트의 음표로 계산해 둔 것
    anchors = [[start + int(slot.get('x-offset')), Fraction(slot.get('time-offset')) * 4]
               for slot in stack.findall('slot')]
    if not anchors:
        return []
    length = Fraction(stack.get('duration') or 1) * 4
    inside = sorted(x for x in head_xs if start <= x < end)
    ordered = sorted(anchors)
    if length != usual_duration * 4 or any(a[1] >= b[1] for a, b in zip(ordered, ordered[1:])):
        # Audiveris가 이 마디의 리듬을 잘못 읽었음(길이가 박자와 다르거나 박 순서가 뒤죽박죽)
        # -> 기준점을 믿을 수 없으므로 첫 음표부터 마디 끝까지 고르게 나눈 것으로 어림
        length = usual_duration * 4
        anchors = [[inside[0] if inside else ordered[0][0], Fraction(0)], [end, length]]
    else:
        if len(anchors) > 1:
            # 첫 박 기준점은 마디 가운데에 그려진 온쉼표에 끌려가 있을 때가 있어 실제 첫 음표 위치로 바로잡음
            first = [x for x in inside if x < anchors[1][0] - tolerance]
            if first:
                anchors[0][0] = first[len(first) // 2]
        if end > anchors[-1][0] + tolerance and length > anchors[-1][1]:
            anchors.append([end, length])

    notes = []
    for x, y in found:
        if not start <= x < end:
            continue
        near = min(anchors, key=lambda a: abs(a[0] - x))
        if abs(near[0] - x) <= tolerance or len(anchors) == 1:
            at = near[1]
        elif x < anchors[0][0]:
            at = anchors[0][1]
        else:
            before = max((a for a in anchors if a[0] <= x), key=lambda a: a[0])
            after = min((a for a in anchors if a[0] > x), key=lambda a: a[0], default=None)
            if after is None:
                at = before[1]
            else:
                ratio = Fraction(int(x - before[0]), max(1, int(after[0] - before[0])))
                at = before[1] + (after[1] - before[1]) * ratio
                at = Fraction(round(at / TIME_GRID)) * TIME_GRID
        note = staff_position_note(y, lines, interline, drum_note_from_position)
        if at < length:
            notes.append((at, note, (x - start) / tenth - 6))   # X의 중심에서 머리 반쪽(6)만큼 왼쪽
    return sorted(notes)

def read_sheet(book, number, drum_note_from_position):
    """
    한 페이지의 마디별 정보: [{'cymbals': [(위치, 음, 가로 위치)], 'ending_open': 괄호가 여기서 시작하는지}]
    마디 순서는 Audiveris가 내보내는 MusicXML과 같음 (위 시스템부터, 왼쪽 마디부터)
    """
    root = ET.fromstring(book.read(f'sheet#{number}/sheet#{number}.xml'))
    page = None
    if np is not None:
        page = np.asarray(Image.open(io.BytesIO(book.read(f'sheet#{number}/BINARY.png'))).convert('L')) < 128
    head_xs = sorted(int(b.get('x')) + int(b.get('w')) / 2 for b in (h.find('bounds') for h in root.iter('head'))
                     if b is not None)
    endings = list(root.iter('ending'))
    durations = collections.Counter(Fraction(s.get('duration') or 1) for s in root.iter('stack') if not s.get('special'))
    usual_duration = durations.most_common(1)[0][0] if durations else Fraction(1)   # 한 마디 길이 (온음표 = 1)
    # MusicXML의 가로 위치 단위: 페이지 전체의 대표 오선 간격을 10으로 침 (보표마다 잰 간격과는 조금 다름)
    scale = root.find('.//interline')
    tenth = float(scale.get('main')) / 10 if scale is not None and scale.get('main') else None

    measures = []
    for system in root.iter('system'):
        staves = [s for s in system.iter('staff') if s.find('lines') is not None]
        # 줄 끝에 조표/박자만 미리 보여 주는 칸(CAUTIONARY)은 마디가 아님
        stacks = [s for s in system.findall('stack') if not s.get('special')]
        if not staves or not stacks:
            continue

        # 괄호: 시스템 맨 위 보표에 걸려 있고 왼쪽 세로선이 있는 것(= 여기서 열리는 것)만
        opens = set()
        for ending in endings:
            bounds = ending.find('bounds')
            if ending.get('staff') == staves[0].get('id') and ending.find('left-leg') is not None and bounds is not None:
                x = int(bounds.get('x')) + int(bounds.get('w')) // 4
                opens |= {i for i, s in enumerate(stacks) if int(s.get('left')) <= x < int(s.get('right'))}

        # X 음표: 드럼은 시스템 맨 아래 보표
        staff = staves[-1]
        lines = [float(line.find('point').get('y')) for line in staff.find('lines')]
        interline = (lines[-1] - lines[0]) / (len(lines) - 1)
        found = []
        if page is not None:
            found = find_x_heads(page, int(lines[0] - 4.5 * interline), int(lines[-1] + 2.5 * interline),
                                 int(staff.get('left')), int(staff.get('right')), interline)
        for i, stack in enumerate(stacks):
            measures.append({
                'cymbals': stack_cymbals(stack, found, head_xs, lines, interline, tenth or interline / 10,
                                         usual_duration, drum_note_from_position),
                'ending_open': i in opens,
            })
    return measures

def read_pages(book_path, sheets, drum_note_from_position):
    """프로젝트(.omr)의 지정한 페이지들의 마디별 정보를 마디 순서대로 이어 붙여 반환 (read_sheet 참고)"""
    measures = []
    with zipfile.ZipFile(book_path) as book:
        for number in sheets:
            measures += read_sheet(book, number, drum_note_from_position)
    return measures
