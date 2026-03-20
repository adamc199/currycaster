# Currycaster

Podcast playout software developed and used by Adam Curry on The No Agenda Show and many other podcasts that record live and in real-time.

Currently tested with the RODE RØDECaster Pro II using the [RODECaster Pro II PipeWire configuration](https://github.com/adamc199/rodecaster-pro2-pipewire).

## Features

- 8 audio players with cue points and waveform display
- Cart wall with 72 (4 tabs x 18) assignable sound buttons
- MIDI controller support (Akai APC, Novation Launchpad, etc.)
- Audio routing via PipeWire/PulseAudio
- Drag-and-drop file loading

## Requirements

- Python 3.10+
- PyQt6
- GStreamer with Python bindings
- pulsectl (PulseAudio control)
- mido (MIDI support)
- PipeWire or PulseAudio

## Installation

```bash
pip install PyQt6 pulsectl mido
```

## Running

```bash
python currycaster.py
```

## Configuration

On first run, config files are created in `~/.config/currycaster/`:
- `audio_config.json` - Audio device routing
- `midi_config.json` - MIDI mappings
- `library_index.json` - File library cache
- `cart_config.json` - Cart wall settings

## License

MIT License - See LICENSE file
