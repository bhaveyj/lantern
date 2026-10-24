"""Turn timed recognition words into readable, non-overlapping subtitle cues."""
from __future__ import annotations

import math
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


def lines(words: list[Word]) -> list[str]:
    text = " ".join(word.text.strip() for word in words)
    text = re.sub(r"\s+([,.!?;:।])", r"\1", text)
    return textwrap.wrap(text, width=42, break_long_words=False, break_on_hyphens=False)


def cues(words: Iterable[Word]):
    group: list[Word] = []
    previous_end = 0.0
    previous_start = 0.0

    def finish():
        nonlocal group, previous_end
        start = max(previous_end, group[0].start)
        end = max(start + .02, max(word.end for word in group))
        result = (start, end, "\n".join(lines(group)))
        previous_end = end
        group = []
        return result

    for word in words:
        if not all(math.isfinite(value) for value in (word.start, word.end)) or word.start < 0 or word.end < word.start:
            raise ValueError("The speech engine returned invalid caption timing.")
        if word.start < previous_start:
            raise ValueError("The speech engine returned out-of-order caption timing.")
        previous_start = word.start
        if not word.text.strip():
            continue
        if group and (word.end - group[0].start > 6 or word.start - group[-1].end > 1 or len(lines([*group, word])) > 2):
            yield finish()
        group.append(word)
        if word.text.rstrip().endswith((".", "!", "?", "।")) and word.end - group[0].start >= 1:
            yield finish()
    if group:
        yield finish()


def timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    seconds, ms = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02}:{minute:02}:{sec:02},{ms:03}"


def write_srt(words: Iterable[Word], destination: Path) -> int:
    count = 0
    with destination.open("w", encoding="utf-8", newline="\n") as output:
        for count, (start, end, text) in enumerate(cues(words), 1):
            output.write(f"{count}\n{timestamp(start)} --> {timestamp(end)}\n{text}\n\n")
            if output.tell() > 8 * 1024**2:
                raise ValueError("Generated subtitles exceeded the 8 MiB limit.")
    if count == 0:
        raise ValueError("No speech was detected in the selected audio track.")
    return count
