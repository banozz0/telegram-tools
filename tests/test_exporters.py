import csv
import json

from telegram_tools.exporters import write_records


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
