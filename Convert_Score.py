import collections
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from fractions import Fraction

from mido import MidiFile, MidiTrack, Message, MetaMessage, bpm2tempo

# 악보 파일 -> MIDI 변환
#   PDF 악보  --(Audiveris: 무료 악보 인식 프로그램)-->  MusicXML  -->  MIDI
# 변환된 MIDI는 Convert_Midi.py의 악기 분리 로직이 그대로 처리합니다.

PDF_EXTS = ('.pdf',)
MUSICXML_EXTS = ('.mxl', '.musicxml')
SCORE_EXTS = PDF_EXTS + MUSICXML_EXTS

TICKS_PER_BEAT = 480
NOTE_VELOCITY = 80
DRUM_CHANNEL = 9
# 드럼 채널(10번)을 제외한 채널을 파트 순서대로 배정
MELODIC_CHANNELS = [ch for ch in range(16) if ch != DRUM_CHANNEL]

AUDIVERIS_DOWNLOAD_URL = 'https://github.com/Audiveris/audiveris/releases'

# 밴드스코어 인식에 필요한 Audiveris 옵션 (기본값은 모두 꺼져 있음)
# - drumNotation: 5선 드럼 보표(타악기 음자리표) 인식
# - crossHeads: X 모양 음표 머리(하이햇/심벌) 인식
# ※ TAB 옵션(fourStringTablatures / sixStringTablatures)은 켜면 안 됨:
#   켜는 순간 Audiveris가 TAB 줄만 찾고 5선 보표를 전부 놓쳐서 음표가 하나도 안 나옴
AUDIVERIS_SWITCHES = ('drumNotation', 'crossHeads')

class AudiverisNotFoundError(RuntimeError):
    pass

def find_audiveris():
    """설치된 Audiveris 실행 파일 경로 (없으면 None). 환경 변수 AUDIVERIS_PATH로 직접 지정 가능"""
    candidates = [os.environ.get('AUDIVERIS_PATH'), shutil.which('Audiveris')]
    for var in ('ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        base = os.environ.get(var)
        if not base:
            continue
        for sub in ('', 'Programs'):
            candidates.append(os.path.join(base, sub, 'Audiveris', 'Audiveris.exe'))
            candidates.append(os.path.join(base, sub, 'Audiveris', 'bin', 'Audiveris.bat'))
    candidates.append(os.path.join(os.path.expanduser('~'), 'scoop', 'apps', 'audiveris', 'current', 'Audiveris.exe'))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None

def pdf_to_musicxml(pdf_path, work_dir, log=print):
    """
    Audiveris로 PDF 악보를 인식해서 MusicXML(.mxl)로 내보냅니다.
    악장이 나뉘거나 중간 페이지가 빠지면 파일이 여러 개 나오므로 순서대로 정렬한 목록을 반환합니다.
    """
    exe = find_audiveris()
    if exe is None:
        raise AudiverisNotFoundError(
            "PDF 악보를 읽으려면 무료 악보 인식 프로그램 Audiveris가 필요합니다.\n"
            f"   설치 파일(.msi): {AUDIVERIS_DOWNLOAD_URL}\n"
            "   설치 후 다시 시도해 주세요. (다른 위치에 설치했다면 환경 변수 AUDIVERIS_PATH에 실행 파일 경로 지정)")

    # 한글/일본어 파일명이 명령줄에서 깨지지 않도록 영문 이름으로 복사해서 넘김
    src = os.path.join(work_dir, 'score.pdf')
    shutil.copyfile(pdf_path, src)
    out_dir = os.path.join(work_dir, 'out')
    os.makedirs(out_dir, exist_ok=True)

    def run(*args):
        proc = subprocess.run(
            [exe, '-batch', *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return proc.returncode, proc.stdout.decode('utf-8', errors='replace')

    def exported():
        found = []
        for root, _, filenames in os.walk(out_dir):
            found += [os.path.join(root, fn) for fn in filenames if fn.lower().endswith(MUSICXML_EXTS + ('.xml',))]
        return found

    switches = []
    for switch in AUDIVERIS_SWITCHES:
        switches += ['-constant', f'org.audiveris.omr.sheet.ProcessingSwitches.{switch}=true']
    code, output = run('-transcribe', '-export', '-output', out_dir, *switches, '--', src)
    found = exported()

    # 한 페이지라도 실패하면(오선이 없는 표지/가사 페이지, Audiveris 내부 오류 등) Audiveris는
    # 전체를 실패로 처리하고 아무것도 내보내지 않음. 인식 결과는 프로젝트(.omr)에 남아 있으므로
    # 실패한 페이지를 빼고 다시 내보냄
    book = os.path.join(out_dir, 'score.omr')
    if not found and os.path.isfile(book):
        total = re.search(r'(\d+) sheets? in ', output)
        failed = {int(n) for n in re.findall(
            r'\[score#(\d+)\][^\n]*(?:Error processing stub|flagged as invalid)', output)}
        good = [n for n in range(1, int(total.group(1)) + 1) if n not in failed] if total else []
        # 빠진 페이지를 사이에 두면 Audiveris가 악보를 둘로 나눠 같은 파일 이름에 덮어쓰므로
        # 연속된 페이지 묶음마다 따로 내보냄
        runs = []
        for n in good:
            if runs and runs[-1][-1] == n - 1:
                runs[-1].append(n)
            else:
                runs.append([n])
        for k, sheets in enumerate(runs):
            code, retry_output = run('-export', '-sheets', *map(str, sheets), *switches, '--', book)
            output += retry_output
            # 프로젝트에서 내보낼 때는 -output 을 무시하고 항상 프로젝트 옆에 저장하므로 직접 옮김
            run_dir = os.path.join(out_dir, f'run{k:02d}')
            os.makedirs(run_dir, exist_ok=True)
            for path in exported():
                if os.path.dirname(path) == out_dir:
                    os.replace(path, os.path.join(run_dir, os.path.basename(path)))
        found = exported()
        if found and failed:
            log(f"   ⚠️ 인식하지 못해 건너뛴 페이지: {', '.join(map(str, sorted(failed)))}")

    if not found:
        if 'InvalidPasswordException' in output:
            raise RuntimeError("비밀번호가 걸린 PDF입니다. 비밀번호를 푼 PDF로 다시 시도해 주세요.")
        # 원인이 담긴 경고 줄만 추려서 보여줌 (자바 오류 추적 줄은 제외)
        warnings = [line.strip() for line in output.splitlines() if ' WARN ' in line and 'No installed OCR' not in line]
        detail = '\n'.join(dict.fromkeys(w.split('|', 1)[-1].strip() for w in warnings))[-600:]
        raise RuntimeError(f"Audiveris가 악보를 인식하지 못했습니다 (종료 코드 {code}).\n{detail}")
    # run02/score.mvt2.mxl < run02/score.mvt10.mxl < run10/... 순서가 되도록 숫자 기준 정렬
    return sorted(found, key=lambda p: [int(s) if s.isdigit() else s
                                        for s in re.split(r'(\d+)', os.path.relpath(p, out_dir))])

def read_musicxml_root(path):
    """MusicXML(.musicxml) 또는 압축 MusicXML(.mxl) 읽기"""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            name = None
            if 'META-INF/container.xml' in z.namelist():
                rootfile = ET.fromstring(z.read('META-INF/container.xml')).find('.//{*}rootfile')
                if rootfile is not None:
                    name = rootfile.get('full-path')
            if name is None:
                name = next(n for n in z.namelist()
                            if n.lower().endswith(('.xml', '.musicxml')) and not n.startswith('META-INF'))
            data = z.read(name)
    else:
        with open(path, 'rb') as f:
            data = f.read()
    root = ET.fromstring(data)
    if root.tag != 'score-partwise':
        raise ValueError(f"지원하지 않는 MusicXML 형식입니다: {root.tag}")
    return root

STEP_SEMITONES = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}

# 5선 드럼 보표의 음표 위치(높은음자리표 기준 음이름) -> GM 드럼 음표
# 음표에 악기 정보(midi-unpitched)가 없을 때만 사용
DRUM_POSITION_NORMAL = {
    'D4': 36, 'E4': 36, 'F4': 36,   # 킥
    'G4': 41, 'A4': 43,             # 플로어 탐
    'B4': 45, 'D5': 47,             # 미드 탐
    'C5': 38,                       # 스네어
    'E5': 50, 'F5': 50,             # 하이 탐
    'G5': 42, 'A5': 49,             # X 머리를 일반 머리로 잘못 읽은 하이햇 / 크래시
}
# X 모양 머리 (심벌/하이햇/림)
DRUM_POSITION_CROSS = {
    'D4': 44,                       # 페달 하이햇
    'C5': 37,                       # 림(사이드 스틱)
    'E5': 42, 'G5': 42,             # 클로즈드 하이햇
    'F5': 51,                       # 라이드
    'A5': 49, 'B5': 57, 'C6': 52,   # 크래시 / 차이나
}

def drum_note_from_position(step, octave, notehead):
    pos = f"{step}{octave}"
    if notehead == 'diamond' and pos == 'F5':
        return 53  # 라이드 벨
    # 마름모/세모 머리는 X로 치지 않음: 스캔 악보에서는 킥·스네어의 검은 머리가 마름모로 자주 잘못 읽힘
    if notehead in ('x', 'cross', 'circle-x'):
        if notehead == 'circle-x' and pos == 'G5':
            return 46  # 오픈 하이햇
        if pos in DRUM_POSITION_CROSS:
            return DRUM_POSITION_CROSS[pos]
        return 49 if (octave, STEP_SEMITONES.get(step, 0)) >= (5, 9) else 42
    return DRUM_POSITION_NORMAL.get(pos, 38)

def empty_measure(number=None):
    return {'number': number, 'notes': [], 'tempos': [], 'time': None, 'forward': False, 'backward': 0,
            'ending_start': None, 'ending_stop': False, 'extent': Fraction(0), 'nominal': None}

def align_measures(parsed):
    """
    스캔 악보는 파트마다 마디가 몇 개씩 빠져 있을 수 있음. 순서대로만 맞추면 빠진 자리부터
    그 파트가 통째로 한 마디씩 밀리므로, 마디 번호가 믿을 만하면 번호 기준으로 자리를 맞춤
    """
    ref = max(parsed, key=lambda p: len(p['measures']))
    numbers = [m['number'] for m in ref['measures']]
    index = {number: i for i, number in enumerate(numbers)}
    if None in index or len(index) != len(numbers):
        return
    for p in parsed:
        own = [m['number'] for m in p['measures']]
        if len(own) == len(numbers) or len(set(own)) != len(own) or any(n not in index for n in own):
            continue
        aligned = [empty_measure(number) for number in numbers]
        for m in p['measures']:
            aligned[index[m['number']]] = m
        p['measures'] = aligned

DIATONIC_STEPS = 'CDEFGAB'
# 같은 줄 위치라도 낮은음자리표는 높은음자리표보다 12도(음이름 12칸) 낮게 읽힘
BASS_CLEF_POSITION_SHIFT = 12

def parse_part(part, drum_instruments, clef_drums=False):
    """
    한 파트를 마디 단위로 읽습니다. 위치/길이는 4분음표 단위(Fraction).
    drum_instruments: {score-instrument id: GM 드럼 음표}
    clef_drums: 타악기 음자리표 없이 일반 보표에 적힌 드럼 파트 (음 높이가 아니라 줄 위치로 해석)
    """
    signs = {c.findtext('sign') for c in part.iter('clef')}
    # 5선보 + TAB이 같이 있는 파트는 같은 음이 두 번 들어 있으므로 TAB 쪽을 버림
    skip_tab = 'TAB' in signs and len(signs - {'TAB'}) > 0

    divisions = 1
    transpose = 0
    clefs = {}
    nominal = None
    measures = []
    staff_numbers = {'1'}
    pitched = lyric_notes = 0

    for measure in part.findall('measure'):
        pos = extent = last_start = Fraction(0)
        m = empty_measure(measure.get('number'))
        for el in measure:
            if el.tag == 'attributes':
                if el.findtext('divisions'):
                    divisions = int(el.findtext('divisions')) or 1
                for clef in el.findall('clef'):
                    clefs[clef.get('number') or '1'] = clef.findtext('sign')
                tr = el.find('transpose')
                if tr is not None:
                    transpose = int(float(tr.findtext('chromatic') or 0)) + 12 * int(tr.findtext('octave-change') or 0)
                time = el.find('time')
                if time is not None and time.findtext('beats') and time.findtext('beat-type'):
                    beats = sum(int(b) for b in re.findall(r'\d+', time.findtext('beats')))
                    beat_type = int(time.findtext('beat-type'))
                    if beats and beat_type:
                        nominal = Fraction(beats * 4, beat_type)
                        m['time'] = (beats, beat_type)
            elif el.tag in ('backup', 'forward'):
                dur = Fraction(int(float(el.findtext('duration') or 0)), divisions)
                pos = pos - dur if el.tag == 'backup' else pos + dur
                extent = max(extent, pos)
            elif el.tag in ('direction', 'sound'):
                for sound in el.iter('sound'):
                    if sound.get('tempo'):
                        m['tempos'].append((pos, float(sound.get('tempo'))))
            elif el.tag == 'barline':
                repeat = el.find('repeat')
                if repeat is not None:
                    if repeat.get('direction') == 'forward':
                        m['forward'] = True
                    else:
                        m['backward'] = int(repeat.get('times') or 2)
                ending = el.find('ending')
                if ending is not None:
                    if ending.get('type') == 'start':
                        m['ending_start'] = {int(n) for n in re.findall(r'\d+', ending.get('number') or '')}
                    else:
                        m['ending_stop'] = True
            elif el.tag == 'note':
                if el.find('grace') is not None or el.find('cue') is not None:
                    continue
                dur = Fraction(int(float(el.findtext('duration') or 0)), divisions)
                if el.find('chord') is not None:
                    start = last_start
                else:
                    start = last_start = pos
                    pos += dur
                    extent = max(extent, pos)
                staff = el.findtext('staff') or '1'
                staff_numbers.add(staff)
                sign = clefs.get(staff, clefs.get('1'))
                if el.find('rest') is not None or (skip_tab and sign == 'TAB'):
                    continue
                pitch = el.find('pitch')
                unpitched = el.find('unpitched')
                if pitch is not None and sign != 'percussion' and not clef_drums:
                    note = (12 * (int(pitch.findtext('octave')) + 1) + STEP_SEMITONES[pitch.findtext('step')]
                            + int(round(float(pitch.findtext('alter') or 0))) + transpose)
                    is_drum = False
                    pitched += 1
                    lyric_notes += el.find('lyric') is not None
                else:
                    inst = el.find('instrument')
                    note = drum_instruments.get(inst.get('id')) if inst is not None else None
                    if note is None:
                        src = pitch if pitch is not None else unpitched
                        prefix = '' if pitch is not None else 'display-'
                        step = (src.findtext(prefix + 'step') if src is not None else None) or 'C'
                        octave = int((src.findtext(prefix + 'octave') if src is not None else None) or 5)
                        if clef_drums and sign == 'F':
                            position = octave * 7 + DIATONIC_STEPS.index(step) + BASS_CLEF_POSITION_SHIFT
                            step, octave = DIATONIC_STEPS[position % 7], position // 7
                        note = drum_note_from_position(step, octave, el.findtext('notehead') or 'normal')
                    is_drum = True
                ties = {t.get('type') for t in el.findall('tie')}
                m['notes'].append((start, dur, max(0, min(127, note)), is_drum, 'start' in ties, 'stop' in ties))
        m['extent'] = extent
        m['nominal'] = nominal
        measures.append(m)

    return {
        'measures': measures,
        'pitched': pitched,
        'lyric_notes': lyric_notes,
        'single_bass_clef': len(staff_numbers) == 1 and signs == {'F'},
    }

def playback_order(marks):
    """
    도돌이표 / 1번·2번 괄호를 풀어서 실제 연주되는 마디 순서를 만듭니다.
    (D.S. / D.C. / Coda 점프는 처리하지 않고 적힌 순서대로 연주)
    """
    # 괄호 구간 [시작 마디, 끝 마디, 번호] (닫는 표시를 못 읽은 괄호는 한 마디로 간주)
    spans = []
    for i, m in enumerate(marks):
        if m['ending_start']:
            spans.append([i, i, m['ending_start']])
        if m['ending_stop'] and spans:
            spans[-1][1] = max(spans[-1][1], i)

    # 괄호가 걸친 마디에 괄호 번호를 표시.
    # 악보 인식은 "Howling ┌──┐" 같은 주법 표시도 괄호로 읽으므로, 1번 괄호가 도돌이표로 끝나고
    # 그 바로 뒤에 이어지는 괄호만 진짜로 인정 (가짜 괄호를 믿으면 마디를 통째로 건너뜀)
    endings = [None] * len(marks)
    valid_stop = None
    for first, last, numbers in spans:
        if 1 in numbers:
            valid = bool(marks[last]['backward'])
        else:
            valid = valid_stop is not None and first == valid_stop + 1
        valid_stop = last if valid else None
        if valid:
            for i in range(first, last + 1):
                endings[i] = numbers

    order = []
    i = start = 0
    pass_no = 1
    while i < len(marks) and len(order) < len(marks) * 20:
        m = marks[i]
        if endings[i] is None and i > 0 and endings[i - 1] is not None and pass_no > 1:
            start, pass_no = i, 1   # 마지막 괄호를 빠져나옴 -> 새 구간 시작
        if m['forward'] and start != i:
            start, pass_no = i, 1
        if endings[i] is not None and pass_no not in endings[i]:
            i += 1
            continue
        order.append(i)
        if m['backward']:
            times = max(m['backward'], max(endings[i]) + 1 if endings[i] else 0)
            if pass_no < times:
                pass_no += 1
                i = start
                continue
            if endings[i] is None:
                start, pass_no = i + 1, 1
        i += 1
    return order

# 파트 이름 약어 (점/숫자/공백을 뺀 이름 전체가 일치할 때만) -> 악기 분리 로직이 알아보는 이름
PART_NAME_ALIASES = {
    'Guitar': ('g', 'gt', 'gtr', 'eg', 'ag', 'egt', 'agt'),
    'Bass': ('b', 'ba', 'bs', 'eb'),
    'Drums': ('d', 'dr', 'drs', 'ds', 'drum', 'drums'),
}
# Audiveris가 파트 이름을 못 읽었을 때 붙이는 기본 이름 (실제 이름이 아님)
AUDIVERIS_DEFAULT_NAMES = ('voice', 'piano', 'noname')

def real_part_name(name, from_audiveris):
    """Audiveris가 붙인 기본 이름은 이름이 없는 것으로 취급"""
    return '' if from_audiveris and name.lower() in AUDIVERIS_DEFAULT_NAMES else name

def part_alias(name):
    compact = re.sub(r'[^a-z]', '', name.lower())
    return next((canonical for canonical, aliases in PART_NAME_ALIASES.items() if compact in aliases), None)

def guess_scanned_roles(parts):
    """
    파트 이름을 하나도 못 읽은 스캔 밴드스코어의 파트 역할을 보표 모양으로 추정합니다.
    반환: {파트 순번: 'drums' | 'tab' | 'guitar' | 'bass'}

    일본식 밴드스코어 배치 (위에서 아래로): 보컬 / 건반 / 기타 5선보 + TAB / 베이스 5선보 + TAB / 드럼
    - 드럼은 타악기 음자리표 대신 낮은음자리표 보표에 적혀 있고 조표가 없음
    - 6줄 TAB은 조표 없는 보표로 잘못 읽히고, 음표가 없거나 엉뚱한 음표 몇 개만 나옴
    """
    shapes = []
    for part in parts:
        notes = [n for n in part.iter('note') if n.find('rest') is None]
        signs = collections.Counter(c.findtext('sign') for c in part.iter('clef'))
        shapes.append({
            'single': len({n.findtext('staff') or '1' for n in notes}) <= 1,
            'clef': signs.most_common(1)[0][0] if signs else None,
            'keys': len(part.findall('.//key')),
            'unpitched': sum(1 for n in notes if n.find('unpitched') is not None),
        })
    max_keys = max(sh['keys'] for sh in shapes)
    # 진짜 5선보는 줄마다 조표가 있음. 다른 파트에 비해 조표가 거의 없는 한 단짜리 보표 = 드럼 또는 TAB
    keyless = [sh['single'] and max_keys >= 2 and sh['keys'] * 3 <= max_keys for sh in shapes]

    roles = {}
    has_drum_staff = any(sh['clef'] == 'percussion' or sh['unpitched'] >= 10 for sh in shapes)
    # 베이스 = 조표가 있는 첫 번째 낮은음자리표 한 단 파트. 그 아래의 한 단 파트는 전부 드럼
    # (페이지마다 보표 수가 달라서 드럼이 두 파트로 갈라져 인식되기도 함)
    bass = next((i for i, sh in enumerate(shapes) if sh['single'] and sh['clef'] == 'F' and not keyless[i]), None)
    last = len(parts) - 1
    if bass is not None:
        roles[bass] = 'bass'
        if not has_drum_staff:
            for idx in range(bass + 1, len(parts)):
                if shapes[idx]['single'] and shapes[idx]['clef'] == 'F':
                    roles[idx] = 'drums'
    elif not has_drum_staff and last > 0 and keyless[last] and shapes[last]['clef'] == 'F':
        roles[last] = 'drums'   # 베이스 보표 없이 드럼만 있는 악보
    # 베이스 위쪽의 조표 없는 한 단 보표 = 6줄 TAB을 잘못 읽은 것. 그 바로 위가 기타
    for idx in range(1, bass if bass is not None else len(parts)):
        if keyless[idx] and idx not in roles and shapes[idx]['clef'] != 'percussion':
            roles[idx] = 'tab'
            if shapes[idx - 1]['single'] and shapes[idx - 1]['clef'] == 'G' and idx - 1 not in roles:
                roles[idx - 1] = 'guitar'
    return roles

def name_part(name, program, part, role=None):
    """
    트랙 이름/악기 번호 결정. 스캔 악보는 파트 이름과 악기 정보가 없는 경우가 많아서
    이름이 없으면 가사(보컬), 낮은음자리표(베이스), 보표 모양으로 추정한 역할을 씁니다.
    반환: (트랙 이름, GM 프로그램 번호 또는 None)
    """
    alias = part_alias(name)
    if alias:
        return f"{alias} ({name})", program
    if name:
        return name, program
    if role == 'guitar':
        return 'Guitar', None
    if role == 'bass':
        return 'Bass', None
    if not part['pitched']:
        return 'Drums', None
    if part['lyric_notes'] * 2 >= part['pitched']:
        return 'Vocal', None
    if part['single_bass_clef']:
        return 'Bass', None
    return '', None

def load_musicxml(path):
    """
    MusicXML 한 파일 -> 파트별 음표 목록 (시간은 4분음표 단위)
    반환: {'parts': [{'name', 'program', 'notes': [[시작, 끝, 음, 드럼여부]]}], 'tempos', 'times', 'length'}
    """
    root = read_musicxml_root(path)
    from_audiveris = 'audiveris' in ' '.join(s.text or '' for s in root.iter('software')).lower()

    part_infos = {}
    for sp in root.iterfind('part-list/score-part'):
        drums = {}
        program = None
        for mi in sp.findall('midi-instrument'):
            if mi.findtext('midi-unpitched'):
                # MusicXML 규격은 (MIDI 음표 + 1)인데 Audiveris는 MIDI 음표를 그대로 기록
                drums[mi.get('id')] = int(mi.findtext('midi-unpitched')) - (0 if from_audiveris else 1)
            elif program is None and mi.findtext('midi-program'):
                program = int(mi.findtext('midi-program')) - 1
        part_infos[sp.get('id')] = ((sp.findtext('part-name') or '').strip(), program, drums)

    parts = root.findall('part')
    if not parts:
        raise ValueError("악보에 파트가 없습니다.")
    infos = [part_infos.get(part.get('id'), ('', None, {})) for part in parts]
    names = [real_part_name(name, from_audiveris) for name, _, _ in infos]
    scanned = from_audiveris and not any(names)   # 파트 이름을 하나도 못 읽은 스캔 악보
    roles = guess_scanned_roles(parts) if scanned else {}
    has_drum_staff = root.find('.//unpitched') is not None
    parsed = []
    for idx, (part, (_, program, drums)) in enumerate(zip(parts, infos)):
        # 타악기 음자리표 없이 일반 보표에 적힌 드럼 (이름이 Dr. / Drs. 이거나 모양으로 추정)
        clef_drums = roles.get(idx) == 'drums' or (part_alias(names[idx]) == 'Drums' and not has_drum_staff)
        p = parse_part(part, drums, clef_drums=clef_drums)
        if roles.get(idx) == 'tab':
            for m in p['measures']:
                m['notes'] = []   # TAB 줄을 5선보로 잘못 읽어서 나온 엉뚱한 음표
        # 이름을 못 읽은 파트의 악기 번호는 Audiveris 기본값이라 의미 없음
        program = program if names[idx] or not from_audiveris else None
        p['name'], p['program'] = name_part(names[idx], program, p, roles.get(idx))
        parsed.append(p)

    align_measures(parsed)
    n_measures = max(len(p['measures']) for p in parsed)
    # 스캔 악보에서 박자표를 못 읽었으면 가장 흔한 마디 길이를 박자로 간주
    guessed = None
    if from_audiveris and not any(m['nominal'] for p in parsed for m in p['measures']):
        extents = collections.Counter(m['extent'] for p in parsed for m in p['measures'] if m['extent'])
        guessed = extents.most_common(1)[0][0] if extents else None
    lengths = []
    marks = []
    for i in range(n_measures):
        ms = [p['measures'][i] for p in parsed if i < len(p['measures'])]
        extent = max(m['extent'] for m in ms)
        nominal = next((m['nominal'] for m in ms if m['nominal']), None) or guessed
        if from_audiveris and nominal and (i > 0 or extent >= nominal):
            # 스캔 악보는 리듬을 잘못 읽은 마디가 많아서, 못갖춘마디일 수 있는 첫 마디만 빼고 박자 길이로 고정
            lengths.append(nominal)
        else:
            # 마디 길이는 내용이 가장 긴 파트 기준 (못갖춘마디 대응).
            # 박자보다 길면 리듬을 잘못 읽은 것이므로 박자 길이로 맞춰서 다른 파트가 밀리지 않게 함
            lengths.append(min(extent, nominal) if nominal and extent else (nominal or extent))
        marks.append({
            'forward': any(m['forward'] for m in ms),
            'backward': max(m['backward'] for m in ms),
            'ending_start': next((m['ending_start'] for m in ms if m['ending_start']), None),
            'ending_stop': any(m['ending_stop'] for m in ms),
        })

    if from_audiveris:
        # 악보 인식은 끝세로줄 등을 도돌이표로 잘못 읽는 일이 있고, 가짜 도돌이표는 곡을 통째로 두 번 연주하게 만듦.
        # 시작 도돌이표와 짝이 맞는 것만 인정
        opened = False
        for mark in marks:
            opened = opened or mark['forward']
            if mark['backward'] and not opened:
                mark['backward'] = 0
            if mark['backward']:
                opened = False

    order = playback_order(marks)
    bases = []
    offset = Fraction(0)
    for i in order:
        bases.append(offset)
        offset += lengths[i]

    score = {'parts': [], 'tempos': [], 'times': [], 'length': offset}
    for p in parsed:
        notes = []
        open_ties = {}
        for i, base in zip(order, bases):
            if i >= len(p['measures']):
                continue
            m = p['measures'][i]
            for at, bpm in m['tempos']:
                if bpm > 0 and (base + at, bpm) not in score['tempos']:
                    score['tempos'].append((base + at, bpm))
            # 박자는 모든 파트에 똑같이 적혀 있으므로 첫 파트 것만 사용
            if p is parsed[0] and m['time'] and (not score['times'] or score['times'][-1][1] != m['time']):
                score['times'].append((base, m['time']))
            for start, dur, note, is_drum, tie_start, tie_stop in m['notes']:
                key = (is_drum, note)
                begin = base + start
                tied = open_ties.pop(key, None) if tie_stop else None
                if tied is not None and begin - tied[1] <= Fraction(1, 4):
                    tied[1] = max(tied[1], begin + dur)  # 붙임줄: 앞 음을 늘림
                    rec = tied
                else:
                    rec = [begin, begin + dur, note, is_drum]
                    notes.append(rec)
                if tie_start:
                    open_ties[key] = rec
        score['parts'].append({'name': p['name'], 'program': p['program'], 'notes': notes})
    return score

DEFAULT_BPM = 120

def scores_to_midi(scores, fallback_bpm=None, log=print):
    """
    load_musicxml 결과(악장이 여러 개면 순서대로 이어 붙임) -> MidiFile
    fallback_bpm: 악보에서 템포를 읽지 못했을 때 쓸 템포
    """
    def tick(quarters):
        return int(round(quarters * TICKS_PER_BEAT))

    conductor = []
    parts = []
    slots = {}
    offset = Fraction(0)
    for score in scores:
        conductor += [(tick(offset + t), MetaMessage('set_tempo', tempo=bpm2tempo(bpm))) for t, bpm in score['tempos']]
        conductor += [(tick(offset + t), MetaMessage('time_signature', numerator=num, denominator=den))
                      for t, (num, den) in score['times'] if den in (1, 2, 4, 8, 16, 32) and 0 < num < 256]
        # 파일마다 파트 수가 다를 수 있으므로 (이름, 같은 이름 중 몇 번째)로 짝을 맞춤
        seen = collections.Counter()
        for part in score['parts']:
            key = (part['name'], seen[part['name']])
            seen[part['name']] += 1
            if key not in slots:
                slots[key] = len(parts)
                parts.append({'name': part['name'], 'program': part['program'], 'notes': []})
            parts[slots[key]]['notes'] += [(offset + s, offset + e, note, drum) for s, e, note, drum in part['notes']]
        offset += score['length']

    if not any(msg.type == 'set_tempo' for _, msg in conductor):
        bpm = fallback_bpm or DEFAULT_BPM
        log(f"   ⚠️ 악보에서 템포를 읽지 못해 {bpm:g} BPM으로 저장합니다."
            + ("" if fallback_bpm else " (템포 칸에 BPM을 적으면 그 값으로 저장)"))
        conductor.append((0, MetaMessage('set_tempo', tempo=bpm2tempo(bpm))))

    def to_track(events):
        track = MidiTrack()
        last = 0
        for at, _, msg in sorted(events, key=lambda e: (e[0], e[1])):
            track.append(msg.copy(time=at - last))
            last = at
        return track

    mid = MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    mid.tracks.append(to_track([(at, 0, msg) for at, msg in conductor]))
    for idx, part in enumerate(parts):
        if not part['notes']:
            continue
        ch = MELODIC_CHANNELS[idx % len(MELODIC_CHANNELS)]
        events = []
        if part['name']:
            events.append((0, 0, MetaMessage('track_name', name=part['name'])))
        if part['program'] is not None and 0 <= part['program'] <= 127:
            events.append((0, 0, Message('program_change', channel=ch, program=part['program'])))
        for start, end, note, is_drum in part['notes']:
            on = tick(start)
            off = max(tick(end), on + 1)
            note_ch = DRUM_CHANNEL if is_drum else ch
            # 같은 틱에서는 note_off(1)가 note_on(2)보다 먼저 오도록 정렬
            events.append((on, 2, Message('note_on', channel=note_ch, note=note, velocity=NOTE_VELOCITY)))
            events.append((off, 1, Message('note_off', channel=note_ch, note=note, velocity=0)))
        mid.tracks.append(to_track(events))
    if len(mid.tracks) < 2:
        raise ValueError("악보에서 음표를 찾지 못했습니다.")
    return mid

def score_to_midi(path, log=print, fallback_bpm=None):
    """PDF / MusicXML 악보 -> MidiFile"""
    if path.lower().endswith(MUSICXML_EXTS):
        return scores_to_midi([load_musicxml(path)], fallback_bpm, log)
    with tempfile.TemporaryDirectory(prefix='midivision_') as work_dir:
        log("   PDF 악보 인식 중입니다 (Audiveris). 한 페이지에 20초쯤 걸립니다...")
        xml_paths = pdf_to_musicxml(path, work_dir, log)
        return scores_to_midi([load_musicxml(p) for p in xml_paths], fallback_bpm, log)
