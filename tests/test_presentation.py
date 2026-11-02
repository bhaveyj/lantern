import pytest

from lantern.presentation import describe_media


LONG_NAME = "Spider-Man.Brand.New.Day.2026.IMAX.2160p.CORE.WEB-DL.DDP5.1.Atmos.HDR.H.265-NotSoNewDayForSony.mkv"


def test_release_title_keeps_original_filename_and_distinguishing_details():
    display = describe_media("C:/Media/" + LONG_NAME)
    assert display.title == "Spider-Man Brand New Day"
    assert display.filename == LONG_NAME
    assert display.context == "2026 · IMAX"
    assert display.format == "MKV"
    assert display.details == ("2160p", "HDR", "Atmos", "DDP5.1")


@pytest.mark.parametrize("name", [
    "2001.A.Space.Odyssey.mkv",
    "Our.family.holiday.2026.mp4",
    "Archive.2160p.WEB-DL.mkv",
    "Track.01.flac",
    "A & B.mp4",
    "नमस्ते दुनिया.mp4",
])
def test_uncertain_titles_are_preserved(name):
    assert describe_media(name).title == name.rsplit(".", 1)[0]


def test_numeric_movie_title_and_episode_number_are_not_stripped():
    assert describe_media("2001.A.Space.Odyssey.1968.2160p.BluRay.mkv").title == "2001 A Space Odyssey"
    assert describe_media("The.Show.S01E02.1080p.WEB-DL.mkv").title == "The Show S01E02"


def test_custom_library_titles_are_preserved():
    display = describe_media(LONG_NAME, "My evening movie")
    assert display.title == "My evening movie"
    assert display.filename == LONG_NAME


def test_dynamic_range_and_resolution_distinguish_the_same_movie():
    hdr = describe_media(LONG_NAME)
    sdr = describe_media(LONG_NAME.replace("2160p", "1080p").replace("HDR", "SDR"))
    assert hdr.title == sdr.title
    assert hdr.summary != sdr.summary
