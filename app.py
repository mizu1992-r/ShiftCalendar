import calendar
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import (
    Flask,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_session import Session
from google_auth_oauthlib.flow import Flow
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from calendar_service import (
    SCOPES,
    create_shift_events,
    credentials_to_dict,
)
from paddle_parser import analyze_shift_table
from shift_extractor import extract_employee_shifts


app = Flask(__name__)

# Renderなどのリバースプロキシ越しでも
# httpsのURLを正しく生成するための設定
app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1,
    x_proto=1,
    x_host=1,
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"

if os.environ.get("RENDER", "").lower() == "true":
    default_session_dir = Path("/tmp/shiftcalendar_sessions")
else:
    default_session_dir = BASE_DIR / ".flask_session"

SESSION_DIR = Path(
    os.environ.get(
        "SESSION_FILE_DIR",
        str(default_session_dir),
    )
)

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)

app.config.update(
    SECRET_KEY=os.environ.get(
        "FLASK_SECRET_KEY",
        "local-development-secret-key",
    ),
    SESSION_TYPE="filesystem",
    SESSION_FILE_DIR=str(SESSION_DIR),
    SESSION_PERMANENT=False,
    SESSION_USE_SIGNER=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        os.environ.get("RENDER", "").lower() == "true"
    ),
    MAX_CONTENT_LENGTH=20 * 1024 * 1024,
)

Session(app)


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
    weekdays = [
        "月",
        "火",
        "水",
        "木",
        "金",
        "土",
        "日",
    ]

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
        row = {
            "day": str(
                shift.get("day", "")
            ).strip(),
            "start": str(
                shift.get("start", "")
            ).strip(),
            "end": str(
                shift.get("end", "")
            ).strip(),
        }

        row["weekday"] = get_weekday(
            year,
            month,
            row["day"],
        )

        result.append(row)

    return result


def get_google_client_config():
    client_id = os.environ.get(
        "GOOGLE_CLIENT_ID",
        "",
    ).strip()

    client_secret = os.environ.get(
        "GOOGLE_CLIENT_SECRET",
        "",
    ).strip()

    if not client_id or not client_secret:
        raise RuntimeError(
            "Google OAuthの環境変数が設定されていません"
        )

    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": (
                "https://accounts.google.com/o/oauth2/auth"
            ),
            "token_uri": (
                "https://oauth2.googleapis.com/token"
            ),
        }
    }


def get_redirect_uri():
    configured_uri = os.environ.get(
        "GOOGLE_REDIRECT_URI",
        "",
    ).strip()

    if configured_uri:
        return configured_uri

    # ローカルでは http://127.0.0.1:5000 を使用。
    # Renderでは ProxyFix により https の公開URLを生成。
    return url_for(
        "oauth_callback",
        _external=True,
    )


def render_index(
    shifts=None,
    message="",
    target_name="",
    year=None,
    month=None,
):
    now = datetime.now()

    year = int(
        year
        or session.get("year")
        or now.year
    )

    month = int(
        month
        or session.get("month")
        or now.month
    )

    if shifts is None:
        shifts = session.get("shifts", [])

    if not target_name:
        target_name = session.get(
            "target_name",
            "",
        )

    return render_template(
        "index.html",
        shifts=add_weekdays(
            shifts,
            year,
            month,
        ),
        message=message,
        target_name=target_name,
        year=year,
        month=month,
        time_options=TIME_OPTIONS,
        google_logged_in=bool(
            session.get("google_credentials")
        ),
    )


def get_shift_rows_from_form(year, month):
    days = request.form.getlist("day")
    starts = request.form.getlist("start")
    ends = request.form.getlist("end")

    try:
        year_number = int(year)
        month_number = int(month)

        last_day = calendar.monthrange(
            year_number,
            month_number,
        )[1]

    except (TypeError, ValueError) as error:
        raise ValueError(
            "年または月が正しくありません"
        ) from error

    rows = []
    seen = set()

    for day, start, end in zip(
        days,
        starts,
        ends,
    ):
        day = day.strip()
        start = start.strip()
        end = end.strip()

        if not day and not start and not end:
            continue

        if not day or not start or not end:
            raise ValueError(
                "入力漏れがあります"
            )

        try:
            day_number = int(day)

        except ValueError as error:
            raise ValueError(
                "日付が正しくありません"
            ) from error

        if not 1 <= day_number <= last_day:
            raise ValueError(
                f"{day_number}日は"
                f"{year_number}年"
                f"{month_number}月に存在しません"
            )

        if (
            start not in TIME_OPTIONS
            or end not in TIME_OPTIONS
        ):
            raise ValueError(
                "時間が正しくありません"
            )

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


def save_form_to_session(year, month, rows):
    session["year"] = int(year)
    session["month"] = int(month)
    session["shifts"] = rows
    session.modified = True


@app.route("/")
def index():
    return render_index(
        message=request.args.get(
            "msg",
            "",
        )
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
            message="名前を入力してください",
            year=year,
            month=month,
        )

    if not is_allowed_image(image.filename):
        return render_index(
            message=(
                "JPG・PNG・WebP画像を"
                "選択してください"
            ),
            target_name=target_name,
            year=year,
            month=month,
        )

    original_name = secure_filename(
        image.filename
    )

    suffix = Path(original_name).suffix.lower()

    if suffix not in ALLOWED_EXTENSIONS:
        suffix = ".jpg"

    image_path = (
        UPLOAD_DIR
        / f"{uuid4().hex}{suffix}"
    )

    image.save(image_path)

    try:
        ocr_rows = analyze_shift_table(
            image_path
        )

        shifts = extract_employee_shifts(
            ocr_rows,
            target_name,
        )

        session["target_name"] = target_name

        save_form_to_session(
            year,
            month,
            shifts,
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

    finally:
        try:
            image_path.unlink(
                missing_ok=True
            )
        except OSError:
            pass


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

        save_form_to_session(
            year,
            month,
            rows,
        )

        return redirect(
            url_for(
                "index",
                msg=f"{len(rows)}件保存しました",
            )
        )

    except Exception as error:
        return render_index(
            shifts=session.get(
                "shifts",
                [],
            ),
            message=f"保存エラー：{error}",
            year=year,
            month=month,
        )


@app.route("/login")
def google_login():
    try:
        flow = Flow.from_client_config(
            get_google_client_config(),
            scopes=SCOPES,
            autogenerate_code_verifier=True,
        )

        flow.redirect_uri = get_redirect_uri()

        authorization_url, state = (
            flow.authorization_url(
                access_type="offline",
                prompt="consent",
            )
        )

        session["oauth_state"] = state
        session["oauth_code_verifier"] = flow.code_verifier
        session.modified = True

        return redirect(authorization_url)

    except Exception as error:
        return redirect(
            url_for(
                "index",
                msg=f"Googleログイン開始エラー：{error}",
            )
        )


@app.route("/oauth/callback")
def oauth_callback():
    expected_state = session.get("oauth_state")
    code_verifier = session.get("oauth_code_verifier")

    if not expected_state:
        return redirect(
            url_for(
                "index",
                msg="Googleログイン情報が見つかりません",
            )
        )

    if not code_verifier:
        return redirect(
            url_for(
                "index",
                msg=(
                    "Googleログイン用の確認情報が"
                    "見つかりません。もう一度ログインしてください"
                ),
            )
        )

    try:
        flow = Flow.from_client_config(
            get_google_client_config(),
            scopes=SCOPES,
            state=expected_state,
            code_verifier=code_verifier,
        )

        flow.redirect_uri = get_redirect_uri()

        flow.fetch_token(
            authorization_response=request.url
        )

        session["google_credentials"] = (
            credentials_to_dict(
                flow.credentials
            )
        )

        session.pop("oauth_state", None)
        session.pop("oauth_code_verifier", None)
        session.modified = True

        return redirect(
            url_for(
                "index",
                msg="Googleへのログインが完了しました",
            )
        )

    except Exception as error:
        session.pop("oauth_state", None)
        session.pop("oauth_code_verifier", None)
        session.modified = True

        return redirect(
            url_for(
                "index",
                msg=f"Googleログインエラー：{error}",
            )
        )


@app.route("/logout", methods=["POST"])
def google_logout():
    session.pop(
        "google_credentials",
        None,
    )

    session.pop(
        "oauth_state",
        None,
    )

    session.modified = True

    return redirect(
        url_for(
            "index",
            msg="Googleからログアウトしました",
        )
    )


@app.route("/calendar", methods=["POST"])
def calendar_register():
    year = request.form.get(
        "year",
        session.get(
            "year",
            datetime.now().year,
        ),
    )

    month = request.form.get(
        "month",
        session.get(
            "month",
            datetime.now().month,
        ),
    )

    rows = []

    try:
        rows = get_shift_rows_from_form(
            year,
            month,
        )

        save_form_to_session(
            year,
            month,
            rows,
        )

        credentials_data = session.get(
            "google_credentials"
        )

        if not credentials_data:
            return redirect(
                url_for(
                    "google_login",
                )
            )

        result = create_shift_events(
            year=int(year),
            month=int(month),
            shifts=rows,
            credentials_data=(
                credentials_data
            ),
        )

        session["google_credentials"] = (
            result["credentials"]
        )

        session.modified = True

        return redirect(
            url_for(
                "index",
                msg=(
                    f"{result['created']}件"
                    "登録しました。"
                    f"登録済みの"
                    f"{result['skipped']}件は"
                    "スキップしました"
                ),
            )
        )

    except Exception as error:
        return render_index(
            shifts=(
                rows
                or session.get(
                    "shifts",
                    [],
                )
            ),
            message=(
                "カレンダー登録エラー："
                f"{error}"
            ),
            year=year,
            month=month,
        )


@app.route(
    "/delete_all",
    methods=["POST"],
)
def delete_all():
    session["shifts"] = []
    session.modified = True

    return redirect(
        url_for(
            "index",
            msg="全削除しました",
        )
    )


@app.errorhandler(413)
def file_too_large(_error):
    return render_index(
        message=(
            "画像サイズが大きすぎます。"
            "20MB以下の画像を選択してください"
        )
    ), 413


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
        use_reloader=False,
    )
