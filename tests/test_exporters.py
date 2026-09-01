import csv
import json

from telegram_tools.exporters import json_text, write_records


def test_write_records_as_json(tmp_path):
    output = tmp_path / "messages.json"
    records = [{"id": 1, "text": "hello"}]

    write_records(records, output, "json")

    assert json.loads(output.read_text()) == records


def test_write_records_as_csv(tmp_path):
    output = tmp_path / "messages.csv"
    records = [{"id": 1, "text": "hello"}, {"id": 2, "text": "bye"}]

    write_records(records, output, "csv")

    with output.open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows == [{"id": "1", "text": "hello"}, {"id": "2", "text": "bye"}]


# -- non-ASCII titles -----------------------------------------------------

EMOJI_ROWS = [{"id": 80, "title": "\U0001f4bb Dobby", "chat_id": -1004297050934}]


def test_json_text_writes_the_emoji_not_its_escape():
    """A title the pickers draw as 💻 Dobby must not print as \\ud83d\\udcbb Dobby."""
    text = json_text(EMOJI_ROWS[0])
    assert "\U0001f4bb Dobby" in text
    assert "\\ud83d" not in text


def test_json_export_round_trips_an_emoji_title(tmp_path):
    path = tmp_path / "topics.json"
    write_records(EMOJI_ROWS, path, "json")
    assert json.loads(path.read_text(encoding="utf-8")) == EMOJI_ROWS


def test_csv_export_round_trips_an_emoji_title(tmp_path):
    path = tmp_path / "topics.csv"
    write_records(EMOJI_ROWS, path, "csv")
    assert "\U0001f4bb Dobby" in path.read_text(encoding="utf-8")


def test_both_formats_are_written_as_utf8_whatever_the_locale(tmp_path):
    """Explicit encoding, not the locale's: the old ASCII output could not fail."""
    for name, fmt in (("a.json", "json"), ("a.csv", "csv")):
        path = tmp_path / name
        write_records(EMOJI_ROWS, path, fmt)
        assert "\U0001f4bb Dobby" in path.read_bytes().decode("utf-8")
