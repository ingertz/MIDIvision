import mido
from mido import MidiFile, MidiTrack, Message
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
    79: 72, 80: 72, 81: 72, 82: 77, 83: 77
}

def get_mapped_drum_note(note):
    """드럼 노트 변환 시 미등록 음표가 70(0번키) 등으로 빠져나가지 않도록 완벽 보정"""
    if note in DRUM_MAP:
        return DRUM_MAP[note]
    # 안전 폴백: 범위를 벗어난 알 수 없는 드럼 음표 보정
    if note in (35, 36): return 65       # Kick (F)
    if note in (37, 38, 39, 40): return 72  # Snare (Q)
    if note in (42, 44): return 62       # Closed Hi-Hat (S)
    if note in (46, 74): return 79       # Open Hi-Hat (T)
    if 41 <= note <= 43: return 69       # Floor Tom (H)
    if 45 <= note <= 48: return 74       # Mid Tom (W)
    if note in (50, 58): return 76       # High Tom (E)
    if note in (51, 53, 59): return 81   # Ride (Y)
    if note < 35: return 72              # 27~34 스틱/효과음 -> Q키 (스네어)
    return 77 # R (Crash)

# 악기별 판별 설정 (기타, 베이스, 건반, 드럼)
INSTRUMENT_CONFIG = {
    'drum': {
        'name': '드럼',
        'suffix': ' (Drum)',
        'remap_drums': True,  # 드럼은 스타 레조넌스 음계 이동(매핑) 유지!
        'keywords': ['drum', 'drums', 'perc', 'percussion', '드럼', '타악기', 'battery', 'kit', 'cymb'],
        'channel_match': lambda ch: ch == 9,
        'program_match': lambda p: False,
    },
    'guitar': {
        'name': '기타',
        'suffix': ' (Guitar)',
        'remap_drums': False,  # 멜로디 악기는 원음 유지
        'keywords': ['guitar', 'gt', 'guit', 'eg', 'ag', 'clean', 'dist', 'overdrive', 'lead gt', 'ac gt', 'el gt', '기타'],
        'channel_match': lambda ch: ch != 9,
        'program_match': lambda p: 24 <= p <= 31,
    },
    'bass': {
        'name': '베이스',
        'suffix': ' (Bass)',
        'remap_drums': False,  # 멜로디 악기는 원음 유지
        'keywords': ['bass', 'eb', 'slap', 'pick bass', 'fingered bass', 'ac bass', 'el bass', '베이스'],
        'channel_match': lambda ch: ch != 9,
        'program_match': lambda p: 32 <= p <= 39,
    },
    'keyboard': {
        'name': '건반',
        'suffix': ' (Keyboard)',
        'remap_drums': False,  # 멜로디 악기는 원음 유지
        'keywords': ['piano', 'key', 'keyboard', 'synth', 'organ', 'clav', 'rhodes', 'harpsichord', '피아노', '건반', '신스'],
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
        if not re.match(r'^[a-z0-9_-]+$', kw_lower):
            if kw_lower in lower:
                return True
        else:
            pattern = r'(?<![a-z0-9])' + re.escape(kw_lower) + r'(?![a-z0-9])'
            if re.search(pattern, lower):
                return True
    return False

def analyze_track_instrument(track):
    """
    트랙의 이름, 프로그램 번호, 채널, 음표 정보를 기반으로 악기 판별:
    - 드럼: 채널 9 또는 드럼 키워드
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
            name = msg.name
        elif msg.type in ['set_tempo', 'time_signature', 'key_signature']:
            has_tempo = True
        elif msg.type == 'program_change':
            programs.add(msg.program)
        elif msg.type in ['note_on', 'note_off']:
            if getattr(msg, 'velocity', 1) > 0:
                total_notes += 1
            if hasattr(msg, 'channel'):
                channels.add(msg.channel)

    # 음표가 없는 메타/템포 트랙
    if total_notes == 0:
        return {
            'name': name,
            'total_notes': 0,
            'has_tempo': has_tempo,
            'instrument': 'tempo' if has_tempo else 'empty',
            'channels': channels,
            'programs': programs
        }

    # 1. 드럼 판별 (Channel 9 또는 드럼 키워드)
    drum_kw = ['drum', 'drums', 'perc', 'percussion', '드럼', '타악기', 'battery', 'kit', 'cymb']
    if (9 in channels) or match_keyword(name, drum_kw):
        return {
            'name': name,
            'total_notes': total_notes,
            'has_tempo': has_tempo,
            'instrument': 'drum',
            'channels': channels,
            'programs': programs
        }

    # 2. 베이스 판별 (베이스 키워드 또는 베이스 프로그램 32~39)
    bass_kw = ['bass', 'eb', 'slap', 'pick bass', 'fingered bass', 'ac bass', 'el bass', '베이스']
    if match_keyword(name, bass_kw) or any(32 <= p <= 39 for p in programs):
        return {
            'name': name,
            'total_notes': total_notes,
            'has_tempo': has_tempo,
            'instrument': 'bass',
            'channels': channels,
            'programs': programs
        }

    # 3. 기타 판별 (기타 키워드 또는 기타 프로그램 24~31)
    guitar_kw = ['guitar', 'gt', 'guit', 'eg', 'ag', 'clean', 'dist', 'overdrive', 'lead gt', 'ac gt', 'el gt', '기타']
    if match_keyword(name, guitar_kw) or any(24 <= p <= 31 for p in programs):
        return {
            'name': name,
            'total_notes': total_notes,
            'has_tempo': has_tempo,
            'instrument': 'guitar',
            'channels': channels,
            'programs': programs
        }

    # 4. 그 외 모든 악기 -> 건반(Keyboard)으로 배정!
    # (색소폰, 트럼펫, 브라스, 바이올린, 스트링, 플루트, 오보에, 보컬 멜로디, 피아노, 오르간, 신스 등)
    return {
        'name': name,
        'total_notes': total_notes,
        'has_tempo': has_tempo,
        'instrument': 'keyboard',
        'channels': channels,
        'programs': programs
    }

def extract_single_instrument(mid, inst_key, force_ch0=True):
    """지정된 단일 악기(drum, guitar, bass, keyboard)를 추출하여 새 MidiFile 객체 생성"""
    cfg = INSTRUMENT_CONFIG[inst_key]
    new_mid = MidiFile(type=mid.type)
    new_mid.ticks_per_beat = mid.ticks_per_beat

    target_tracks = []
    tempo_tracks = []

    # 트랙 분석
    for idx, track in enumerate(mid.tracks):
        info = analyze_track_instrument(track)
        if info['instrument'] == inst_key and info['total_notes'] > 0:
            target_tracks.append(idx)
        elif info['has_tempo'] and info['total_notes'] == 0:
            tempo_tracks.append(idx)

    fallback_filter = (len(target_tracks) == 0)
    total_notes_extracted = 0
    total_mapped = 0

    for idx, track in enumerate(mid.tracks):
        if idx in tempo_tracks:
            new_mid.tracks.append(track.copy())
            continue

        if idx in target_tracks or fallback_filter:
            new_track = MidiTrack()
            accumulated_time = 0
            has_valid_note = False
            current_program = None

            for msg in track:
                accumulated_time += msg.time

                if msg.is_meta:
                    new_track.append(msg.copy(time=accumulated_time))
                    accumulated_time = 0
                    continue

                if msg.type == 'program_change':
                    current_program = msg.program

                # 폴백 필터링 검사 (단일 트랙에 모든 악기가 섞인 경우)
                is_target_note = False
                if not fallback_filter:
                    is_target_note = True
                else:
                    ch = getattr(msg, 'channel', 0)
                    prog = current_program if current_program is not None else 0
                    if inst_key == 'drum' and ch == 9:
                        is_target_note = True
                    elif inst_key == 'bass' and ch != 9 and (32 <= prog <= 39):
                        is_target_note = True
                    elif inst_key == 'guitar' and ch != 9 and (24 <= prog <= 31):
                        is_target_note = True
                    elif inst_key == 'keyboard' and ch != 9 and not (24 <= prog <= 39):
                        # 색소폰, 관악기, 현악기, 피아노 등 드럼/기타/베이스 제외한 모든 음표
                        is_target_note = True

                if msg.type in ['note_on', 'note_off'] and is_target_note:
                    total_notes_extracted += 1
                    
                    # 드럼인 경우에만 스타 레조넌스 음계 이동(매핑) 적용!
                    if cfg['remap_drums']:
                        new_note = get_mapped_drum_note(msg.note)
                        total_mapped += 1
                    else:
                        # 기타, 베이스, 건반(색소폰 등 포함)은 원음 유지!
                        new_note = msg.note

                    kwargs = {'note': new_note, 'time': accumulated_time}
                    if force_ch0:
                        kwargs['channel'] = 0
                    new_track.append(msg.copy(**kwargs))
                    accumulated_time = 0
                    has_valid_note = True
                elif msg.type not in ['note_on', 'note_off'] and is_target_note:
                    kwargs = {'time': accumulated_time}
                    if force_ch0 and hasattr(msg, 'channel'):
                        kwargs['channel'] = 0
                    new_track.append(msg.copy(**kwargs))
                    accumulated_time = 0

            if has_valid_note:
                new_mid.tracks.append(new_track)

    return new_mid, total_notes_extracted, total_mapped

def extract_selected_instruments(input_path, selected_keys, force_ch0=True):
    """
    밴드스코어에서 사용자가 선택한 악기 목록(drum, guitar, bass, keyboard)을 각각 추출하여 저장합니다.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {input_path}")

    mid = MidiFile(input_path)
    dir_name, file_name = os.path.split(input_path)
    base_name, ext = os.path.splitext(file_name)

    results = []
    for inst_key in selected_keys:
        if inst_key not in INSTRUMENT_CONFIG:
            continue
        cfg = INSTRUMENT_CONFIG[inst_key]
        new_mid, total_notes, mapped_notes = extract_single_instrument(mid, inst_key, force_ch0=force_ch0)

        # 노트가 1개 이상 추출된 경우에만 파일 저장
        if total_notes > 0:
            suffix = cfg['suffix']
            # 파일명 중복 방지
            out_base = base_name if not base_name.endswith(suffix) else base_name[:-len(suffix)]
            output_path = os.path.join(dir_name, f"{out_base}{suffix}{ext}")
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

if __name__ == "__main__":
    if len(sys.argv) > 1:
        print("="*65)
        print("🎸 밴드스코어 악기 선택 추출기 (기타 / 베이스 / 건반 / 드럼)")
        print("   * 드럼: 스타 레조넌스 음계 이동 적용")
        print("   * 기타/베이스/건반: 원본 음계 보존")
        print("="*65)
        for arg in sys.argv[1:]:
            if os.path.exists(arg) and arg.lower().endswith(('.mid', '.midi')):
                print(f"\n처리 중: {os.path.basename(arg)}")
                # CLI 기본값: 4대 악기 모두 추출
                extract_selected_instruments(arg, ['drum', 'guitar', 'bass', 'keyboard'])
        input("\n[Enter 키를 누르면 종료됩니다...]")
    else:
        try:
            from Convert_Midi_GUI import main as run_gui
            run_gui()
        except Exception as e:
            test_file = "Butter-Fly (Drum).mid"
            if os.path.exists(test_file):
                extract_selected_instruments(test_file, ['drum'])
            else:
                print(f"GUI 실행 중 오류: {e}")
                print("MIDI 파일을 드래그 앤 드롭하거나 GUI를 사용해주세요.")
                input("\n[Enter 키를 누르면 종료됩니다...]")