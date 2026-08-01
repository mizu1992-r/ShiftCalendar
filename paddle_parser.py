import json
from pathlib import Path

from paddleocr import PaddleOCR


# --------------------------------------------------
# OCRモデルは起動時に1回だけ作る
# --------------------------------------------------

OCR_ENGINE = PaddleOCR(
    lang="japan",
    device="cpu",
    enable_mkldnn=False,
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
)


# --------------------------------------------------
# boxから中心座標を求める
# --------------------------------------------------

def calc_center(box):

    if len(box) < 4:
        return 0.0, 0.0

    return (
        (box[0] + box[2]) / 2,
        (box[1] + box[3]) / 2,
    )


# --------------------------------------------------
# OCR解析
# --------------------------------------------------

def analyze_shift_table(image_path):

    image_path = Path(image_path)

    if not image_path.exists():
        raise FileNotFoundError(
            f"画像が見つかりません: {image_path}"
        )

    output_dir = Path("outputs/paddle_test")
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("OCR解析中...")

    results = OCR_ENGINE.predict(str(image_path))

    extracted = []

    for result in results:

        data = result.json

        if callable(data):
            data = data()

        if isinstance(data, str):
            data = json.loads(data)

        result_data = data.get("res", data)

        texts = result_data.get(
            "rec_texts",
            [],
        )

        scores = result_data.get(
            "rec_scores",
            [],
        )

        boxes = result_data.get(
            "rec_boxes",
            [],
        )

        for text, score, box in zip(
            texts,
            scores,
            boxes,
        ):

            if hasattr(box, "tolist"):
                box = box.tolist()

            center_x, center_y = calc_center(box)

            extracted.append(
                {
                    "text": str(text).strip(),
                    "score": float(score),
                    "box": box,
                    "center_x": center_x,
                    "center_y": center_y,
                }
            )

    output_path = (
        output_dir / "ocr_result.json"
    )

    with open(
        output_path,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            extracted,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(
        f"OCR完了 "
        f"({len(extracted)}件)"
    )

    return extracted


# --------------------------------------------------

if __name__ == "__main__":

    rows = analyze_shift_table(
        "uploads/test.jpg"
    )

    print(
        f"取得件数: {len(rows)}"
    )