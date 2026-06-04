from src.ingestion.text import read_text


def test_json_text_ingestion_accepts_utf8_bom(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('\ufeff{"bench_root": "C:/tmp", "tasks": []}', encoding="utf-8")

    text, structured = read_text(path)

    assert structured["json_data"]["tasks"] == []
    assert '"bench_root": "C:/tmp"' in text
