# Currycaster

Podcast playout software developed and used by Adam Curry on The No Agenda Show and many other podcasts that record live and in real-time.

Currently tested with the RODE RØDECaster Pro II using the [RODECaster Pro II PipeWire configuration](https://github.com/adamc199/rodecaster-pro2-pipewire).

## Features

### Audio Players (8 Channels)
- **8 independent audio players** arranged horizontally
- **Waveform display** with zoom and pan controls
- **Cue points** - set in/out points by right-clicking on waveform
- **Cue point auto-seek** - player automatically jumps to cue-in point when playback reaches cue-out
- **Time display** - toggle between elapsed time and remaining time (click time display)
- **Volume control** - vertical fader with cubic gain curve
- **PFL (Pre-Fade Listen)** - route audio to cue bus without affecting program output (click player label)
- **Auto-load** - drag audio files from library directly onto players
- **Supported formats**: MP3, WAV, OGG, FLAC, M4A, AAC, WMA

### Cart Wall (72 Sound Buttons)
- **4 tabs** with 18 carts each: Openers, Donations, General, Misc
- **Individual volume per cart** - vertical slider on right side
- **Color-coded buttons** - right-click to set custom colors
- **Named carts** - right-click to rename
- **Auto-play** - carts auto-play on click
- **Drag-and-drop** - drop audio files onto carts
- **Save/Load** - save cart configurations to files
- **Countdown timer** - shows remaining time of active cart

### Audio Routing
- **Dual output buses**: Program Out and Cue Out
- **Real-time routing** - switch any player between buses via PFL
- **Automatic stream routing** via PulseAudio/PipeWire
- **Per-player routing** - each player can be independently routed
- **Instant switching** - PFL toggle routes immediately without audio glitches

### MIDI Controller Support
- **Auto-detects** MIDI controllers (Akai APC, Novation Launchpad, etc.)
- **MIDI Learn** - right-click any button or slider to learn MIDI mapping
- **CC and Note** support - map knobs, faders, buttons, and pads
- **Persistent mappings** - saved to `midi_config.json`

### File Library
- **Background indexing** - scans media folders without blocking UI
- **Folder browser** - tree view of media directory
- **Search** - real-time search with debouncing (Ctrl+F)
- **Focus mode** - limit view to specific folder
- **Font customization** - adjust font size for readability
- **Drag-to-load** - drag files from library to players or carts
- **Auto-reindex** - detects new files automatically

### Waveform Display
- **Zoom** - mouse wheel to zoom in/out
- **Pan** - right-click drag to pan view
- **Cue markers** - green (in) and red (out) markers
- **Playhead** - white line shows current position
- **Click-to-seek** - left-click to seek to position
- **Visual feedback** - dimmed areas outside cue points

### Window Management
- **Remembers window positions** - saves layout on close
- **Multi-window** - players, cart wall, and library as separate windows
- **Dark theme** - professional broadcast aesthetic

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
- `audio_config.json` - Audio device routing (Program/Cue sinks)
- `midi_config.json` - MIDI controller mappings
- `library_index.json` - File library cache
- `cart_config.json` - Cart wall settings
- `window_layout.json` - Window positions and sizes
- `explorer_config.json` - Library folder and font settings

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+F | Focus search field in Library |

## Mouse Controls

### Players
| Action | Control |
|--------|---------|
| Load file | Click LOAD or drag file |
| Play/Pause | Click PLAY/PAUSE button |
| Stop | Click STOP (returns to cue-in) |
| Dump track | Click DUMP (clears player) |
| Toggle PFL | Click player name label |
| Toggle time mode | Click time display |
| Seek | Left-click on waveform |
| Set cue in | Right-click → Set Start (Cue In) |
| Set cue out | Right-click → Set End (Cue Out) |
| Reset cue | Right-click → Reset Clip |
| Change volume | Drag vertical fader |
| MIDI learn | Right-click any control |

### Carts
| Action | Control |
|--------|---------|
| Play/Stop | Click cart button |
| Change volume | Drag vertical fader |
| Rename | Right-click → Rename Cart |
| Change color | Right-click → Set Color |
| Clear cart | Right-click → Clear Cart |

### Library
| Action | Control |
|--------|---------|
| Search | Type in search box (Ctrl+F) |
| Load to player | Click file (auto-loads to first empty player) |
| Refresh index | Click Re-Index button |
| Change folder | Click ... button |
| Adjust font | Click Aa button |

## Hardware Compatibility

Currycaster is designed for professional broadcast use with equipment like:

- RODE RØDECaster Pro II (see PipeWire config)
- Akai APC series controllers
- Novation Launchpad controllers
- Any MIDI controller with CC or Note support

## License

MIT License - See LICENSE file
