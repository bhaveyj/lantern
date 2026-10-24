import math

import pytest

from lantern.captions import Word, cues, timestamp, write_srt


def test_captions_preserve_unicode_and_original_timing_across_silence(tmp_path):
    words = [Word(.1, .4, "Hello,"), Word(.5, 1.4, "world."), Word(30.0, 30.6, "नमस्ते"), Word(30.7, 31.5, "दुनिया।")]
    output = tmp_path / "captions.srt"
    assert write_srt(iter(words), output) == 2
    text = output.read_text(encoding="utf-8")
    assert "00:00:00,100 --> 00:00:01,400" in text
    assert "00:00:30,000 --> 00:00:31,500" in text
    assert "Hello, world." in text and "नमस्ते दुनिया।" in text


def test_caption_wrapping_bounds_duration_and_avoids_overlap():
    words = [Word(i * .4, i * .4 + .5, "readable") for i in range(60)]
    result = list(cues(words))
    assert len(result) > 3
    previous = 0
    for start, end, text in result:
        assert start >= previous and end > start
        assert end - start <= 6.1
        assert len(text.splitlines()) <= 2
        assert all(len(line) <= 42 for line in text.splitlines())
        previous = end
    assert timestamp(3599.9996) == "01:00:00,000"


@pytest.mark.parametrize("word", [Word(-1, 0, "bad"), Word(1, 0, "bad"), Word(math.nan, 1, "bad")])
def test_invalid_engine_timing_is_rejected(word):
    with pytest.raises(ValueError, match="invalid caption timing"):
        list(cues([word]))


def test_no_speech_is_reported_instead_of_attaching_empty_captions(tmp_path):
    with pytest.raises(ValueError, match="No speech"):
        write_srt([], tmp_path / "empty.srt")
