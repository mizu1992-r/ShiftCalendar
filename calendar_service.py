import os
from datetime import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
]

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

TIMEZONE = "Asia/Tokyo"
CREATED_BY = "ShiftCalendar"


def credentials_from_dict(credentials_data):
    """
    セッションに保存した辞書形式の認証情報から
    Google Credentialsを復元する。
    """

    if not credentials_data:
        raise ValueError(
            "Googleにログインしてください"
        )

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

    return Credentials(
        token=credentials_data.get("token"),
        refresh_token=credentials_data.get(
            "refresh_token"
        ),
        token_uri=credentials_data.get(
            "token_uri",
            GOOGLE_TOKEN_URI,
        ),
        client_id=client_id,
        client_secret=client_secret,
        scopes=credentials_data.get(
            "scopes",
            SCOPES,
        ),
    )


def credentials_to_dict(credentials):
    """
    Google Credentialsを
    セッションに保存できる辞書形式へ変換する。
    """

    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": (
            credentials.token_uri
            or GOOGLE_TOKEN_URI
        ),
        "scopes": list(
            credentials.scopes
            or SCOPES
        ),
    }


def get_calendar_service(credentials_data):
    """
    ログイン中の利用者の認証情報から、
    Google Calendar APIサービスを作成する。

    戻り値:
        service
        更新後の認証情報
    """

    credentials = credentials_from_dict(
        credentials_data
    )

    if (
        credentials.expired
        and credentials.refresh_token
    ):
        credentials.refresh(Request())

    if not credentials.valid:
        raise ValueError(
            "Googleの認証期限が切れています。"
            "もう一度ログインしてください"
        )

    service = build(
        "calendar",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )

    return (
        service,
        credentials_to_dict(credentials),
    )


def make_shift_key(
    year,
    month,
    day,
    start,
    end,
):
    """
    同じシフトか判定するための一意なキーを作る。

    例:
    2026-08-02_17:00_21:30
    """

    return (
        f"{int(year):04d}-"
        f"{int(month):02d}-"
        f"{int(day):02d}_"
        f"{start}_{end}"
    )


def parse_google_datetime(value):
    """
    GoogleカレンダーのdateTimeを
    YYYY-MM-DD HH:MMへ整形する。
    """

    if not value:
        return ""

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        return parsed.strftime(
            "%Y-%m-%d %H:%M"
        )

    except ValueError:
        return ""


def get_existing_shift_keys(
    service,
    year,
    month,
    calendar_id="primary",
):
    """
    指定月にShiftCalendarが登録した予定を取得し、
    重複判定用キーの集合を返す。
    """

    year = int(year)
    month = int(month)

    month_start = datetime(
        year,
        month,
        1,
    )

    if month == 12:
        next_month = datetime(
            year + 1,
            1,
            1,
        )
    else:
        next_month = datetime(
            year,
            month + 1,
            1,
        )

    response = service.events().list(
        calendarId=calendar_id,
        timeMin=(
            month_start.isoformat()
            + "+09:00"
        ),
        timeMax=(
            next_month.isoformat()
            + "+09:00"
        ),
        singleEvents=True,
        privateExtendedProperty=(
            f"createdBy={CREATED_BY}"
        ),
        maxResults=2500,
    ).execute()

    events = response.get("items", [])
    existing_keys = set()

    for event in events:
        private_properties = (
            event
            .get("extendedProperties", {})
            .get("private", {})
        )

        shift_key = private_properties.get(
            "shiftKey"
        )

        if shift_key:
            existing_keys.add(shift_key)
            continue

        # 以前の形式で登録した予定にも対応
        start_value = (
            event
            .get("start", {})
            .get("dateTime")
        )

        end_value = (
            event
            .get("end", {})
            .get("dateTime")
        )

        start_text = parse_google_datetime(
            start_value
        )

        end_text = parse_google_datetime(
            end_value
        )

        if not start_text or not end_text:
            continue

        start_datetime = datetime.strptime(
            start_text,
            "%Y-%m-%d %H:%M",
        )

        end_datetime = datetime.strptime(
            end_text,
            "%Y-%m-%d %H:%M",
        )

        fallback_key = make_shift_key(
            start_datetime.year,
            start_datetime.month,
            start_datetime.day,
            start_datetime.strftime("%H:%M"),
            end_datetime.strftime("%H:%M"),
        )

        existing_keys.add(fallback_key)

    return existing_keys


def create_shift_events(
    year,
    month,
    shifts,
    credentials_data,
    calendar_id="primary",
):
    """
    ログイン中の利用者のGoogleカレンダーへ、
    未登録のシフトだけ追加する。

    戻り値:
        {
            "created": 登録件数,
            "skipped": 重複で飛ばした件数,
            "credentials": 更新後の認証情報,
        }
    """

    service, updated_credentials = (
        get_calendar_service(
            credentials_data
        )
    )

    existing_keys = get_existing_shift_keys(
        service=service,
        year=year,
        month=month,
        calendar_id=calendar_id,
    )

    created_count = 0
    skipped_count = 0

    for shift in shifts:
        day = int(shift["day"])
        start = shift["start"]
        end = shift["end"]

        shift_key = make_shift_key(
            year,
            month,
            day,
            start,
            end,
        )

        if shift_key in existing_keys:
            skipped_count += 1
            continue

        start_datetime = datetime.strptime(
            f"{year}-{month}-{day} {start}",
            "%Y-%m-%d %H:%M",
        )

        end_datetime = datetime.strptime(
            f"{year}-{month}-{day} {end}",
            "%Y-%m-%d %H:%M",
        )

        event = {
            "summary": (
                f"シフト {start}-{end}"
            ),
            "description": (
                "ShiftCalendarから"
                "登録した予定です。"
            ),
            "start": {
                "dateTime": (
                    start_datetime.isoformat()
                ),
                "timeZone": TIMEZONE,
            },
            "end": {
                "dateTime": (
                    end_datetime.isoformat()
                ),
                "timeZone": TIMEZONE,
            },
            "extendedProperties": {
                "private": {
                    "createdBy": CREATED_BY,
                    "shiftKey": shift_key,
                }
            },
        }

        service.events().insert(
            calendarId=calendar_id,
            body=event,
        ).execute()

        existing_keys.add(shift_key)
        created_count += 1

    return {
        "created": created_count,
        "skipped": skipped_count,
        "credentials": updated_credentials,
    }