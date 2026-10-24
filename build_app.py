"""資料箱.exe を作り直して、使っている exe と配布用 zip を入れ替える

アプリを作る_Windows.bat (ダブルクリック) と、git pull 後のフック (.githooks/post-merge) から呼ばれる。
Python 3.9 以上の標準ライブラリだけで動く。ビルド用の Python は build/venv に自動で用意する。

入れ替える exe:
  - このフォルダの 資料箱.exe
  - update_targets.txt に1行ずつ書いたフォルダ (または exe) — 自分の PC 用の設定なので git には載せない
"""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
VENV_PY = BUILD / "venv" / "Scripts" / "python.exe"
DIST_EXE = BUILD / "dist" / "資料箱.exe"
EXE = "資料箱.exe"
ZIP = HERE / "資料箱_配布用.zip"


def run(cmd, check=True):
    print(">", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], cwd=HERE, check=check).returncode


def ensure_venv():
    if VENV_PY.exists():
        return
    print("ビルド用の環境を作っています…", flush=True)
    for py in (["py", "-3.11"], ["py", "-3"], [sys.executable]):
        try:
            run([*py, "-m", "venv", BUILD / "venv"])
            return
        except (OSError, subprocess.CalledProcessError):
            continue
    sys.exit("ビルド用の Python 環境を作れませんでした (Python 3.11 推奨)")


def build():
    ensure_venv()
    # ネットにつながらない時も、入っている版でビルドを続ける
    run([VENV_PY, "-m", "pip", "install", "-q", "pywebview", "pillow", "pyinstaller"], check=False)
    run([VENV_PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
         "--name", "資料箱", "--icon", HERE / "icon.ico", "--add-data", f"{HERE / 'app.html'};.",
         "--distpath", BUILD / "dist", "--workpath", BUILD / "pyi", "--specpath", BUILD, "shiryo_box.py"])
    run([VENV_PY, HERE / "make_licenses.py"])


def targets():
    out = [HERE / EXE]
    conf = HERE / "update_targets.txt"
    if conf.exists():
        for line in conf.read_text("utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                p = Path(line)
                out.append(p if p.suffix.lower() == ".exe" else p / EXE)
    return out


def install():
    """作った exe で入れ替える。起動中の exe は Windows が書き換えさせないので、知らせるだけ"""
    busy = []
    for t in targets():
        if not t.parent.is_dir():
            print("フォルダが見つからないので飛ばしました:", t.parent)
            continue
        try:
            shutil.copy2(DIST_EXE, t)
            print("更新しました:", t)
        except PermissionError:
            busy.append(t)
    return busy


def make_zip():
    files = {"資料箱.exe": DIST_EXE, "README.txt": HERE / "README.txt",
             "THIRD_PARTY_LICENSES.txt": HERE / "THIRD_PARTY_LICENSES.txt"}
    tmp = ZIP.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, src in files.items():
            z.write(src, "資料箱/" + name)
    tmp.replace(ZIP)
    print("配布用 zip を作り直しました:", ZIP)


def main():
    # git pull のフック経由だと出力がパイプになり、Python は Shift_JIS で書いてしまう。
    # Git Bash などは UTF-8 で読むので文字化けする → パイプの時は UTF-8 に揃える
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        os.environ["PYTHONIOENCODING"] = "utf-8"
    build()
    busy = install()
    make_zip()
    if busy:
        print("\n起動中だったので入れ替えられませんでした。資料箱を閉じてから、もう一度 アプリを作る_Windows.bat を実行してください:")
        for t in busy:
            print("  ", t)
        return 1
    print("\n資料箱.exe を最新にしました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
