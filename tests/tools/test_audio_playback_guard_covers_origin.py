"""The autouse audio-playback guard must cover the ORIGIN playback symbols.

Incident (2026-09-10): ``pytest tests/gateway/`` spoke "Hello world" out of the
developer's speakers, twice. The guard added after the earlier
"partial answer complete" incident patched only the ``hermes_cli.voice``
re-exports, so this route walked straight past it:

    tests/gateway/test_voice_command.py::TestStreamTtsToSpeaker
      -> tools.tts_tool.stream_tts_to_speaker      (real, unmocked)
      -> tools/tts_tool.py: from tools.voice_mode import play_audio_file
      -> tools.voice_mode.play_audio_file -> subprocess.Popen(["afplay", ...])

The late import resolves the attribute off ``tools.voice_mode`` at call time,
so patching ``hermes_cli.voice.play_audio_file`` has no effect on it.

These tests pin the guard to the origin module. They are deliberately NOT
marked ``real_audio_playback`` — they assert the guard is active on an
ordinary test, which is the whole contract.
"""

import subprocess

import pytest


def test_voice_mode_play_audio_file_is_stubbed():
    """The origin definition, not just the hermes_cli.voice re-export."""
    import tools.voice_mode as vm

    assert vm.play_audio_file("/nonexistent/should-never-play.wav") is False


def test_late_import_off_voice_mode_gets_the_stub():
    """A fresh ``from tools.voice_mode import play_audio_file`` sees the stub.

    This is the exact import form ``tools/tts_tool.py`` uses inside the
    streaming-TTS drain loop.
    """
    from tools.voice_mode import play_audio_file

    assert play_audio_file("/nonexistent/should-never-play.wav") is False


def test_hermes_cli_voice_reexport_still_stubbed():
    """The original guard's coverage must not regress."""
    voice = pytest.importorskip("hermes_cli.voice")

    if hasattr(voice, "play_audio_file"):
        assert voice.play_audio_file("/nonexistent/should-never-play.wav") is False
    if hasattr(voice, "speak_text"):
        assert voice.speak_text("should never be spoken") is None


def test_streaming_tts_opens_no_player(monkeypatch):
    """End-to-end reproduction of the incident, asserted silent.

    Drives the real ``stream_tts_to_speaker`` with the same fixture string the
    offending gateway test used, and fails if any audio player subprocess is
    spawned. Before the origin-symbol patch this spawned ``afplay``.
    """
    import queue
    import threading

    spawned = []
    real_popen = subprocess.Popen

    def _recording_popen(cmd, *args, **kwargs):
        argv0 = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else str(cmd)
        spawned.append(argv0)
        return real_popen(  # pragma: no cover - guard should prevent this
            [__import__("sys").executable, "-c", ""], *args, **kwargs
        )

    monkeypatch.setattr(subprocess, "Popen", _recording_popen)

    from tools.tts_tool import stream_tts_to_speaker

    text_q: queue.Queue = queue.Queue()
    text_q.put("Hello world.")
    text_q.put(None)

    stream_tts_to_speaker(text_q, threading.Event(), threading.Event())

    players = {"afplay", "ffplay", "aplay"}
    assert not (players & set(spawned)), (
        f"streaming TTS opened an audio player: {spawned}. The audio-playback "
        "guard must stub tools.voice_mode.play_audio_file, not only the "
        "hermes_cli.voice re-export."
    )
