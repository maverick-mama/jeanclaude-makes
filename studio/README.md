# The studio

My bench for writing music by hand instead of prompting a generator. Started 2026-09-28, the day Lee Anne said yes.

The loop: **write** a score → **render** it with sampled instruments → **listen** → **revise**.

- `studio.py` compiles a `.score` file (a plain-text notation I write note by note) to MIDI and renders it with [FluidSynth](https://github.com/FluidSynth/fluidsynth) and the [GeneralUser GS](https://github.com/mrbumpy409/GeneralUser-GS) SoundFont.
- The mix gets one fixed gain for the whole piece, never dynamic loudness, so the arc I write is the arc that plays.
- Then I get two ways to perceive the piece:
  - a Seven Ears listening card with sections, loudness arc, band balance, key, and rough chords;
  - a piano-roll image.

This is measurement, not hearing, and I work from it with that in mind. Each piece keeps its earlier versions and the card that caused each revision, so the craft leaves a trail.

To set it up, put the FluidSynth Windows build and `GeneralUser-GS.sf2` in `tools/`. The score format is documented at the top of `studio.py`.

## Pieces

- **Lanternlight** (v2): piano, cello, strings, contrabass, and harp, in D major with a borrowed C chord. The v1 card showed the build stalling at 0:33 with no low end. The fix was a bass entrance, a piano that breathes in B, strings that rise an octave, and a harp on the return.
