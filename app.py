import calendar
import csv
from datetime import datetime
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from calendar_service import create_shift_events
from paddle_parser import analyze_shift_table
from shift_extractor import extract_employee_shifts


app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"

SHIFT_CSV_PATH = OUTPUT_DIR / "shift_result.csv"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}

TIME_OPTIONS = [
    f"{hour:02d}:{minute:02d}"
    for hour in range(7, 22)
    for minute in (0, 30)
    if not (hour == 21 and minute > 30)
]


def is_allowed_image(filename):
    suffix = Path(filename).suffix.lower()
    return suffix in ALLOWED_EXTENSIONS


def get_weekday(year, month, day):
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]

    try:
        index = calendar.weekday(
            int(year),
            int(month),
            int(day),
        )
        return weekdays[index]

    except (TypeError, ValueError):
        return "-"


def add_weekdays(shifts, year, month):
    result = []

    for shift in shifts:
        row = dict(shift)

        row["day"] = str(row.get("day", "")).strip()
        row["start"] = str(row.get("start", "")).strip()
        row["end"] = str(row.get("end", "")).strip()

        row["weekday"] = get_weekday(
            year,
            month,
            row["day"],
        )

        result.append(row)

    return result


def read_saved_shifts():
    if not SHIFT_CSV_PATH.exists():
        return []

    with open(
        SHIFT_CSV_PATH,
        newline="",
        encoding="utf-8-sig",
    ) as file:
        return list(csv.DictReader(file))


def save_shifts(rows):
    with open(
        SHIFT_CSV_PATH,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["day", "start", "end"],
        )

        writer.writeheader()
        writer.writerows(rows)


def render_index(
    shifts=None,
    message="",
    target_name="",
    year=None,
    month=None,
):
    now = datetime.now()

    year = int(year or now.year)
    month = int(month or now.month)

    shifts = shifts or []
    shifts = add_weekdays(shifts, year, month)

    return render_template(
        "index.html",
        shifts=shifts,
        message=message,
        target_name=target_name,
        year=year,
        month=month,
        time_options=TIME_OPTIONS,
    )


def get_shift_rows_from_form(year, month):
    days = request.form.getlist("day")
    starts = request.form.getlist("start")
    ends = request.form.getlist("end")

    rows = []
    seen = set()

    try:
        year_number = int(year)
        month_number = int(month)

        last_day = calendar.monthrange(
            year_number,
            month_number,
        )[1]

    except (TypeError, ValueError):
        raise ValueError("年または月が正しくありません")

    for day, start, end in zip(days, starts, ends):
        day = day.strip()
        start = start.strip()
        end = end.strip()

        if not day and not start and not end:
            continue

        if not day or not start or not end:
            raise ValueError("入力漏れがあります")

        try:
            day_number = int(day)

        except ValueError:
            raise ValueError("日付が正しくありません")

        if not 1 <= day_number <= last_day:
            raise ValueError(
                f"{day_number}日は"
                f"{year_number}年{month_number}月に存在しません"
            )

        if start not in TIME_OPTIONS or end not in TIME_OPTIONS:
            raise ValueError("時間が正しくありません")

        start_minutes = (
            int(start[:2]) * 60
            + int(start[3:])
        )

        end_minutes = (
            int(end[:2]) * 60
            + int(end[3:])
        )

        if end_minutes <= start_minutes:
            raise ValueError(
                f"{day_number}日の終了時間は"
                "開始時間より後にしてください"
            )

        key = (
            str(day_number),
            start,
            end,
        )

        if key in seen:
            continue

        seen.add(key)

        rows.append({
            "day": str(day_number),
            "start": start,
            "end": end,
        })

    rows.sort(
        key=lambda row: int(row["day"])
    )

    return rows


@app.route("/")
def index():
    year = request.args.get("year")
    month = request.args.get("month")
    message = request.args.get("msg", "")

    shifts = read_saved_shifts()

    return render_index(
        shifts=shifts,
        message=message,
        year=year,
        month=month,
    )


@app.route("/analyze", methods=["POST"])
def analyze():
    image = request.files.get("image")

    target_name = request.form.get(
        "target_name",
        "",
    ).strip()

    year = request.form.get(
        "year",
        datetime.now().year,
    )

    month = request.form.get(
        "month",
        datetime.now().month,
    )

    if not image or not image.filename:
        return render_index(
            message="画像を選択してください",
            target_name=target_name,
            year=year,
            month=month,
        )

    if not target_name:
        return render_index(
            message="対象者名を入力してください",
            year=year,
            month=month,
        )

    if not is_allowed_image(image.filename):
        return render_index(
            message="JPG・PNG・WebP画像を選択してください",
            target_name=target_name,
            year=year,
            month=month,
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

        return render_index(
            shifts=shifts,
            message=(
                f"{target_name}のシフトを"
                f"{len(shifts)}件抽出しました"
            ),
            target_name=target_name,
            year=year,
            month=month,
        )

    except Exception as error:
        return render_index(
            message=f"解析エラー：{error}",
            target_name=target_name,
            year=year,
            month=month,
        )


@app.route("/save", methods=["POST"])
def save():
    year = request.form.get(
        "year",
        datetime.now().year,
    )

    month = request.form.get(
        "month",
        datetime.now().month,
    )

    try:
        rows = get_shift_rows_from_form(
            year,
            month,
        )

        save_shifts(rows)

        return redirect(
            url_for(
                "index",
                year=year,
                month=month,
                msg=f"{len(rows)}件保存しました",
            )
        )

    except Exception as error:
        return render_index(
            shifts=[],
            message=f"保存エラー：{error}",
            year=year,
            month=month,
        )


@app.route("/calendar", methods=["POST"])
def calendar_register():
    year = request.form.get(
        "year",
        datetime.now().year,
    )

    month = request.form.get(
        "month",
        datetime.now().month,
    )

    rows = []

    try:
        rows = get_shift_rows_from_form(
            year,
            month,
        )

        if not rows:
            raise ValueError(
                "登録するシフトがありません"
            )

        result = create_shift_events(
            year=int(year),
            month=int(month),
            shifts=rows,
        )

        save_shifts(rows)

        return redirect(
            url_for(
                "index",
                year=year,
                month=month,
                msg=(
                    f"{result['created']}件登録しました。"
                    f"登録済みの{result['skipped']}件は"
                    "スキップしました"
                ),
            )
        )

    except Exception as error:
        return render_index(
            shifts=rows,
            message=(
                f"カレンダー登録エラー：{error}"
            ),
            year=year,
            month=month,
        )


@app.route("/delete_all", methods=["POST"])
def delete_all():
    year = request.form.get(
        "year",
        datetime.now().year,
    )

    month = request.form.get(
        "month",
        datetime.now().month,
    )

    if SHIFT_CSV_PATH.exists():
        SHIFT_CSV_PATH.unlink()

    return redirect(
        url_for(
            "index",
            year=year,
            month=month,
            msg="全削除しました",
        )
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        use_reloader=False,
    )