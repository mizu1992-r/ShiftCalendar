from pathlib import Path

from flask import Flask, redirect, render_template, request
from werkzeug.utils import secure_filename

from paddle_parser import analyze_shift_table
from shift_extractor import extract_employee_shifts


app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}


def is_allowed_image(filename):
    suffix = Path(filename).suffix.lower()
    return suffix in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    return render_template(
        "index.html",
        shifts=[],
        message="",
        target_name="",
    )


@app.route("/analyze", methods=["POST"])
def analyze():
    image = request.files.get("image")
    target_name = request.form.get(
        "target_name",
        "",
    ).strip()

    if not image or not image.filename:
        return render_template(
            "index.html",
            shifts=[],
            message="画像を選択してください",
            target_name=target_name,
        )

    if not target_name:
        return render_template(
            "index.html",
            shifts=[],
            message="対象者名を入力してください",
            target_name="",
        )

    if not is_allowed_image(image.filename):
        return render_template(
            "index.html",
            shifts=[],
            message="JPG・PNG・WebP画像を選択してください",
            target_name=target_name,
        )

    filename = secure_filename(image.filename)

    if not filename:
        filename = "shift_image.jpg"

    image_path = UPLOAD_DIR / filename
    image.save(image_path)

    try:
        ocr_rows = analyze_shift_table(image_path)

        shifts = extract_employee_shifts(
            ocr_rows,
            target_name,
        )

        message = (
            f"{target_name}のシフトを"
            f"{len(shifts)}件抽出しました"
        )

        return render_template(
            "index.html",
            shifts=shifts,
            message=message,
            target_name=target_name,
        )

    except Exception as error:
        return render_template(
            "index.html",
            shifts=[],
            message=f"解析エラー：{error}",
            target_name=target_name,
        )


if __name__ == "__main__":
    app.run(debug=True)