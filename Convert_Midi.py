import mido
from mido import MidiFile, MidiTrack, Message, MetaMessage
import sys
import os
import re

# GM 드럼 노트 번호 -> 스타 레조넌스 건반 노트 번호 정밀 매핑 테이블
# 인게임 건반:
# F  = F4 (65) : 베이스 드럼 (Kick)
# Q  = C5 (72) : 스네어 드럼 (Snare)
# S  = D4 (62) : 클로즈드/페달 하이햇 (Closed/Pedal Hi-Hat)
# T  = G5 (79) : 오픈 하이햇 (Open Hi-Hat)
# W  = D5 (74) : 미드 탐 (Mid Tom)
# E  = E5 (76) : 하이 탐 (High Tom)
# H  = A4 (69) : 플로어 탐 (Floor Tom) ★ (A4#인 70번=0번키가 아닌 A4=69번=H키!)
# R  = F5 (77) : 크래시 심벌 1 (Crash Cymbal 1 / China / Splash)
# Y  = A5 (81) : 라이드 심벌 / 크래시 2 (Ride Cymbal / Crash 2)

DRUM_MAP = {
    # 베이스 드럼 계열 -> F4 (인게임 F키 = 65)
    35: 65,  # Acoustic Bass Drum
    36: 65,  # Bass Drum 1
    
    # 스네어 계열 -> C5 (인게임 Q키 = 72)
    37: 72,  # Side Stick
    38: 72,  # Acoustic Snare
    39: 72,  # Hand Clap
    40: 72,  # Electric Snare
    
    # 하이햇 계열
    42: 62,  # Closed Hi-Hat -> D4 (인게임 S키 = 62)
    44: 62,  # Pedal Hi-Hat  -> D4 (인게임 S키 = 62)
    46: 79,  # Open Hi-Hat   -> G5 (인게임 T키 = 79)
    
    # 탐(Tom) 계열
    41: 69,  # Low Floor Tom  -> A4 (인게임 H키 = 69) ★ A4#인 70(0번키)이 아닌 69(H키)!
    43: 69,  # High Floor Tom -> A4 (인게임 H키 = 69)
    45: 74,  # Low Tom        -> D5 (인게임 W키 = 74)
    47: 74,  # Low-Mid Tom    -> D5 (인게임 W키 = 74)
    48: 74,  # Hi-Mid Tom     -> D5 (인게임 W키 = 74)
    50: 76,  # High Tom       -> E5 (인게임 E키 = 76)
    58: 76,  # Vibra-Slap     -> E5 (인게임 E키 = 76)
    
    # 심벌(Cymbal) 계열 -> F5 (인게임 R키 = 77) / A5 (인게임 Y키 = 81)
    49: 77,  # Crash Cymbal 1 -> F5 (인게임 R키 = 77)
    52: 77,  # Chinese Cymbal -> F5 (인게임 R키 = 77)
    55: 77,  # Splash Cymbal  -> F5 (인게임 R키 = 77)
    57: 77,  # Crash Cymbal 2 -> F5 (인게임 R키 = 77)
    51: 81,  # Ride Cymbal 1  -> A5 (인게임 Y키 = 81)
    53: 81,  # Ride Bell      -> A5 (인게임 Y키 = 81)
    59: 81,  # Ride Cymbal 2  -> A5 (인게임 Y키 = 81)

    # 기타 확장 타악기
    27: 72, 28: 72, 29: 72, 30: 72, 31: 72,  # Laser/Slap/Scratch/Sticks -> Q키 (스네어/스틱)
    32: 62, 33: 62, 34: 62,                  # Clicks/Bells -> S키 (하이햇)
    54: 81,  # Tambourine     -> Y키 (81)
    56: 77,  # Cowbell        -> R키 (77)
    60: 81, 61: 81, 62: 74, 63: 76, 64: 76, 65: 74, 66: 69,
    67: 62, 68: 72, 69: 69, 
    70: 62,  # 70번 Maracas / Shaker -> D4 (인게임 S키 = 62, Closed Hi-Hat) ★ 플로어탐(H)이 아닌 하이햇으로 매핑!
    71: 77, 72: 72, 73: 72, 74: 79, 75: 81, 76: 81, 77: 72, 78: 72,
    79: 72, 80: 72, 81: 72, 82: 77, 83: 77,
    84: 77, 85: 72, 86: 69, 87: 69,  # GS 확장: Belltree / Castanets / Surdo
}

# 드럼 음표로 인정하는 범위 (GM/GS 타악기 27~87). 범위 밖 음표는 드럼 소리가 아니므로 버림
DRUM_NOTE_RANGE = range(27, 88)

# 쉐이커 계열 보조 타악기는 드럼 세트가 아니므로 버림
# (예: Melt는 마라카스(70)가 1,891번 나와서 H/S키를 계속 연타하게 됨)
SKIP_DRUM_NOTES = {
    69,  # Cabasa
    70,  # Maracas
    82,  # Shaker
}

def get_mapped_drum_note(note):
    """
    GM 드럼 음표 -> 인게임 키. 타악기 범위(27~87) 밖의 음표는 None을 반환하여 버립니다.
    (예전에는 범위 밖 음표를 전부 크래시(R)로 바꿔서, 드럼이 아닌 음표가 섞이면
     크래시가 계속 울리는 문제가 있었음)
    """
    if note not in DRUM_NOTE_RANGE or note in SKIP_DRUM_NOTES:
        return None
    return DRUM_MAP[note]

# 악기별 판별 설정 (기타, 베이스, 건반, 드럼)
INSTRUMENT_CONFIG = {
    'drum': {
        'name': '드럼',
        'suffix': ' (Drum)',
        'remap_drums': True,  # 드럼은 스타 레조넌스 음계 이동(매핑) 유지!
        'keywords': ['drum', 'drums', 'perc', 'percussion', '드럼', '타악기', 'battery', 'kit', 'cymb', 'ドラム', 'パーカッション', '打楽器'],
        'channel_match': lambda ch: ch == 9,
        'program_match': lambda p: False,
    },
    'guitar': {
        'name': '기타',
        'suffix': ' (Guitar)',
        'remap_drums': False,  # 멜로디 악기는 원음 유지
        'keywords': ['guitar', 'gt', 'guit', 'eg', 'ag', 'clean', 'dist', 'overdrive', 'lead gt', 'ac gt', 'el gt', '기타', 'ギター'],
        'channel_match': lambda ch: ch != 9,
        'program_match': lambda p: 24 <= p <= 31,
    },
    'bass': {
        'name': '베이스',
        'suffix': ' (Bass)',
        'remap_drums': False,  # 멜로디 악기는 원음 유지
        'keywords': ['bass', 'eb', 'slap', 'pick bass', 'fingered bass', 'ac bass', 'el bass', '베이스', 'ベース'],
        'channel_match': lambda ch: ch != 9,
        'program_match': lambda p: 32 <= p <= 39,
    },
    'keyboard': {
        'name': '건반',
        'suffix': ' (Keyboard)',
        'remap_drums': False,  # 멜로디 악기는 원음 유지
        'keywords': ['piano', 'key', 'keyboard', 'synth', 'organ', 'clav', 'rhodes', 'harpsichord', '피아노', '건반', '신스', 'ピアノ', 'オルガン', 'シンセ', 'キーボード'],
        'channel_match': lambda ch: ch != 9,
        'program_match': lambda p: (0 <= p <= 23) or (80 <= p <= 103),
    }
}

def match_keyword(name, keywords):
    """
    키워드 매칭 함수:
    - 영문 키워드는 단어 경계(?<![a-z0-9])를 사용하여 supercell 내 'perc', legend 내 'eg' 등의 오판별을 방지합니다.
    - 한글 등 비영문 키워드는 일반 부분 일치 검사를 수행합니다.
    """
    lower = name.lower()
    for kw in keywords:
        kw_lower = kw.lower()
        if not re.match(r'^[a-z0-9_ -]+$', kw_lower):
            if kw_lower in lower:
                return True
        else:
            pattern = r'(?<![a-z0-9])' + re.escape(kw_lower) + r'(?![a-z0-9])'
            if re.search(pattern, lower):
                return True
    return False

# 모든 트랙에 공통으로 적용되어야 하는 메타 메시지 (템포/박자/조성)
CONDUCTOR_META_TYPES = ('set_tempo', 'time_signature', 'key_signature')

def is_note_on(msg):
    return msg.type == 'note_on' and msg.velocity > 0

def is_note_off(msg):
    return msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0)

def classify_program(program):
    """GM 프로그램 번호 -> 악기 키"""
    if 32 <= program <= 39:
        return 'bass'
    if 24 <= program <= 31:
        return 'guitar'
    return 'keyboard'

def decode_text(text):
    """
    mido는 트랙 이름을 latin-1로 읽기 때문에 일본어(Shift-JIS)/한국어(CP949) 이름이 깨집니다.
    원래 바이트로 되돌린 뒤 UTF-8 -> Shift-JIS -> CP949 순서로 다시 디코딩합니다.
    """
    try:
        raw = text.encode('latin-1')
    except UnicodeEncodeError:
        return text
    for enc in ('utf-8', 'cp932', 'cp949'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return text

def file_has_drum_channel(mid):
    """파일 어딘가에 채널 10(인덱스 9) 음표가 있는지"""
    return any(msg.type == 'note_on' and msg.channel == 9 for track in mid.tracks for msg in track)

def analyze_track_instrument(track, drum_by_name=True):
    """
    트랙의 이름, 프로그램 번호, 채널, 음표 정보를 기반으로 악기 판별:
    - 드럼: 채널 9 또는 드럼 키워드 (drum_by_name=False이면 채널 9만 인정)
    - 베이스: 베이스 프로그램(32~39) 또는 베이스 키워드
    - 기타: 기타 프로그램(24~31) 또는 기타 키워드
    - 건반: 위 3개를 제외한 모든 악기(피아노, 색소폰, 브라스, 스트링, 플루트, 보컬 멜로디 등)를 건반으로 배정!
    """
    name = ""
    channels = set()
    programs = set()
    total_notes = 0
    has_tempo = False

    for msg in track:
        if msg.type == 'track_name':
            name = decode_text(msg.name)
        elif msg.type in CONDUCTOR_META_TYPES:
            has_tempo = True
        elif msg.type == 'program_change':
            programs.add(msg.program)
        elif msg.type in ('note_on', 'note_off'):
            if is_note_on(msg):
                total_notes += 1
            channels.add(msg.channel)

    info = {
        'name': name,
        'total_notes': total_notes,
        'has_tempo': has_tempo,
        'channels': channels,
        'programs': programs,
    }

    # 음표가 없는 메타/템포 트랙
    if total_notes == 0:
        info['instrument'] = 'tempo' if has_tempo else 'empty'
    # 1. 드럼 판별 (Channel 9 또는 드럼 키워드)
    elif (9 in channels) or (drum_by_name and match_keyword(name, INSTRUMENT_CONFIG['drum']['keywords'])):
        info['instrument'] = 'drum'
    # 2. 베이스 판별 (베이스 키워드 또는 베이스 프로그램 32~39)
    elif match_keyword(name, INSTRUMENT_CONFIG['bass']['keywords']) or any(32 <= p <= 39 for p in programs):
        info['instrument'] = 'bass'
    # 3. 기타 판별 (기타 키워드 또는 기타 프로그램 24~31)
    elif match_keyword(name, INSTRUMENT_CONFIG['guitar']['keywords']) or any(24 <= p <= 31 for p in programs):
        info['instrument'] = 'guitar'
    # 4. 그 외 모든 악기 -> 건반(Keyboard)으로 배정!
    # (색소폰, 트럼펫, 브라스, 바이올린, 스트링, 플루트, 오보에, 보컬 멜로디, 피아노, 오르간, 신스 등)
    else:
        info['instrument'] = 'keyboard'
    return info

def build_conductor_track(mid):
    """
    모든 트랙에서 템포/박자/조성 메타 메시지를 모아 하나의 컨덕터 트랙을 만듭니다.
    (템포가 음표가 있는 트랙에 들어 있어도 추출 파일의 재생 속도가 틀어지지 않도록)
    """
    events = []
    seen = set()
    for track in mid.tracks:
        abs_time = 0
        for msg in track:
            abs_time += msg.time
            if msg.type in CONDUCTOR_META_TYPES:
                key = (abs_time, str(msg.copy(time=0)))
                if key not in seen:
                    seen.add(key)
                    events.append((abs_time, msg))
    events.sort(key=lambda e: e[0])

    conductor = MidiTrack()
    last = 0
    for abs_time, msg in events:
        conductor.append(msg.copy(time=abs_time - last))
        last = abs_time
    return conductor

class DrumNoteMapper:
    """
    여러 GM 드럼 음표가 같은 인게임 키로 합쳐질 때(예: 42/44 -> S키) 발생하는
    note_on/note_off 꼬임(소리가 중간에 끊기거나 겹쳐서 씹히는 현상)을 방지합니다.
    - 같은 틱에 같은 키가 중복으로 눌리면 1번만 누름
    - 이미 눌린 키가 다시 눌리면 먼저 떼고 다시 누름(재타격)
    - note_off는 해당 키를 마지막으로 누른 원본 음표에서만 발생
    """
    def __init__(self):
        self.owner = {}        # 인게임 키 -> 현재 누르고 있는 원본 음표
        self.last_on_tick = {} # 인게임 키 -> 마지막으로 누른 틱

    def process(self, msg, abs_tick):
        """변환된 메시지 목록을 반환 (없으면 빈 목록)"""
        key = get_mapped_drum_note(msg.note)
        if key is None:
            return []  # 타악기 범위 밖 음표는 버림
        if is_note_on(msg):
            if self.last_on_tick.get(key) == abs_tick:
                return []  # 같은 순간 중복 타격 제거
            out = []
            if key in self.owner:
                out.append(Message('note_off', channel=msg.channel, note=key, velocity=0))
            self.owner[key] = msg.note
            self.last_on_tick[key] = abs_tick
            out.append(msg.copy(note=key, time=0))
            return out
        # note_off
        if self.owner.get(key) != msg.note:
            return []
        del self.owner[key]
        return [msg.copy(note=key, time=0)]

def to_absolute(track):
    """MidiTrack -> [(절대틱, 메시지)]"""
    abs_tick = 0
    events = []
    for msg in track:
        abs_tick += msg.time
        events.append((abs_tick, msg))
    return events

def to_track(events):
    """[(절대틱, 메시지)] -> MidiTrack (시간순 정렬, 같은 틱이면 기존 순서 유지)"""
    track = MidiTrack()
    last = 0
    for abs_tick, msg in sorted(events, key=lambda e: e[0]):
        track.append(msg.copy(time=abs_tick - last))
        last = abs_tick
    return track

def song_timeline(mid):
    """원곡 전체의 첫 음 시작 / 마지막 음 시작 / 마지막 음 끝 / 파일 끝 틱"""
    first_on = last_on = last_off = None
    song_end = 0
    for track in mid.tracks:
        for abs_tick, msg in to_absolute(track):
            song_end = max(song_end, abs_tick)
            if msg.type not in ('note_on', 'note_off'):
                continue
            if is_note_on(msg):
                first_on = abs_tick if first_on is None else min(first_on, abs_tick)
                last_on = abs_tick if last_on is None else max(last_on, abs_tick)
            else:
                last_off = abs_tick if last_off is None else max(last_off, abs_tick)
    if first_on is None:
        return None
    if last_off is None or last_off < last_on:
        last_off = last_on
    return {'first_on': first_on, 'last_on': last_on, 'last_off': last_off, 'end': song_end}

# 시작/끝 맞춤용 음표 세기 (소리는 거의 안 나지만 게임이 음표로 인식하도록 1)
ANCHOR_VELOCITY = 1

def align_to_song_timeline(new_mid, part_indices, timeline):
    """
    파트별로 분리하면 그 악기의 첫 음/마지막 음 기준으로 곡이 잘려서
    파트마다 시작·종료 시간이 달라집니다. (예: Melt 드럼 253.7초, 원곡 256.7초)
    원곡의 첫 음/마지막 음 위치에 아주 약한 음표를 넣고 파일 끝도 원곡과 맞춥니다.
    """
    if timeline is None or not part_indices:
        return
    all_events = [(i, to_absolute(new_mid.tracks[i])) for i in part_indices]
    note_ons = [(t, m) for _, evs in all_events for t, m in evs if is_note_on(m)]
    note_offs = [(t, m) for _, evs in all_events for t, m in evs if is_note_off(m)]
    if not note_ons:
        return
    part_first_tick, part_first_msg = min(note_ons, key=lambda e: e[0])
    part_last_tick, part_last_msg = max(note_ons, key=lambda e: e[0])
    part_last_off = max([t for t, _ in note_offs] + [part_last_tick])

    idx, events = all_events[0]
    ch = part_first_msg.channel

    # 시작 맞춤: 원곡 첫 음 위치에 이 파트의 첫 음과 같은 음 (첫 음 전에 끝나도록 짧게)
    if part_first_tick > timeline['first_on']:
        start = timeline['first_on']
        off = min(start + max(1, new_mid.ticks_per_beat // 16), part_first_tick)
        events.append((start, Message('note_on', channel=ch, note=part_first_msg.note, velocity=ANCHOR_VELOCITY)))
        events.append((off, Message('note_off', channel=ch, note=part_first_msg.note, velocity=0)))

    # 끝 맞춤: 원곡 마지막 음 위치에 이 파트의 마지막 음과 같은 음
    if part_last_off < timeline['last_off']:
        on = max(timeline['last_on'], part_last_off)
        off = max(timeline['last_off'], on + 1)
        events.append((on, Message('note_on', channel=part_last_msg.channel, note=part_last_msg.note, velocity=ANCHOR_VELOCITY)))
        events.append((off, Message('note_off', channel=part_last_msg.channel, note=part_last_msg.note, velocity=0)))

    # 파일 끝(end_of_track)도 원곡과 동일하게
    track_end = max(t for t, _ in events) if events else 0
    events = [(t, m) for t, m in events if m.type != 'end_of_track']
    events.append((max(timeline['end'], track_end), MetaMessage('end_of_track')))
    new_mid.tracks[idx] = to_track(events)

def extract_single_instrument(mid, inst_key, force_ch0=True, align_timeline=True):
    """지정된 단일 악기(drum, guitar, bass, keyboard)를 추출하여 새 MidiFile 객체 생성"""
    cfg = INSTRUMENT_CONFIG[inst_key]
    new_mid = MidiFile(type=1)
    new_mid.ticks_per_beat = mid.ticks_per_beat

    conductor = build_conductor_track(mid)
    if len(conductor) > 0:
        new_mid.tracks.append(conductor)

    total_notes_extracted = 0
    total_mapped = 0
    part_indices = []
    # 채널 10 드럼이 있는 파일이면 트랙 이름만으로 드럼 판정하지 않음
    # (이름 때문에 멜로디 트랙이 드럼 파일에 섞여 들어가는 것 방지)
    drum_by_name = not file_has_drum_channel(mid)

    for track in mid.tracks:
        info = analyze_track_instrument(track, drum_by_name)
        if info['total_notes'] == 0:
            continue

        track_inst = info['instrument']
        # 여러 채널이 섞인 트랙(Type 0 MIDI 등)은 채널별로 악기를 판별
        per_channel = len(info['channels']) > 1
        if not per_channel and track_inst != inst_key:
            continue

        channel_program = {}  # 채널별 현재 프로그램 번호

        def channel_matches(ch):
            if not per_channel:
                return True
            if ch == 9:
                return inst_key == 'drum'
            if ch in channel_program:
                return classify_program(channel_program[ch]) == inst_key
            # 프로그램 정보가 없는 채널은 트랙 판별 결과를 따름
            fallback = track_inst if track_inst != 'drum' else 'keyboard'
            return fallback == inst_key

        new_track = MidiTrack()
        mapper = DrumNoteMapper() if cfg['remap_drums'] else None
        abs_tick = 0
        last_written_tick = 0
        has_valid_note = False

        def emit(m):
            nonlocal last_written_tick
            new_track.append(m.copy(time=abs_tick - last_written_tick))
            last_written_tick = abs_tick

        for msg in track:
            abs_tick += msg.time

            if msg.is_meta:
                # 템포 계열은 컨덕터 트랙으로 이동, end_of_track은 저장 시 자동 생성
                if msg.type not in CONDUCTOR_META_TYPES and msg.type != 'end_of_track':
                    emit(msg)
                continue

            if msg.type == 'program_change':
                channel_program[msg.channel] = msg.program

            if not hasattr(msg, 'channel'):
                # sysex 등 채널이 없는 메시지는 단일 악기 트랙에서만 유지
                if not per_channel:
                    emit(msg)
                continue

            if not channel_matches(msg.channel):
                continue

            out_msgs = [msg]
            if msg.type in ('note_on', 'note_off'):
                if mapper is not None:
                    out_msgs = mapper.process(msg, abs_tick)
                    if is_note_on(msg) and get_mapped_drum_note(msg.note) is None:
                        continue  # 버려진 음표는 개수에 포함하지 않음
                    if is_note_on(msg):
                        total_mapped += 1
                if is_note_on(msg):
                    total_notes_extracted += 1
                    has_valid_note = True

            for out in out_msgs:
                if force_ch0:
                    out = out.copy(channel=0)
                emit(out)

        if has_valid_note:
            part_indices.append(len(new_mid.tracks))
            new_mid.tracks.append(new_track)

    if align_timeline:
        align_to_song_timeline(new_mid, part_indices, song_timeline(mid))

    return new_mid, total_notes_extracted, total_mapped

def strip_known_suffixes(base_name):
    """'곡 (Drum) (Guitar)' 처럼 이미 붙은 악기 접미사를 모두 제거"""
    changed = True
    while changed:
        changed = False
        for cfg in INSTRUMENT_CONFIG.values():
            if base_name.endswith(cfg['suffix']):
                base_name = base_name[:-len(cfg['suffix'])]
                changed = True
    return base_name

def is_generated_file(path):
    """이 프로그램이 만든 결과 파일인지 (폴더 일괄 처리 시 재변환 방지용)"""
    base = os.path.splitext(os.path.basename(path))[0]
    return strip_known_suffixes(base) != base

def extract_selected_instruments(input_path, selected_keys, force_ch0=True):
    """
    밴드스코어에서 사용자가 선택한 악기 목록(drum, guitar, bass, keyboard)을 각각 추출하여 저장합니다.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {input_path}")

    mid = MidiFile(input_path)
    dir_name, file_name = os.path.split(input_path)
    base_name, ext = os.path.splitext(file_name)
    out_base = strip_known_suffixes(base_name)

    results = []
    for inst_key in selected_keys:
        if inst_key not in INSTRUMENT_CONFIG:
            continue
        cfg = INSTRUMENT_CONFIG[inst_key]
        new_mid, total_notes, mapped_notes = extract_single_instrument(mid, inst_key, force_ch0=force_ch0)

        # 노트가 1개 이상 추출된 경우에만 파일 저장
        if total_notes > 0:
            output_path = os.path.join(dir_name, f"{out_base}{cfg['suffix']}{ext}")
            # 원본 파일을 덮어쓰지 않음 (드럼 매핑이 이중으로 적용되는 것 방지)
            if os.path.normcase(os.path.abspath(output_path)) == os.path.normcase(os.path.abspath(input_path)):
                print(f"[{cfg['name']}] 원본 파일과 저장 경로가 같아 건너뜁니다: {file_name}")
                continue
            new_mid.save(output_path)
            results.append({
                'key': inst_key,
                'name': cfg['name'],
                'path': output_path,
                'total_notes': total_notes,
                'mapped_notes': mapped_notes,
                'is_drum': cfg['remap_drums']
            })
            print(f"[{cfg['name']}] 추출 완료 ({total_notes}개 음표): {os.path.basename(output_path)}")
        else:
            print(f"[{cfg['name']}] 트랙 또는 음표가 감지되지 않아 건너뜁니다.")

    return results

def print_analysis(input_path):
    """트랙별 판별 결과 출력 (어떤 트랙이 어떤 악기로 분류되는지 확인용)"""
    mid = MidiFile(input_path)
    drum_by_name = not file_has_drum_channel(mid)
    print(f"파일: {os.path.basename(input_path)} (Type {mid.type}, {len(mid.tracks)}개 트랙, TPB {mid.ticks_per_beat})")
    for idx, track in enumerate(mid.tracks):
        info = analyze_track_instrument(track, drum_by_name)
        notes = [m.note for m in track if is_note_on(m)]
        note_range = f"{min(notes)}~{max(notes)}" if notes else "-"
        print(f"  [{idx}] {info['name'] or '(이름 없음)'!r}: {info['instrument']} | "
              f"음표 {info['total_notes']}개 (범위 {note_range}) | "
              f"채널 {sorted(info['channels'])} | 프로그램 {sorted(info['programs'])}")

if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == '--analyze':
        for arg in sys.argv[2:]:
            print_analysis(arg)
    elif len(sys.argv) > 1:
        print("="*65)
        print("🎸 밴드스코어 악기 선택 추출기 (기타 / 베이스 / 건반 / 드럼)")
        print("   * 드럼: 스타 레조넌스 음계 이동 적용")
        print("   * 기타/베이스/건반: 원본 음계 보존")
        print("="*65)
        for arg in sys.argv[1:]:
            if os.path.exists(arg) and arg.lower().endswith(('.mid', '.midi')):
                print(f"\n처리 중: {os.path.basename(arg)}")
                # CLI 기본값: 4대 악기 모두 추출
                try:
                    extract_selected_instruments(arg, ['drum', 'guitar', 'bass', 'keyboard'])
                except Exception as e:
                    print(f"오류 발생: {e}")
        input("\n[Enter 키를 누르면 종료됩니다...]")
    else:
        try:
            from Convert_Midi_GUI import main as run_gui
        except Exception as e:
            print(f"GUI 실행 중 오류: {e}")
            print("MIDI 파일을 이 스크립트에 드래그 앤 드롭하거나, customtkinter를 설치한 뒤 GUI를 사용해주세요.")
            input("\n[Enter 키를 누르면 종료됩니다...]")
        else:
            run_gui()
