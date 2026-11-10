import mido
from mido import MidiFile, MidiTrack, Message, MetaMessage
import io
import sys
import os
import re

# 악보 파일(PDF / MusicXML)은 Convert_Score.py, 음원(MP3 등)은 Convert_Audio.py가 MIDI로 바꾼 뒤 드럼 매핑 처리
from Convert_Score import SCORE_EXTS, score_to_midi
from Convert_Audio import AUDIO_EXTS

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

DRUM_KEYWORDS = ['drum', 'drums', 'perc', 'percussion', '드럼', '타악기', 'battery', 'kit', 'cymb', 'ドラム', 'パーカッション', '打楽器']

def get_mapped_drum_note(note):
    """
    GM 드럼 음표 -> 인게임 키. 타악기 범위(27~87) 밖의 음표는 None을 반환하여 버립니다.
    (예전에는 범위 밖 음표를 전부 크래시(R)로 바꿔서, 드럼이 아닌 음표가 섞이면
     크래시가 계속 울리는 문제가 있었음)
    """
    if note not in DRUM_NOTE_RANGE or note in SKIP_DRUM_NOTES:
        return None
    return DRUM_MAP[note]

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

def is_note_on(msg):
    return msg.type == 'note_on' and msg.velocity > 0

def is_note_off(msg):
    return msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0)

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

# 뱅크 셀렉트(CC0) 값이 이 값이면 야마하 XG 드럼 킷 (채널 10이 아니어도 드럼)
XG_DRUM_BANKS = (126, 127)

def track_drum_channels(track):
    """드럼 채널 집합: 채널 10(인덱스 9) + XG 드럼 뱅크(127/126)가 선택된 채널"""
    drum = {9}
    for msg in track:
        if msg.type == 'control_change' and msg.control == 0 and msg.value in XG_DRUM_BANKS:
            drum.add(msg.channel)
    return drum

def file_has_drum_channel(mid):
    """파일 어딘가에 드럼 채널(채널 10 또는 XG 드럼 킷) 음표가 있는지"""
    for track in mid.tracks:
        drum = track_drum_channels(track)
        if any(is_note_on(msg) and msg.channel in drum for msg in track):
            return True
    return False

def analyze_track_info(track, drum_by_name=True):
    """트랙의 이름, 채널, 프로그램, 드럼 여부 정보를 분석합니다."""
    name = ""
    channels = set()
    programs = set()
    total_notes = 0

    for msg in track:
        if msg.type == 'track_name':
            name = decode_text(msg.name)
        elif msg.type == 'program_change':
            programs.add(msg.program)
        elif msg.type in ('note_on', 'note_off'):
            if is_note_on(msg):
                total_notes += 1
            channels.add(msg.channel)

    drum_channels = track_drum_channels(track)
    # 단일 채널 트랙이고 파일에 채널 10 드럼이 없을 때 트랙 이름이 드럼 키워드면 해당 채널도 드럼으로 취급
    if drum_by_name and len(channels) == 1 and match_keyword(name, DRUM_KEYWORDS):
        drum_channels = drum_channels | channels

    is_drum = bool(channels & drum_channels)
    return {
        'name': name,
        'total_notes': total_notes,
        'channels': channels,
        'programs': programs,
        'drum_channels': drum_channels,
        'is_drum': is_drum,
    }

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

def convert_midi_drums(mid):
    """
    원본 MIDI의 모든 트랙과 채널 구조를 그대로 유지하면서,
    드럼 트랙/채널의 음표만 스타 레조넌스 인게임 건반 음계로 매핑하여 합본 MidiFile을 생성합니다.
    반환: (new_mid, 전체 유효 음표 수, 매핑된 드럼 음표 수)
    """
    new_mid = MidiFile(type=mid.type)
    new_mid.ticks_per_beat = mid.ticks_per_beat

    total_notes = 0
    mapped_drum_notes = 0
    drum_by_name = not file_has_drum_channel(mid)

    for track in mid.tracks:
        info = analyze_track_info(track, drum_by_name)
        drum_chs = info['drum_channels']
        mappers = {}  # 채널별 DrumNoteMapper

        new_track = MidiTrack()
        abs_tick = 0
        last_written_tick = 0

        def emit(m):
            nonlocal last_written_tick
            new_track.append(m.copy(time=abs_tick - last_written_tick))
            last_written_tick = abs_tick

        for msg in track:
            abs_tick += msg.time

            if msg.type in ('note_on', 'note_off') and msg.channel in drum_chs:
                mapper = mappers.setdefault(msg.channel, DrumNoteMapper())
                out_msgs = mapper.process(msg, abs_tick)
                if is_note_on(msg):
                    if get_mapped_drum_note(msg.note) is None:
                        continue  # 버려진 드럼 음표는 개수에 포함하지 않음
                    mapped_drum_notes += 1
                    total_notes += 1
                for out in out_msgs:
                    emit(out)
            else:
                if msg.type in ('note_on', 'note_off') and is_note_on(msg):
                    total_notes += 1
                emit(msg)

        new_mid.tracks.append(new_track)

    return new_mid, total_notes, mapped_drum_notes

CONVERTED_SUFFIX = '_converted'

# 예전 버전 결과 파일 이름 앞/뒤에 붙던 악기 표시: '(Drum)__곡명', '(Drum)_01_곡명', '곡명 (Drum)'
LEGACY_PART_NAMES = ('(Drum)', '(Guitar)', '(Bass)', '(Keyboard)')
PART_PREFIX = re.compile(
    '^(?:' + '|'.join(re.escape(p) for p in LEGACY_PART_NAMES) + r')_(?:\d+_|_)?')

def output_file_name(song_name, ext):
    """결과 파일 이름: '곡명_converted.mid'"""
    return f"{song_name}{CONVERTED_SUFFIX}{ext}"

def strip_part_labels(base_name):
    """
    결과 파일 이름에서 '_converted' 또는 예전 악기 표시를 모두 제거하고 곡명만 남김
    """
    changed = True
    while changed:
        changed = False
        if base_name.lower().endswith(CONVERTED_SUFFIX):
            base_name = base_name[:-len(CONVERTED_SUFFIX)]
            changed = True
        stripped = PART_PREFIX.sub('', base_name)
        if stripped != base_name:
            base_name = stripped
            changed = True
        for p in LEGACY_PART_NAMES:
            suffix = f" {p}"
            if base_name.endswith(suffix):
                base_name = base_name[:-len(suffix)]
                changed = True
    return base_name

def is_generated_file(path):
    """이 프로그램이 만든 결과 파일인지 (폴더 일괄 처리 시 재변환 방지용)"""
    base = os.path.splitext(os.path.basename(path))[0]
    return strip_part_labels(base) != base

def repair_smf_bytes(data):
    """
    규격 위반 MIDI 바이트 복구 (파일 길이/구조는 그대로 두고 값만 교정).

    예: Rally go round 의 바이올린 트랙은 피치벤드가 최저값 아래로 내려가면서
        음수 값(f8 ff 등)이 그대로 기록되어 있음.
        - mido는 'data byte must be in range 0..127' 오류로 파일을 거부하고
        - clip=True로 읽어도 f8을 '타이밍 클럭' 메시지로 오인해서 뒤쪽 트랙 전체가 밀려
          곡 길이가 20분으로 늘어나는 등 망가짐.
    러닝 스테이터스 중에 0x80 이상 바이트가 연달아 나오면 상태 바이트가 아닌 깨진 데이터로 간주하고,
    범위를 넘은 데이터는 피치벤드면 최저값(0), 그 외는 127로 교정합니다.
    반환: (복구된 바이트, 교정한 개수)
    """
    buf = bytearray(data)
    fixed = 0
    pos = 14 if buf[:4] == b'MThd' else 0
    if buf[:4] == b'MThd':
        pos = 8 + int.from_bytes(buf[4:8], 'big')

    def read_vlq(i, end):
        value = 0
        while i < end:
            b = buf[i]
            i += 1
            value = (value << 7) | (b & 0x7F)
            if b < 0x80:
                break
        return value, i

    while pos + 8 <= len(buf):
        length = int.from_bytes(buf[pos + 4:pos + 8], 'big')
        start, end = pos + 8, min(pos + 8 + length, len(buf))
        if buf[pos:pos + 4] == b'MTrk':
            i = start
            status = None
            while i < end:
                _, i = read_vlq(i, end)  # delta time
                if i >= end:
                    break
                b = buf[i]
                # 정상 파일에서는 상태 바이트 바로 뒤에 0x80 이상이 올 수 없음(메타 타입/데이터는 0~127).
                # 러닝 스테이터스 중에 0x80 이상이 두 개 연속이면 음수로 기록된 깨진 데이터로 판단
                # (예: f8 ff, e7 fd, d6 fb = 16비트 음수 피치벤드)
                is_running = b < 0x80 or (
                    status is not None and b not in (0xF0, 0xF7)
                    and i + 1 < end and buf[i + 1] > 0x7F
                )
                if not is_running:
                    i += 1
                    if b == 0xFF:  # 메타 메시지
                        i += 1
                        size, i = read_vlq(i, end)
                        i += size
                        continue
                    if b in (0xF0, 0xF7):  # sysex
                        size, i = read_vlq(i, end)
                        i += size
                        continue
                    if b >= 0xF0:
                        continue
                    status = b
                n_data = 1 if (status & 0xF0) in (0xC0, 0xD0) else 2
                data_bytes = buf[i:i + n_data]
                if any(x > 0x7F for x in data_bytes):
                    fixed += 1
                    if (status & 0xF0) == 0xE0 and n_data == 2:
                        buf[i] = 0x00      # 음수로 넘어간 피치벤드 -> 최저값
                        buf[i + 1] = 0x00
                    else:
                        for k in range(n_data):
                            if i + k < end and buf[i + k] > 0x7F:
                                buf[i + k] = 0x7F
                i += n_data
        pos = start + length
    return bytes(buf), fixed

def load_midi(input_path):
    """
    MIDI 파일 읽기. 규격 위반(데이터 바이트 > 127) 파일은 자동 복구 후 읽습니다.
    반환: (MidiFile, 복구 여부)
    """
    try:
        return MidiFile(input_path), False
    except (OSError, ValueError) as e:
        if 'data byte' not in str(e):
            raise
    with open(input_path, 'rb') as f:
        data, _ = repair_smf_bytes(f.read())
    return MidiFile(file=io.BytesIO(data)), True

MIDI_EXTS = ('.mid', '.midi')
INPUT_EXTS = MIDI_EXTS + SCORE_EXTS + AUDIO_EXTS

def convert_file(input_path, log=print, score_bpm=None):
    """
    MIDI / PDF 악보 / MusicXML / 음원(MP3 등)을 받아 드럼만 인게임 건반 음계로 매핑한 뒤
    하나로 합쳐진 합본 MIDI 파일('곡명_converted.mid')로 저장합니다.
    score_bpm: 악보에서 템포를 읽지 못했을 때 쓸 템포
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {input_path}")

    dir_name, file_name = os.path.split(input_path)
    base_name, ext = os.path.splitext(file_name)
    if ext.lower() in SCORE_EXTS:
        mid = score_to_midi(input_path, log=log, fallback_bpm=score_bpm)
        ext = '.mid'
    elif ext.lower() in AUDIO_EXTS:
        from Convert_Audio import audio_to_midi     # 무거운 패키지(PyTorch 등)는 음원을 넣을 때만 읽음
        mid = audio_to_midi(input_path, log=log)
        ext = '.mid'
    else:
        mid, repaired = load_midi(input_path)
        if repaired:
            print(f"⚠️ 규격에 맞지 않는 데이터가 있어 자동 복구해서 읽었습니다: {os.path.basename(input_path)}")
    out_base = strip_part_labels(base_name)

    new_mid, total_notes, mapped_notes = convert_midi_drums(mid)
    if total_notes <= 0:
        print("트랙 또는 음표가 감지되지 않아 건너뜁니다.")
        return None

    output_path = os.path.join(dir_name, output_file_name(out_base, ext))
    # 원본 파일을 덮어쓰지 않음 (드럼 매핑이 이중으로 적용되는 것 방지)
    if os.path.normcase(os.path.abspath(output_path)) == os.path.normcase(os.path.abspath(input_path)):
        print(f"원본 파일과 저장 경로가 같아 건너뜁니다: {file_name}")
        return None

    new_mid.save(output_path)
    print(f"[합본 변환 완료] {os.path.basename(output_path)} (전체 음표: {total_notes}개, 드럼 매핑: {mapped_notes}개)")
    return {
        'path': output_path,
        'total_notes': total_notes,
        'mapped_notes': mapped_notes,
    }

def print_analysis(input_path):
    """트랙별 정보 출력 (어떤 트랙이 드럼으로 인식되는지 확인용)"""
    mid, repaired = load_midi(input_path)
    if repaired:
        print("⚠️ 규격에 맞지 않는 데이터가 있어 자동 복구해서 읽었습니다.")
    drum_by_name = not file_has_drum_channel(mid)
    print(f"파일: {os.path.basename(input_path)} (Type {mid.type}, {len(mid.tracks)}개 트랙, TPB {mid.ticks_per_beat})")
    for idx, track in enumerate(mid.tracks):
        info = analyze_track_info(track, drum_by_name)
        notes = [m.note for m in track if is_note_on(m)]
        note_range = f"{min(notes)}~{max(notes)}" if notes else "-"
        role = 'drum (음계 매핑 대상)' if info['is_drum'] else ('melodic (원음 유지)' if info['total_notes'] > 0 else 'meta/empty')
        print(f"  [{idx}] {info['name'] or '(이름 없음)'!r}: {role} | "
              f"음표 {info['total_notes']}개 (범위 {note_range}) | "
              f"채널 {sorted(info['channels'])} | 프로그램 {sorted(info['programs'])}")

if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == '--analyze':
        for arg in sys.argv[2:]:
            print_analysis(arg)
    elif len(sys.argv) > 1:
        print("=" * 65)
        print("🎼 스타 레조넌스 합본 MIDI 변환기 (드럼 인게임 음계 매핑)")
        print("   * 드럼: 스타 레조넌스 인게임 건반 노트로 자동 변환")
        print("   * 기타/베이스/건반 등: 원본 트랙·채널·음계 그대로 유지")
        print("=" * 65)
        for arg in sys.argv[1:]:
            if os.path.exists(arg) and arg.lower().endswith(INPUT_EXTS):
                print(f"\n처리 중: {os.path.basename(arg)}")
                try:
                    convert_file(arg)
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
