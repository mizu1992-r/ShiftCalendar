from waitress import serve

from app import app


if __name__ == "__main__":
    print("ShiftCalendar Webを起動しました")
    print("PC: http://127.0.0.1:5000")
    print("停止: Ctrl + C")

    serve(
        app,
        host="0.0.0.0",
        port=5000,
        threads=4,
    )