import os
import tempfile

# 음원(MP3 등) -> 악기별 MIDI
#
# 1. Demucs(htdemucs_6s)로 곡을 드럼 / 베이스 / 기타 / 피아노 / 보컬 / 나머지 여섯 갈래로 나눕니다.
# 2. 드럼은 낮은음(킥) / 중간음과 스네어 줄 소리(스네어) / 높은음(하이햇·심벌) 대역마다 소리가 갑자기
#    커지는 순간을 찾아 타격으로 적습니다.
# 3. 나머지 갈래는 Basic Pitch(Spotify)로 음높이와 길이를 읽습니다.
# 4. 박을 찾아(librosa) 모든 음을 16분음표 격자에 맞추고, 박 간격의 중앙값을 템포로 씁니다.
#    음원은 사람이 연주한 것이라 템포가 조금씩 흔들리므로, 시간이 아니라 "몇 번째 박인지"로 옮겨 적어야
#    파트끼리, 그리고 게임의 박자와 맞습니다.
#
# 악보나 MIDI보다 훨씬 거칩니다: 베이스·보컬 멜로디는 쓸 만하고, 드럼은 킥·스네어·하이햇 위주,
# 기타·피아노처럼 화음이 많은 악기는 틀린 음이 섞입니다.

AUDIO_EXTS = ('.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac')
PACKAGES = {'numpy': 'numpy', 'soundfile': 'soundfile', 'librosa': 'librosa', 'torch': 'torch',
            'demucs': 'demucs', 'basic_pitch': 'basic-pitch', 'av': 'av', 'onnxruntime': 'onnxruntime'}

# 설치 명령. basic-pitch 는 Python 3.12 이상에서 설치되지 않는 TensorFlow 를 요구하므로 의존성 없이 깔고,
# 대신 ONNX 모델로 돌림 (onnxruntime)
INSTALL = ('pip install soundfile librosa demucs av onnxruntime mir_eval pretty_midi',
           'pip install --no-deps basic-pitch resampy')

SEPARATION_MODEL = 'htdemucs_6s'
GRID = 4                 # 한 박을 몇 칸으로 나눠 맞출지 (4 = 16분음표)
TICKS = 480

# 갈래별: (트랙 이름, MIDI 채널, 악기 번호, Basic Pitch 설정)
# 트랙 이름은 Convert_Midi 가 파트를 가르는 기준 ('Vocal' 과 'Synth' 는 건반으로 들어감)
STEMS = {
    'bass':   ('Bass', 1, 33, {'minimum_frequency': 30, 'maximum_frequency': 400, 'onset_threshold': 0.5,
                               'frame_threshold': 0.3, 'minimum_note_length': 90}),
    'guitar': ('Guitar', 2, 29, {'minimum_frequency': 70, 'maximum_frequency': 1400, 'onset_threshold': 0.6,
                                 'frame_threshold': 0.35, 'minimum_note_length': 90}),
    'piano':  ('Piano', 3, 0, {'minimum_frequency': 27, 'maximum_frequency': 4200, 'onset_threshold': 0.55,
                               'frame_threshold': 0.3, 'minimum_note_length': 70}),
    'vocals': ('Vocal', 4, 52, {'minimum_frequency': 80, 'maximum_frequency': 1100, 'onset_threshold': 0.55,
                                'frame_threshold': 0.35, 'minimum_note_length': 110}),
    'other':  ('Synth', 5, 81, {'minimum_frequency': 40, 'maximum_frequency': 4200, 'onset_threshold': 0.6,
                                'frame_threshold': 0.35, 'minimum_note_length': 90}),
}
# 거의 비어 있는 갈래(그 악기가 없는 곡)는 다른 악기가 새어 든 소리뿐이므로 뺌: 전체 음량 대비 이 비율 미만
QUIET_STEM = 0.04

# 드럼 대역 (Hz): 타격 순간마다 어느 대역이 갑자기 커졌는지로 악기를 가름
KICK_BAND = (35, 130)
SNARE_BAND = (160, 350)      # 스네어 몸통
WIRE_BAND = (1500, 5000)     # 스네어 줄 소리: 몸통과 같이 커져야 스네어 (탐과 구분)
HAT_BAND = (7000, 16000)
# 그 곡의 타격들 가운데 상위 10% 의 커짐을 1로 보았을 때, 이만큼 이상 커져야 그 악기를 친 것으로 봄
HIT_LEVEL = 0.45


def missing_packages():
    """설치되지 않은 패키지의 pip 이름 목록"""
    import importlib.util
    return [pip for module, pip in PACKAGES.items() if importlib.util.find_spec(module) is None]


def decode_av(path):
    """PyAV(ffmpeg 내장)로 첫 번째 오디오 스트림 읽기 -> ((채널, 샘플) 배열, 표본율)"""
    import av
    import numpy as np
    with av.open(path) as container:
        stream = next((s for s in container.streams if s.type == 'audio'), None)
        if stream is None:
            raise RuntimeError("파일에 소리가 없습니다.")
        resampler = av.AudioResampler(format='fltp', layout='stereo', rate=stream.codec_context.sample_rate)
        chunks = []
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray())
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray())
    return np.concatenate(chunks, axis=1).astype('float32'), stream.codec_context.sample_rate


def load_audio(path, rate):
    """음원 -> (채널, 샘플) float32 배열, 표본율 rate"""
    import numpy as np
    import soundfile
    try:
        data, sr = soundfile.read(path, dtype='float32', always_2d=True)
        data = data.T
    except Exception:
        # soundfile 이 못 여는 것(m4a, 영상 파일, 확장자만 .mp3 인 MP4 등)은 PyAV 로
        data, sr = decode_av(path)
    if sr != rate:
        import librosa
        data = librosa.resample(data, orig_sr=sr, target_sr=rate)
    if data.shape[0] == 1:
        data = np.vstack([data, data])
    return data[:2]


def separate(audio, log):
    """Demucs 로 갈래 나누기 -> {갈래 이름: (채널, 샘플) 배열}"""
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model
    model = get_model(SEPARATION_MODEL)
    model.eval()
    wav = torch.from_numpy(audio)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    with torch.no_grad():
        # shifts=0: 같은 음원은 늘 같은 결과 (기본값은 무작위로 조금 밀어서 나누므로 돌릴 때마다 조금씩 다름)
        out = apply_model(model, ((wav - mean) / std)[None], device='cpu', shifts=0, split=True, overlap=0.25,
                          progress=False)[0]
    out = out * std + mean
    return {name: out[i].numpy() for i, name in enumerate(model.sources)}


def beat_times(mono, rate):
    """박의 시각(초) 목록"""
    import librosa
    import numpy as np
    _, beats = librosa.beat.beat_track(y=mono, sr=rate, units='time', trim=False)
    beats = np.asarray(beats, dtype=float)
    if len(beats) < 4:
        tempo = 120.0
        beats = np.arange(0, len(mono) / rate + 1, 60 / tempo)
    return beats


class BeatMap:
    """시각(초) <-> 박 위치. 박 사이는 직선으로 잇고, 처음 박 앞과 마지막 박 뒤는 평균 박 간격으로 늘림"""
    def __init__(self, beats):
        import numpy as np
        self.beats = beats
        gaps = np.diff(beats)
        self.period = float(np.median(gaps))

    def __call__(self, t):
        import numpy as np
        b = self.beats
        if t <= b[0]:
            return (t - b[0]) / self.period
        if t >= b[-1]:
            return len(b) - 1 + (t - b[-1]) / self.period
        k = int(np.searchsorted(b, t)) - 1
        return k + (t - b[k]) / (b[k + 1] - b[k])

    @property
    def bpm(self):
        return 60.0 / self.period


def drum_hits(stem, rate):
    """
    드럼 갈래 -> [(시각, GM 드럼 음, 세기 0~1)]
    먼저 드럼 소리 전체에서 타격 순간을 찾고(librosa 의 기본 설정), 순간마다 직전보다 얼마나 커졌는지를
    대역별로 재서 킥 / 스네어 / 하이햇을 가립니다. 한 순간에 여러 개를 같이 칠 수 있습니다.
    높은음이 크게 튀고 오래 울리면 크래시로 봅니다.
    """
    import librosa
    import numpy as np
    mono = stem.mean(0)
    hop = 256
    onsets = librosa.onset.onset_detect(y=mono, sr=rate, hop_length=hop, units='frames')
    if len(onsets) == 0:
        return []
    spec = np.log1p(np.abs(librosa.stft(mono, n_fft=2048, hop_length=hop)) * 100)
    freqs = librosa.fft_frequencies(sr=rate, n_fft=2048)
    frames = spec.shape[1]

    def band(lo, hi):
        return spec[(freqs >= lo) & (freqs < hi)].mean(0)

    def rises(curve):
        # 타격 직후(약 20ms)의 크기 - 직전(약 25ms)의 크기
        out = []
        for f in onsets:
            after = curve[f:min(frames, f + 4)].max()
            before = curve[max(0, f - 5):max(1, f - 1)].mean()
            out.append(max(0.0, after - before))
        out = np.array(out)
        top = np.percentile(out, 90)
        return out / top if top > 0 else out

    kick, snare, wire, hat = (rises(band(*b)) for b in (KICK_BAND, SNARE_BAND, WIRE_BAND, HAT_BAND))
    high = band(*HAT_BAND)
    loud = np.percentile(high, 97)
    hits = []
    for k, f in enumerate(onsets):
        t = f * hop / rate
        found = []
        if kick[k] >= HIT_LEVEL:
            found.append((36, kick[k]))
        if snare[k] >= HIT_LEVEL and wire[k] >= HIT_LEVEL * 0.8:
            found.append((38, snare[k]))
        if hat[k] >= HIT_LEVEL:
            # 크래시: 높은음이 아주 크고 0.3초 뒤에도 많이 남아 있음
            later = high[min(frames - 1, f + int(0.3 * rate / hop))]
            peak = high[f:min(frames, f + 4)].max()
            crash = peak >= loud and later - high.min() > 0.6 * (peak - high.min())
            if crash or not found or hat[k] >= 1.2 * max(v for _, v in found):
                found.append((49 if crash else 42, hat[k]))
        if not found:
            # 어느 대역도 뚜렷하지 않은 타격: 가장 많이 커진 대역으로
            best = max(((36, kick[k]), (38, snare[k]), (42, hat[k])), key=lambda nv: nv[1])
            if best[1] >= HIT_LEVEL * 0.6:
                found.append(best)
        hits += [(t, note, min(1.0, float(v))) for note, v in found]
    return hits


def pitched_notes(stem, rate, settings, work_dir):
    """선율 악기 갈래 -> [(시작 초, 끝 초, 음 번호, 세기 0~1)]"""
    import soundfile
    from basic_pitch.inference import predict
    path = os.path.join(work_dir, 'stem.wav')
    soundfile.write(path, stem.mean(0), rate)
    _, _, events = predict(path, **settings)
    return [(float(start), float(end), int(pitch), float(amp)) for start, end, pitch, amp, *_ in events]


def audio_to_midi(path, log=print):
    """음원 파일 -> 악기별 트랙이 든 MidiFile (Convert_Midi 가 파트별로 나눔)"""
    import numpy as np
    from mido import Message, MetaMessage, MidiFile, MidiTrack, bpm2tempo

    missing = missing_packages()
    if missing:
        raise RuntimeError("음원을 변환하려면 패키지를 먼저 설치해야 합니다 (2~3GB). 명령 프롬프트에서 두 줄을 차례로 실행하세요:\n"
                           f"  {INSTALL[0]}\n  {INSTALL[1]}")

    from demucs.pretrained import get_model
    rate = get_model(SEPARATION_MODEL).samplerate
    audio = load_audio(path, rate)
    seconds = audio.shape[1] / rate
    log(f"   음원 길이 {seconds:.0f}초. 악기별로 소리를 나누는 중입니다 (CPU로 곡 길이의 1~3배쯤 걸림)...")
    stems = separate(audio, log)

    mix = audio.mean(0)
    beats = beat_times(stems['drums'].mean(0) + stems['bass'].mean(0) if 'drums' in stems else mix, rate)
    when = BeatMap(beats)
    log(f"   박을 찾았습니다: 약 {when.bpm:.0f} BPM")

    def tick(t):
        return int(round(when(t) * GRID)) * TICKS // GRID

    loud = float(np.sqrt(np.mean(mix ** 2))) or 1.0
    tracks = []
    with tempfile.TemporaryDirectory() as work_dir:
        if 'drums' in stems:
            hits = drum_hits(stems['drums'], rate)
            events = [(tick(t), note, v) for t, note, v in hits]
            tracks.append(('Drums', 9, 0, [(t, t + TICKS // GRID, n, v) for t, n, v in events]))
            log(f"   드럼: 타격 {len(events)}개")
        for name, (label, channel, program, settings) in STEMS.items():
            stem = stems.get(name)
            if stem is None:
                continue
            level = float(np.sqrt(np.mean(stem.mean(0) ** 2))) / loud
            if level < QUIET_STEM:
                log(f"   {label}: 소리가 거의 없어서 건너뜁니다")
                continue
            notes = pitched_notes(stem, rate, settings, work_dir)
            placed = []
            for start, end, pitch, amp in notes:
                a = tick(start)
                b = max(a + TICKS // GRID, tick(end))
                placed.append((a, b, pitch, amp))
            tracks.append((label, channel, program, placed))
            log(f"   {label}: 음 {len(placed)}개")

    # 박 앞에서 시작하는 음이 있으면 한 마디씩 뒤로 밀어 모든 음이 0 이상에 오게 함
    first = min((n[0] for _, _, _, ns in tracks for n in ns), default=0)
    shift = (-first + TICKS * 4 - 1) // (TICKS * 4) * TICKS * 4 if first < 0 else 0

    mid = MidiFile(type=1, ticks_per_beat=TICKS)
    conductor = MidiTrack([MetaMessage('set_tempo', tempo=bpm2tempo(round(when.bpm, 2)), time=0),
                           MetaMessage('time_signature', numerator=4, denominator=4, time=0)])
    mid.tracks.append(conductor)
    for label, channel, program, notes in tracks:
        events = []
        for a, b, pitch, amp in notes:
            velocity = max(30, min(120, int(40 + amp * 80)))
            events.append((a + shift, 1, Message('note_on', channel=channel, note=pitch, velocity=velocity)))
            events.append((b + shift, 0, Message('note_off', channel=channel, note=pitch, velocity=0)))
        events.sort(key=lambda e: (e[0], e[1]))
        track = MidiTrack([MetaMessage('track_name', name=label, time=0)])
        if channel != 9:
            track.append(Message('program_change', channel=channel, program=program, time=0))
        last = 0
        for t, _, msg in events:
            track.append(msg.copy(time=t - last))
            last = t
        mid.tracks.append(track)
    return mid
