#!/usr/bin/env python3
"""
İndirim Avcısı - yerel tarayıcı.

GitHub Actions'ın IP'lerini engelleyen mağazaları ("local_only": true) kendi
bilgisayarında tarar ve sonucu "data" dalına gönderir. Diğer mağazaların
indirimleri (Actions'ın yazdıkları) aynen korunur.

Windows Görev Zamanlayıcı'da 30 dakikada bir şöyle çalıştırılır:
    pythonw.exe scraper\\local_run.py

Kayıtlar repo kökündeki .local-data.log dosyasına yazılır.
"""
from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WORK = os.path.join(REPO, ".local-data")
LOG = os.path.join(REPO, ".local-data.log")
LOG_KEEP_LINES = 3000
ATTEMPTS = 3


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if sys.stdout:  # pythonw'de konsol yok
        print(line, flush=True)


def trim_log() -> None:
    try:
        with open(LOG, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > LOG_KEEP_LINES:
            with open(LOG, "w", encoding="utf-8") as f:
                f.writelines(lines[-LOG_KEEP_LINES:])
    except FileNotFoundError:
        pass


def git(*args: str, cwd: str = REPO, binary: bool = False, check: bool = True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout if binary else r.stdout.decode("utf-8", "replace").strip()


def remove_tree(path: str) -> None:
    """Windows'ta git'in salt okunur nesne dosyaları da dahil klasörü siler."""
    def force(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if os.path.exists(path):
        kw = {"onexc": force} if sys.version_info >= (3, 12) else {"onerror": force}
        shutil.rmtree(path, **kw)


def fresh_workdir(sha: str | None) -> None:
    """data dalının o anki içeriğini WORK klasörüne çıkarır."""
    remove_tree(WORK)
    os.makedirs(WORK)
    if sha:
        tar = git("archive", "--format=tar", sha, binary=True)
        with tarfile.open(fileobj=io.BytesIO(tar)) as t:
            t.extractall(WORK, filter="data")


def run_once() -> bool:
    git("fetch", "-q", "origin", "data", check=False)
    sha = git("rev-parse", "--verify", "-q", "FETCH_HEAD", check=False) or None
    fresh_workdir(sha)

    r = subprocess.run([sys.executable, os.path.join(HERE, "scrape.py"), "--out", WORK, "--local"],
                       capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        if line.strip():
            log("  " + line)
    if r.returncode not in (0, 2):  # 2 = hiçbir mağaza çalışmadı; durum yine de yayımlanır
        log("  tarayıcı hata verdi: " + r.stderr.decode("utf-8", "replace").strip()[-800:])
        return True  # tekrar denemenin anlamı yok
    if not os.path.exists(os.path.join(WORK, "deals.json")):
        log("  deals.json üretilmedi")
        return True

    remote = git("remote", "get-url", "origin")
    remove_tree(os.path.join(WORK, ".git"))
    git("init", "-q", "-b", "data", cwd=WORK)
    git("add", "-A", cwd=WORK)
    git("-c", "user.name=indirim-avcisi-yerel", "-c", "user.email=indirim-avcisi-yerel@users.noreply.github.com",
        "commit", "-q", "-m", f"İndirimler güncellendi (yerel) {datetime.now().astimezone():%Y-%m-%dT%H:%M:%S%z}",
        cwd=WORK)
    # Actions arada yazdıysa push reddedilir: üstüne yazmak yerine baştan alıp tekrar dene
    lease = f"--force-with-lease=data:{sha}" if sha else "--force"
    r = subprocess.run(["git", "push", "-q", lease, remote, "HEAD:data"], cwd=WORK, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode == 0:
        log("  data dalına gönderildi")
        return True
    log("  gönderilemedi (data dalı bu arada değişmiş olabilir): " + r.stderr.decode("utf-8", "replace").strip()[-300:])
    return False


def main() -> int:
    trim_log()
    log("Yerel tarama başladı")
    # Kodun güncel sürümünü al (sources.json'a yeni kategori eklenmiş olabilir)
    try:
        git("pull", "-q", "--ff-only")
    except RuntimeError as e:
        log(f"  kod güncellenemedi, mevcut sürümle devam: {e}")
    for attempt in range(1, ATTEMPTS + 1):
        try:
            if run_once():
                return 0
        except Exception as e:
            log(f"  hata: {type(e).__name__}: {e}")
        if attempt < ATTEMPTS:
            time.sleep(30)
    log("  vazgeçildi; 30 dakika sonra tekrar denenecek")
    return 1


if __name__ == "__main__":
    sys.exit(main())
