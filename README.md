# Currycaster

Broadcast automation software for the RODE RØDECaster Pro II on Linux.

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
