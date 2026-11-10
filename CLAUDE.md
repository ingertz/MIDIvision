# MIDIvision

밴드 MIDI·악보의 드럼을 스타 레조넌스 인게임 음계로 매핑하여 합본 MIDI(`곡명_converted.mid`)로 내보내는 Python 프로그램. 사용법과 동작 설명은 `README.md`.

- `Convert_Midi.py` — 드럼 음계 매핑 및 합본 MIDI 생성 본체. 악보 입력은 `Convert_Score.py` 로 MIDI 로 바꾼 뒤 처리
- `Convert_Score.py` — PDF·MusicXML → MIDI (스캔본은 Audiveris 호출)
- `Convert_Vector.py` — 도형으로 된 PDF 에서 악보를 직접 읽기
- `Convert_Glyphs.py` — 악보 글꼴의 글자로 찍힌 PDF(MuseScore)의 글자를 기호로 바꾸기. `Convert_Vector.py` 가 부름
- `Convert_Audio.py` — 음원(MP3 등) → 악기별 MIDI (Demucs 로 나누고 Basic Pitch·드럼 타격 찾기로 받아 적음)
- `Convert_Pages.py` — Audiveris 프로젝트(.omr) 페이지 데이터에서 드럼 X 음표와 1번·2번 괄호 보충
- `Convert_Midi_GUI.py` — GUI. `Convert_Midi.bat` 이 실행

## 컨텍스트 아끼기

- 서로 기다릴 필요가 없는 조회는 한 응답에 같이 보낸다.
- 파일은 1,000줄 안팎이다. 통째로 읽지 말고 `Grep` 으로 위치를 찾아 필요한 범위만 읽는다.
- 변환 결과를 확인할 때는 음표 전체를 출력하지 말고 마디 수·음표 수·어긋난 마디 같은 요약만 출력한다.
- PDF·MIDI 같은 바이너리는 내용을 덤프하지 않는다.

## Compact instructions

남길 것: 지금 고치는 증상과 재현에 쓴 파일 경로, 원인으로 확인한 것, 수정한 함수와 파일, 아직 안 맞는 악보·마디, 사용자 지시 원문.
버릴 것: 도구 출력 본문, 디버그 덤프, 끝난 탐색 과정.
