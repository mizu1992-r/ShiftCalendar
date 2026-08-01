import csv
import json
import re
from pathlib import Path


OCR_JSON_PATH = Path("outputs/paddle_test/ocr_result.json")
OUTPUT_CSV_PATH = Path("outputs/extracted_shift.csv")

# シフト表に載っている従業員名
EMPLOYEE_NAMES = [
    "彦坂",
    "白田",
    "西山",
    "水戸",
    "石橋",
    "森友",
    "曾我部",
]


def box_center(box):
    """OCRのboxから中央座標を返す。"""
    left, top, right, bottom = box
    return (
        (left + right) / 2,
        (top + bottom) / 2,
    )


def load_ocr_results(json_path):
    if not json_path.exists():
        raise FileNotFoundError(f"OCR結果が見つかりません: {json_path}")

    with open(json_path, encoding="utf-8") as f:
        rows = json.load(f)

    normalized = []

    for row in rows:
        box = row.get("box", [])

        if len(box) != 4:
            continue

        center_x, center_y = box_center(box)

        normalized.append({
            "text": str(row.get("text", "")).strip(),
            "score": float(row.get("score", 0)),
            "box": box,
            "center_x": center_x,
            "center_y": center_y,
        })

    return normalized

def estimate_table_slope(ocr_rows):
    """
    従業員名の見出し行から、表の横方向の傾きを自動推定する。

    戻り値:
        xが1px右へ進んだときのy座標の変化量

    右上がりの表では負の値、
    右下がりの表では正の値になる。
    """
    employee_headers = []

    for row in ocr_rows:
        if row["text"] not in EMPLOYEE_NAMES:
            continue

        # 従業員名は表の上部にある
        if not 70 <= row["center_y"] <= 180:
            continue

        employee_headers.append(row)

    employee_headers.sort(
        key=lambda row: row["center_x"]
    )

    if len(employee_headers) < 2:
        print("傾き推定: 見出し不足のため補正なし")
        return 0.0

    # 最小二乗法で y = slope*x + intercept を求める
    x_values = [
        row["center_x"]
        for row in employee_headers
    ]

    y_values = [
        row["center_y"]
        for row in employee_headers
    ]

    x_average = sum(x_values) / len(x_values)
    y_average = sum(y_values) / len(y_values)

    numerator = sum(
        (x - x_average) * (y - y_average)
        for x, y in zip(x_values, y_values)
    )

    denominator = sum(
        (x - x_average) ** 2
        for x in x_values
    )

    if abs(denominator) < 1e-9:
        print("傾き推定: 計算不能のため補正なし")
        return 0.0

    slope = numerator / denominator

    # 異常な傾きは使わない
    if abs(slope) > 0.08:
        print(
            f"傾き推定: 異常値 {slope:.6f} のため補正なし"
        )
        return 0.0

    print(
        f"傾き推定: {slope:.6f} "
        f"（見出し数: {len(employee_headers)}）"
    )

    return slope

def find_employee_columns(ocr_rows):
    """
    従業員名の中央座標を探し、
    各人の担当列の左右境界を計算する。
    """
    employees = []

    for row in ocr_rows:
        if row["text"] in EMPLOYEE_NAMES:
            employees.append({
                "name": row["text"],
                "center_x": row["center_x"],
            })

    employees.sort(key=lambda item: item["center_x"])

    if not employees:
        raise ValueError("従業員名を認識できませんでした")

    columns = {}

    for index, employee in enumerate(employees):
        center_x = employee["center_x"]

        if index == 0:
            if len(employees) >= 2:
                width = employees[1]["center_x"] - center_x
            else:
                width = 130

            left = center_x - width / 2
        else:
            left = (
                employees[index - 1]["center_x"]
                + center_x
            ) / 2

        if index == len(employees) - 1:
            if len(employees) >= 2:
                width = center_x - employees[index - 1]["center_x"]
            else:
                width = 130

            right = center_x + width / 2
        else:
            right = (
                center_x
                + employees[index + 1]["center_x"]
            ) / 2

        columns[employee["name"]] = {
            "left": left,
            "right": right,
            "center": center_x,
        }

    return columns


def find_date_rows(ocr_rows):
    """
    左側の日付欄から各日付の縦位置を取得する。

    写真の大きさや位置に依存しないように、
    固定のx座標ではなく画像幅と文字形式で判定する。

    対応例:
    1土
    2日
    8±
    15土刺しなし
    21金休館日あけ
    """

    if not ocr_rows:
        return []

    image_right = max(
        row["box"][2]
        for row in ocr_rows
        if len(row.get("box", [])) == 4
    )

    # 日付欄は画像の左側25%以内にあるものとして判定
    date_area_right = image_right * 0.25

    dates = []

    for row in ocr_rows:
        box = row.get("box", [])

        if len(box) != 4:
            continue

        box_left = box[0]

        # 画像の左側にある文字だけを対象にする
        if box_left > date_area_right:
            continue

        text = row["text"].strip()

        # 曜日を含む日付だけを取得
        # 「±」は土曜日のOCR誤認として許可
        match = re.match(
            r"^\s*(\d{1,2})\s*[月火水木金土日±]",
            text,
        )

        if not match:
            continue

        day = int(match.group(1))

        if not 1 <= day <= 31:
            continue

        dates.append({
            "day": day,
            "center_y": row["center_y"],
            "text": text,
        })

    # 同じ日付が複数認識された場合は最初のものを使用
    unique = {}

    for item in dates:
        day = item["day"]

        if day not in unique:
            unique[day] = item

    result = list(unique.values())
    result.sort(key=lambda item: item["center_y"])

    print(
        f"日付認識: {len(result)}件 "
        f"({', '.join(str(item['day']) for item in result)})"
    )

    return result


def build_date_ranges(date_rows):
    """
    日付の中央位置から、それぞれの行の上下範囲を作る。
    """
    ranges = []

    for index, current in enumerate(date_rows):
        current_y = current["center_y"]

        if index == 0:
            if len(date_rows) >= 2:
                gap = date_rows[1]["center_y"] - current_y
            else:
                gap = 27

            top = current_y - gap / 2
        else:
            top = (
                date_rows[index - 1]["center_y"]
                + current_y
            ) / 2

        if index == len(date_rows) - 1:
            if len(date_rows) >= 2:
                gap = current_y - date_rows[index - 1]["center_y"]
            else:
                gap = 27

            bottom = current_y + gap / 2
        else:
            bottom = (
                current_y
                + date_rows[index + 1]["center_y"]
            ) / 2

        ranges.append({
            "day": current["day"],
            "top": top,
            "bottom": bottom,
        })

    return ranges


def extract_time_values(text):
    """
    OCR文字列から時刻らしい数字を取得する。

    対応例:
    17.00
    17.00 21.50
    17.0021.50
    17.5
    """
    text = str(text).strip()

    # 「17.0021.50」のようにつながった場合
    compact_matches = re.findall(
        r"(?<!\d)(\d{1,2}\.\d{1,2})(?=\d{1,2}\.\d{1,2})",
        text,
    )

    normal_matches = re.findall(
        r"\d{1,2}[.:]\d{1,2}",
        text,
    )

    short_matches = re.findall(
        r"(?<!\d)(\d{1,2}\.[05])(?!\d)",
        text,
    )

    values = []

    for value in compact_matches + normal_matches + short_matches:
        if value not in values:
            values.append(value)

    # 17.0021.50 の専用分割
    joined = re.fullmatch(
        r"(\d{1,2}\.\d{2})(\d{1,2}\.\d{2})",
        text.replace(" ", ""),
    )

    if joined:
        values = [joined.group(1), joined.group(2)]

    return values


def normalize_time(value):
    """
    シフト表独自の時間表記を通常時刻に変換する。

    17.00 -> 17:00
    17.50 -> 17:30
    17.5  -> 17:30
    """
    value = str(value).strip().replace(":", ".")

    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})", value)

    if not match:
        return ""

    hour = int(match.group(1))
    minute_text = match.group(2)

    if minute_text in {"5", "50"}:
        minute = 30
    elif minute_text in {"0", "00"}:
        minute = 0
    else:
        return ""

    if hour < 7 or hour > 21:
        return ""

    if hour == 21 and minute > 30:
        return ""

    return f"{hour:02d}:{minute:02d}"


def is_valid_shift(start, end):
    if not start or not end:
        return False

    start_minutes = (
        int(start[:2]) * 60
        + int(start[3:])
    )

    end_minutes = (
        int(end[:2]) * 60
        + int(end[3:])
    )

    return end_minutes > start_minutes


def extract_employee_shifts(ocr_rows, target_name):
    columns = find_employee_columns(ocr_rows)
    table_slope = estimate_table_slope(ocr_rows)

    if target_name not in columns:
        available = "、".join(columns.keys())
        raise ValueError(
            f"{target_name}が見つかりません。"
            f"認識できた名前: {available}"
        )

    date_rows = find_date_rows(ocr_rows)
    date_ranges = build_date_ranges(date_rows)

    target = columns[target_name]
    column_width = target["right"] - target["left"]

    # 各人の列は概ね
    # 出・退・休・実 の4区画。
    # 実働欄を除くため、左から約76%だけを見る。
    work_left = target["left"]
    work_right = target["left"] + column_width * 0.76

    shifts = []

    for date_range in date_ranges:
        candidates = []

        for row in ocr_rows:
            # 日付列のx座標を基準に、表の傾きを補正する
            reference_x = 50.0

            corrected_y = (
                row["center_y"]
                - table_slope
                * (row["center_x"] - reference_x)
            )

            if not (
                date_range["top"]
                <= corrected_y
                < date_range["bottom"]
            ):
               continue

            # 文字の中央ではなく、boxの左右も使って
            # 対象者の出・退側と重なるものを取得する
            box_left = row["box"][0]
            box_right = row["box"][2]

            overlaps = (
                box_right >= work_left
                and box_left <= work_right
            )

            if not overlaps:
                continue

            time_values = extract_time_values(row["text"])

            if not time_values:
                continue

            candidates.append({
                "x": row["center_x"],
                "text": row["text"],
                "times": time_values,
            })

        candidates.sort(key=lambda item: item["x"])

        raw_times = []

        for candidate in candidates:
            for value in candidate["times"]:
                normalized = normalize_time(value)

                if normalized and normalized not in raw_times:
                    raw_times.append(normalized)

        # 出勤と退勤の最初の2時刻だけ使用
        if len(raw_times) < 2:
            continue

        start = raw_times[0]
        end = raw_times[1]

        if not is_valid_shift(start, end):
            continue

        shifts.append({
            "day": str(date_range["day"]),
            "start": start,
            "end": end,
        })

    return shifts


def save_csv(rows, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(
        output_path,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["day", "start", "end"],
        )
        writer.writeheader()
        writer.writerows(rows)


def main():
    target_name = "水戸"

    ocr_rows = load_ocr_results(OCR_JSON_PATH)
    shifts = extract_employee_shifts(
        ocr_rows,
        target_name,
    )

    save_csv(shifts, OUTPUT_CSV_PATH)

    print()
    print(f"対象者: {target_name}")
    print(f"抽出件数: {len(shifts)}件")

    for shift in shifts:
        print(
            f"{shift['day']}日 "
            f"{shift['start']} - {shift['end']}"
        )

    print()
    print(f"CSV保存先: {OUTPUT_CSV_PATH.resolve()}")


if __name__ == "__main__":
     main()