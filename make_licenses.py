"""資料箱.exe に同梱される他者ソフトのライセンス全文を THIRD_PARTY_LICENSES.txt にまとめる

アプリを作る_Windows.bat から、ビルド用の Python (build/venv) で実行される。
"""
import sys
from importlib.metadata import distribution
from pathlib import Path

OUT = Path(__file__).resolve().parent / "THIRD_PARTY_LICENSES.txt"

# (パッケージ名, 用途)。exe に入るものだけ
PACKAGES = [
    ("pillow", "画像の読み込み・CMYK/TIFF/PSD の変換"),
    ("pywebview", "アプリのウィンドウ表示"),
    ("pythonnet", "pywebview から Windows の機能を使うため"),
    ("clr_loader", "pythonnet の内部部品"),
    ("cffi", "clr_loader の内部部品"),
    ("pycparser", "cffi の内部部品"),
    ("bottle", "pywebview の内部部品"),
    ("proxy_tools", "pywebview の内部部品"),
    ("typing_extensions", "pywebview の内部部品"),
    ("packaging", "PyInstaller の起動処理が使う部品"),
    ("setuptools", "PyInstaller の起動処理が使う部品 (pkg_resources)"),
    ("pyinstaller", "exe 化の起動部分 (ブートローダー)"),
]

# パッケージにライセンス文が入っていないもの
FALLBACK = {
    "proxy_tools": """MIT License (作者: Jonathan Tushman, http://github.com/jtushman/proxy_tools)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.""",
}

WEBVIEW2 = """Copyright (C) Microsoft Corporation. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

   * Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.
   * Redistributions in binary form must reproduce the above
copyright notice, this list of conditions and the following disclaimer
in the documentation and/or other materials provided with the
distribution.
   * The name of Microsoft Corporation, or the names of its contributors
may not be used to endorse or promote products derived from this
software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE."""

RULE = "=" * 78


def license_texts(dist):
    seen, texts = set(), []
    for f in dist.files or []:
        name = f.name.upper()
        in_info = f.parent.name.endswith(".dist-info") or f.parent.name == "licenses"
        if in_info and name.startswith(("LICENSE", "LICENCE", "COPYING", "AUTHORS")):
            text = Path(f.locate()).read_text(encoding="utf-8", errors="replace").strip()
            if text and text not in seen:
                seen.add(text)
                texts.append(f"--- {f.name} ---\n{text}")
    return texts


def main():
    parts = [
        "資料箱 同梱ソフトウェアのライセンス (THIRD PARTY LICENSES)",
        "",
        "資料箱.exe には、以下の他者が作ったソフトウェアが含まれています。",
        "それぞれの権利は各作者に帰属し、以下のライセンス条件で利用・再配布しています。",
        "(この一覧はビルド時に自動生成しています)",
        "",
    ]
    ver = sys.version.split()[0]
    parts += [RULE, f"Python {ver} (実行環境本体)  -  Python Software Foundation License Version 2",
              "Windows 版に含まれる OpenSSL / libffi / bzip2 / xz / Expat / zlib 等の条件も下記に含まれます", RULE]
    parts.append((Path(sys.base_prefix) / "LICENSE.txt").read_text(encoding="utf-8", errors="replace").strip())
    parts += ["", RULE, "Microsoft Visual C++ ランタイム (VCRUNTIME140.dll)",
              "Python に同梱されている Microsoft の再頒布可能コンポーネントです。著作権は Microsoft Corporation に帰属します。", RULE, ""]
    for name, use in PACKAGES:
        d = distribution(name)
        lic = d.metadata.get("License-Expression") or (d.metadata.get("License") or "").splitlines()[0:1]
        lic = lic if isinstance(lic, str) else (lic[0] if lic else "")
        if not lic:  # メタデータに無ければ分類タグかライセンス文の1行目から
            cls = [c.split(" :: ")[-1] for c in d.metadata.get_all("Classifier") or [] if c.startswith("License")]
            lic = cls[0] if cls else next(iter(t.split("\n")[1] for t in license_texts(d)), "")
        parts += [RULE, f"{d.metadata['Name']} {d.version}  -  {lic}".rstrip(" -"), f"用途: {use}", RULE]
        texts = license_texts(d) or [FALLBACK.get(name, "(ライセンス文が見つかりませんでした)")]
        parts += ["\n\n".join(texts), ""]
    parts += [RULE, "Microsoft Edge WebView2 SDK (Microsoft.Web.WebView2.Core.dll / WinForms.dll / WebView2Loader.dll)",
              "用途: アプリのウィンドウ表示 (pywebview に同梱)", RULE, WEBVIEW2, ""]
    text = "\n".join(parts).replace("\r\n", "\n").replace("\r", "\n")  # 元ファイルの改行コードがまちまちなので揃える
    OUT.write_text(text.replace("\n", "\r\n"), encoding="utf-8-sig", newline="")
    print("書き出しました:", OUT)


if __name__ == "__main__":
    main()
