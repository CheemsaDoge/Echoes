import numpy as np
import pytest
from echoledger.audio import wav_bytes, read_wav, pack_audio, restore_interval, merge_intervals
from echoledger.models import VadConfig
from echoledger.vad import speech_intervals, score_intervals, EnergyDemoScorer


def test_wav_round_trip():
    x = np.arange(-500, 500, dtype=np.int16)
    assert np.array_equal(read_wav(wav_bytes(x)), x)

@pytest.mark.parametrize("bad", [b"", b"not audio", b"RIFF1234"])
def test_bad_wav(bad):
    with pytest.raises(ValueError):
        read_wav(bad)

def test_merge_clips_and_unions():
    assert merge_intervals([(-10, 3), (2, 6), (9, 20)], 12) == [(0, 6), (9, 12)]

def test_time_mapping_skips_deleted_silence_and_inserted_separator():
    x = np.zeros(160000, dtype=np.int16)
    packed, spans = pack_audio(x, [(16000, 32000), (128000, 144000)], [
        {"input_start": 0, "input_end": 160000, "original_start": 0, "chunk_id": "a", "chunk_start": 0}])
    assert len(packed) == 33920
    source = restore_interval(0, 2120, spans, 100000)
    assert [(x["start_ms"], x["end_ms"]) for x in source] == [(101000,102000),(108000,109000)]
    assert restore_interval(1000,1120,spans,100000) == []

def test_mapping_cross_chunk():
    x = np.zeros(32000, dtype=np.int16)
    _, spans = pack_audio(x, [(8000,24000)], [
        {"input_start": 0, "input_end": 16000, "original_start": 0, "chunk_id": "a", "chunk_start": 0},
        {"input_start": 16000, "input_end": 32000, "original_start": 16000, "chunk_id": "b", "chunk_start": 16000}])
    out = restore_interval(0,1000,spans,0)
    assert [s["chunk_id"] for s in out] == ["a","b"]
    assert out[1]["source_start_sample"] == 0

def test_silence_produces_no_interval():
    assert speech_intervals(np.zeros(100),51200,VadConfig()) == []

def test_uncertain_short_response_kept():
    probs = np.zeros(100)
    probs[50] = .25
    assert speech_intervals(probs,51200,VadConfig())
    assert speech_intervals(probs,51200,VadConfig(retain_uncertain=False)) == []

def test_padding_bounded():
    assert speech_intervals(np.ones(2),1000,VadConfig()) == [(0,1000)]

def test_hysteresis_and_short_silence():
    probs = np.array([.9]*10+[0]*5+[.9]*10+[0]*40)
    result = speech_intervals(probs,len(probs)*512,VadConfig(retain_uncertain=False,pad_ms=0))
    assert result == [(0,25*512)]

def test_metrics_use_duration_not_number_of_segments():
    result = score_intervals([(0,80)],[(0,100)],200)
    assert result["speech_recall"] == .8
    assert result["retained_precision"] == 1
    assert result["removed_fraction"] == .6

@pytest.mark.parametrize("values", [[float("nan")],[-.1],[1.1]])
def test_invalid_probabilities(values):
    with pytest.raises(ValueError):
        speech_intervals(np.array(values),512,VadConfig())

@pytest.mark.parametrize("values", [dict(threshold=.2,negative_threshold=.3),dict(pad_ms=9999),dict(uncertainty_threshold=.8)])
def test_invalid_profile(values):
    with pytest.raises(ValueError):
        VadConfig(**values)
