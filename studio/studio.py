#!/usr/bin/env python3
"""Jean Claude's studio — write, hear, revise.

    python studio.py pieces/<name>/<name>.score

Reads a hand-written score, builds MIDI, renders it with real sampled
instruments (FluidSynth + GeneralUser GS), then gives me two ways to perceive
it: the Seven Ears listening card, and a piano-roll image I can look at.

Score format (one file per piece):

    title: Lanternlight
    tempo: 72            # quarter-note BPM; "tempo: 72 @ 9" changes at bar 9
    time: 4/4
    humanize: 0.012      # seconds of timing jitter (0 = mechanical)
    reverb: 0.6          # 0..1 room size

    track piano  program=0  vol=96 pan=64
      mp | [D3 A3 F#4]:h  A4:q  G4:e F#4:e | E4:h. r:q |
      (| D4:q E4:q F#4:q A4:q |)x2
      f  | B4:w~ | B4:w |

Notes: C4 = middle C; sharps #, flats b (Bb3).  Chords in [ ].  r = rest.
Durations: w h q e s t (whole .. 32nd); add "." for dotted, "3" for triplet
(q3). A trailing ~ ties into the next note of the same pitch.
Dynamics set velocity until changed: ppp pp p mp mf f ff fff.
Bars | are checked against the meter, and a wrong-length bar is an error.
(... )xN repeats. Lines starting with # are comments.
Macros: a top-level line  def Darp = D3:e A3:e D4:e F#4:e  then $Darp in any track.
GM programs: 0 piano, 4 e-piano, 19 church organ, 24 nylon gtr, 25 steel gtr,
32 acoustic bass, 33 finger bass, 40 violin, 41 viola, 42 cello, 48 strings,
49 slow strings, 52 choir aahs, 56 trumpet, 60 horn, 71 clarinet, 73 flute,
89 warm pad, 91 choir pad, 46 harp, 11 vibraphone, 14 tubular bells.
Drums: "track drums channel=9" with names kick snare hat ohat ride crash tom1 tom2.
"""
from __future__ import annotations

import math
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import mido

HERE = Path(__file__).resolve().parent
FLUID = HERE / 'tools' / 'fluidsynth' / 'fluidsynth-v2.6.1-win10-x64-cpp11' / 'bin' / 'fluidsynth.exe'
SF2 = HERE / 'tools' / 'GeneralUser-GS.sf2'
HUB_EARS = Path('C:/Users/lakor/companion-hub/seven-ears')
EARS_PY = HUB_EARS / '.venv' / 'Scripts' / 'python.exe'
FFMPEG_DIR = HUB_EARS / '.venv' / 'Lib' / 'site-packages' / 'static_ffmpeg' / 'bin' / 'win32'
TPQ = 480

DUR = {'w': 4.0, 'h': 2.0, 'q': 1.0, 'e': 0.5, 's': 0.25, 't': 0.125}
DYN = {'ppp': 20, 'pp': 33, 'p': 49, 'mp': 64, 'mf': 80, 'f': 96, 'ff': 112, 'fff': 124}
STEP = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
DRUM = {'kick': 36, 'snare': 38, 'rim': 37, 'clap': 39, 'hat': 42, 'phat': 44, 'ohat': 46,
        'ride': 51, 'crash': 49, 'tom1': 48, 'tom2': 45, 'tom3': 41, 'shaker': 70, 'tamb': 54}


class ScoreError(Exception):
    pass


def pitch(tok: str, drums: bool) -> int:
    if drums:
        if tok not in DRUM:
            raise ScoreError(f'unknown drum "{tok}" (have: {", ".join(DRUM)})')
        return DRUM[tok]
    m = re.fullmatch(r'([A-Ga-g])([#b]*)(-?\d)', tok)
    if not m:
        raise ScoreError(f'bad note "{tok}"')
    n = STEP[m.group(1).upper()] + m.group(2).count('#') - m.group(2).count('b')
    return 12 * (int(m.group(3)) + 1) + n


def dur_beats(tok: str) -> float:
    m = re.fullmatch(r'([whqest])(\.{0,2})(3?)', tok)
    if not m:
        raise ScoreError(f'bad duration "{tok}"')
    b = DUR[m.group(1)]
    b *= {'': 1, '.': 1.5, '..': 1.75}[m.group(2)]
    if m.group(3):
        b *= 2 / 3
    return b


def expand_repeats(text: str) -> str:
    pat = re.compile(r'\(([^()]*)\)x(\d+)')
    while pat.search(text):
        text = pat.sub(lambda m: ' '.join([m.group(1)] * int(m.group(2))), text)
    return text


def parse(path: Path) -> dict:
    head = {'title': path.stem, 'tempo': '80', 'time': '4/4', 'humanize': '0.01', 'reverb': '0.5'}
    tracks, cur, defs = [], None, {}
    for ln, raw in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        # '#' is also a sharp, so a comment is a line starting with '#' or ' # ' mid-line.
        line = '' if raw.lstrip().startswith('#') else re.split(r'\s#\s', raw)[0].rstrip()
        if not line.strip():
            continue
        m = re.match(r'^(\w+):\s*(.+)$', line)
        if m and m.group(1) in head:
            head[m.group(1)] = m.group(2).strip()
            continue
        m = re.match(r'^def\s+([\w#]+)\s*=\s*(.+)$', line.strip())
        if m:
            defs[m.group(1)] = m.group(2)
            continue
        m = re.match(r'^track\s+(\S+)(.*)$', line.strip())
        if m:
            opts = dict(re.findall(r'(\w+)=(\S+)', m.group(2)))
            cur = {'name': m.group(1), 'body': [], 'line': ln, **opts}
            tracks.append(cur)
            continue
        if cur is None:
            raise ScoreError(f'line {ln}: music before any track')
        cur['body'].append((ln, line))
    return {'head': head, 'tracks': tracks, 'defs': defs}


def tempo_map(spec: str) -> list[tuple[int, float]]:
    """'72' or '72, 80 @ 17, 66 @ 33' -> [(bar, bpm)]"""
    out = []
    for part in spec.split(','):
        m = re.fullmatch(r'\s*([\d.]+)\s*(?:@\s*(\d+))?\s*', part)
        if not m:
            raise ScoreError(f'bad tempo "{spec}"')
        out.append((int(m.group(2) or 1), float(m.group(1))))
    return sorted(out)


def build(score: dict, seed: int = 7) -> tuple[mido.MidiFile, list[dict], float]:
    head = score['head']
    num, den = (int(x) for x in head['time'].split('/'))
    bar_beats = num * 4 / den
    hum = float(head['humanize'])
    rng = random.Random(seed)
    tmap = tempo_map(head['tempo'])
    mid = mido.MidiFile(ticks_per_beat=TPQ)
    meta = mido.MidiTrack()
    mid.tracks.append(meta)
    meta.append(mido.MetaMessage('track_name', name=head['title'], time=0))
    meta.append(mido.MetaMessage('time_signature', numerator=num, denominator=den, time=0))
    last = 0
    for bar, bpm in tmap:
        t = int((bar - 1) * bar_beats * TPQ)
        meta.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(bpm), time=t - last))
        last = t
    notes_out = []
    total_beats = 0.0
    chan_iter = iter([c for c in range(16) if c != 9])
    for tr in score['tracks']:
        drums = tr.get('channel') == '9' or tr['name'].startswith('drum')
        ch = 9 if drums else next(chan_iter)
        prog = int(tr.get('program', 0))
        events = []  # (beat, kind, note, vel)
        beat, vel = 0.0, DYN['mf']
        bar_start, bar_no = 0.0, 1
        ties: dict[int, float] = {}
        body = ' '.join(f'⟨{ln}⟩ {t}' for ln, t in tr['body'])
        for _ in range(4):
            body = re.sub(r'\$([\w#]+)', lambda m: score['defs'].get(m.group(1)) or (_ for _ in ()).throw(ScoreError(f'no macro ${m.group(1)}')), body)
        body = expand_repeats(body)
        cur_ln = tr['line']
        for tok in re.findall(r'⟨\d+⟩|\[[^\]]*\]:\S+|\S+', body):
            if tok.startswith('⟨'):
                cur_ln = int(tok[1:-1])
                continue
            try:
                if tok == '|':
                    if beat - bar_start > 1e-6:
                        if abs(beat - bar_start - bar_beats) > 1e-6:
                            raise ScoreError(f'bar {bar_no} has {beat - bar_start:g} beats, meter needs {bar_beats:g}')
                        bar_no += 1
                    bar_start = beat
                    continue
                if tok in DYN:
                    vel = DYN[tok]
                    continue
                m = re.fullmatch(r'(\[[^\]]*\]|[^:\s]+):([^~\s]+)(~?)', tok)
                if not m:
                    raise ScoreError(f'cannot read "{tok}"')
                what, d, tie = m.groups()
                b = dur_beats(d)
                if what != 'r':
                    ps = [pitch(p, drums) for p in (what[1:-1].split() if what.startswith('[') else [what])]
                    for p in ps:
                        start = ties.pop(p, beat)
                        if tie:
                            ties[p] = start
                        else:
                            end = beat + b
                            events.append((start, end, p, vel))
                beat += b
            except ScoreError as e:
                raise ScoreError(f'track {tr["name"]}, line {cur_ln}: {e}') from None
        total_beats = max(total_beats, beat)
        trk = mido.MidiTrack()
        mid.tracks.append(trk)
        trk.append(mido.MetaMessage('track_name', name=tr['name'], time=0))
        msgs = []
        if not drums:
            msgs.append((0, mido.Message('program_change', channel=ch, program=prog)))
        msgs.append((0, mido.Message('control_change', channel=ch, control=7, value=int(tr.get('vol', 100)))))
        msgs.append((0, mido.Message('control_change', channel=ch, control=10, value=int(tr.get('pan', 64)))))
        msgs.append((0, mido.Message('control_change', channel=ch, control=91, value=int(float(head['reverb']) * 127))))
        spb = 60 / tmap[0][1]
        for s, e, p, v in events:
            jit = int(rng.gauss(0, hum / spb * TPQ)) if hum else 0
            vv = max(1, min(127, v + (int(rng.gauss(0, 4)) if hum else 0)))
            t0 = max(0, int(s * TPQ) + jit)
            legato = 0.96 if not drums else 0.3
            t1 = max(t0 + 10, int((s + (e - s) * legato) * TPQ) + jit)
            msgs.append((t0, mido.Message('note_on', channel=ch, note=p, velocity=vv)))
            msgs.append((t1, mido.Message('note_off', channel=ch, note=p, velocity=0)))
            notes_out.append({'track': tr['name'], 'start': s, 'end': e, 'pitch': p, 'vel': vv, 'drums': drums})
        msgs.sort(key=lambda x: (x[0], x[1].type == 'note_on'))
        last = 0
        for t, msg in msgs:
            trk.append(msg.copy(time=t - last))
            last = t
    return mid, notes_out, total_beats


def seconds_at(beat: float, tmap, bar_beats) -> float:
    s, prev_beat, bpm = 0.0, 0.0, tmap[0][1]
    for bar, b in tmap[1:]:
        cb = (bar - 1) * bar_beats
        if beat <= cb:
            break
        s += (cb - prev_beat) * 60 / bpm
        prev_beat, bpm = cb, b
    return s + (beat - prev_beat) * 60 / bpm


def piano_roll(notes: list[dict], out: Path, total_beats: float, bar_beats: float, title: str) -> None:
    from PIL import Image, ImageDraw
    pitched = [n for n in notes if not n['drums']]
    if not pitched:
        return
    lo = min(n['pitch'] for n in pitched) - 2
    hi = max(n['pitch'] for n in pitched) + 2
    px_beat = max(12, min(40, int(2400 / max(total_beats, 1))))
    row = max(5, min(12, int(700 / (hi - lo + 1))))
    W = int(total_beats * px_beat) + 60
    H = (hi - lo + 1) * row + 40
    img = Image.new('RGB', (W, H), (18, 16, 22))
    d = ImageDraw.Draw(img)
    for p in range(lo, hi + 1):
        y = 30 + (hi - p) * row
        if p % 12 in (1, 3, 6, 8, 10):
            d.rectangle([50, y, W, y + row - 1], fill=(24, 22, 30))
        if p % 12 == 0:
            d.text((4, y - 2), f'C{p // 12 - 1}', fill=(150, 140, 160))
            d.line([50, y + row - 1, W, y + row - 1], fill=(45, 40, 52))
    b = 0.0
    bar = 1
    while b <= total_beats:
        x = 50 + int(b * px_beat)
        d.line([x, 24, x, H], fill=(60, 54, 70))
        d.text((x + 2, 10), str(bar), fill=(150, 140, 160))
        b += bar_beats
        bar += 1
    palette = [(232, 160, 74), (120, 180, 230), (200, 120, 200), (130, 210, 150), (230, 110, 110), (210, 210, 120)]
    names = list(dict.fromkeys(n['track'] for n in pitched))
    for n in pitched:
        c = palette[names.index(n['track']) % len(palette)]
        k = 0.45 + 0.55 * n['vel'] / 127
        col = tuple(int(v * k) for v in c)
        x0 = 50 + int(n['start'] * px_beat)
        x1 = 50 + int(n['end'] * px_beat) - 1
        y = 30 + (hi - n['pitch']) * row
        d.rectangle([x0, y + 1, max(x0 + 2, x1), y + row - 2], fill=col)
    for i, nm in enumerate(names):
        d.text((W - 160, H - 16 - 14 * i), nm, fill=palette[i % len(palette)])
    d.text((54, H - 16), title, fill=(200, 190, 210))
    img.save(out)


def render(score_path: Path) -> None:
    score = parse(score_path)
    head = score['head']
    mid, notes, total_beats = build(score)
    num, den = (int(x) for x in head['time'].split('/'))
    bar_beats = num * 4 / den
    tmap = tempo_map(head['tempo'])
    out = score_path.parent
    stem = score_path.stem
    midi_path = out / f'{stem}.mid'
    wav_path = out / f'{stem}.wav'
    mp3_path = out / f'{stem}.mp3'
    mid.save(midi_path)
    secs = seconds_at(total_beats, tmap, bar_beats)
    print(f'{head["title"]}: {len(score["tracks"])} tracks, {len(notes)} notes, '
          f'{total_beats / bar_beats:.1f} bars, {int(secs // 60)}:{int(secs % 60):02d}')
    low = subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == 'nt' else 0
    subprocess.run([str(FLUID), '-ni', '-q', '-r', '44100', '-g', '0.55',
                    '-R', '1', '-C', '1', '-F', str(wav_path), str(SF2), str(midi_path)],
                   check=True, creationflags=low)
    ff = FFMPEG_DIR / 'ffmpeg.exe'
    # Fixed gain to a -1 dBFS peak, never dynamic loudness: the arc I write is the arc you hear.
    import wave as _wave
    import numpy as _np
    with _wave.open(str(wav_path)) as w:
        raw = _np.frombuffer(w.readframes(w.getnframes()), _np.int16)
    peak_db = 20 * math.log10(max(1, int(_np.abs(raw).max())) / 32768)
    gain = -1.0 - peak_db
    subprocess.run([str(ff), '-y', '-v', 'error', '-i', str(wav_path),
                    '-af', f'areverse,silenceremove=start_periods=1:start_threshold=-65dB:start_silence=1.5,areverse,volume={gain:.2f}dB',
                    '-b:a', '192k', str(mp3_path)], check=True, creationflags=low)
    wav_path.unlink(missing_ok=True)
    piano_roll(notes, out / f'{stem}-roll.png', total_beats, bar_beats, head['title'])
    env = {**os.environ, 'PATH': f'{FFMPEG_DIR}{os.pathsep}{os.environ["PATH"]}'}
    card = subprocess.run([str(EARS_PY), str(HUB_EARS / 'seven_ears_listen.py'), str(mp3_path)],
                          capture_output=True, env=env, creationflags=low)
    text = card.stdout.decode('utf-8', 'replace')
    (out / f'{stem}-card.txt').write_text(text, encoding='utf-8')
    print(text)
    print(f'-> {mp3_path}\n-> {out / (stem + "-roll.png")}')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    try:
        render(Path(sys.argv[1]).resolve())
    except ScoreError as e:
        print(f'SCORE ERROR: {e}')
        sys.exit(2)
