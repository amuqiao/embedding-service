import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from scripts.verify import image_inspect

ROOT_DIR = Path(__file__).resolve().parents[1]


def test_image_inspect_reports_transparent_background(tmp_path):
    image_path = tmp_path / "title.png"
    image = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    image.putpixel((1, 1), (255, 0, 0, 255))
    image.save(image_path)

    result = image_inspect.inspect_source(str(image_path))

    assert result["format"] == "PNG"
    assert result["mime"] == "image/png"
    assert result["width"] == 4
    assert result["height"] == 4
    assert result["alpha"]["has_alpha_channel"] is True
    assert result["alpha"]["has_transparency"] is True
    assert result["alpha"]["transparent_background"] is True
    assert result["alpha"]["fully_opaque"] is False


def test_image_inspect_reports_opaque_image(tmp_path):
    image_path = tmp_path / "opaque.jpg"
    Image.new("RGB", (3, 2), (255, 255, 255)).save(image_path)

    result = image_inspect.inspect_source(str(image_path))

    assert result["format"] == "JPEG"
    assert result["mime"] == "image/jpeg"
    assert result["alpha"]["has_alpha_channel"] is False
    assert result["alpha"]["has_transparency"] is False
    assert result["alpha"]["fully_opaque"] is True


def test_image_inspect_requires_transparent_background(tmp_path):
    image_path = tmp_path / "opaque.png"
    Image.new("RGBA", (2, 2), (0, 0, 0, 255)).save(image_path)

    with pytest.raises(RuntimeError, match="background corners"):
        image_inspect.main(["--require-transparent-background", str(image_path)])


def test_image_inspect_cli_outputs_json(tmp_path, capsys):
    image_path = tmp_path / "transparent.png"
    Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(image_path)

    assert image_inspect.main(["--json", str(image_path)]) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["source"] == str(image_path)
    assert payload["alpha"]["fully_transparent"] is True


def test_image_inspect_human_output_is_bilingual(tmp_path, capsys):
    image_path = tmp_path / "transparent.png"
    Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(image_path)

    assert image_inspect.main([str(image_path)]) == 0

    captured = capsys.readouterr()
    assert "source(来源)=" in captured.out
    assert "transparent_bg(透明底)=True" in captured.out
    assert "transparent_ratio(透明占比)=" in captured.out


def test_verify_image_inspect_json_stdout_is_machine_readable(tmp_path):
    image_path = tmp_path / "transparent.png"
    Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(image_path)

    result = subprocess.run(
        ["./scripts/verify.sh", "image-inspect", str(image_path), "--json"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["source"] == str(image_path)
    assert payload["alpha"]["transparent_background"] is True
