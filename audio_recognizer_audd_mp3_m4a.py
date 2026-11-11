#!/usr/bin/env python3
"""
Identify M4A and MP3 tracks with AudD and AcoustID audio recognition, then MusicBrainz;
fill tags, embed artwork and auto-rename.

No audio is re-encoded. No backup files are created.
Default is preview; pass --apply to write tags.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tempfile
from pathlib import Path

import requests
from mutagen.mp4 import MP4, MP4Cover
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TPE2, TDRC, APIC

ACOUSTID_LOOKUP = "https://api.acoustid.org/v2/lookup"
AUDD_LOOKUP = "https://api.audd.io/"
MB_ROOT = "https://musicbrainz.org/ws/2"
CAA_ROOT = "https://coverartarchive.org"
USER_AGENT = "M4A-Audio-Fingerprint-Tagger/1.0 (personal music library)"
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


def fingerprint_file(path: Path, fpcalc: str, length: int):
    """Run Chromaprint fpcalc and return (duration, fingerprint)."""
    proc = subprocess.run(
        [fpcalc, "-json", "-length", str(length), str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180
    )
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "fpcalc failed").strip()
        raise RuntimeError(message)
    try:
        data = json.loads(proc.stdout)
        return int(float(data["duration"])), data["fingerprint"]
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot parse fpcalc output: {proc.stdout[:500]}") from exc


def acoustid_lookup(api_key: str, duration: int, fingerprint: str):
    response = session.post(
        ACOUSTID_LOOKUP,
        data={
            "client": api_key,
            "duration": duration,
            "fingerprint": fingerprint,
            "meta": "recordings releases releasegroups",
            "format": "json",
        },
        timeout=40,
    )
    if response.status_code >= 400:
        # AcoustID returns a useful JSON error message with many 400 responses.
        try:
            details = response.json()
        except ValueError:
            details = response.text[:1000]
        raise RuntimeError(f"AcoustID HTTP {response.status_code}: {details}")
    data = response.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"AcoustID returned status: {data.get('status')}")
    return data.get("results", [])


def audd_lookup(api_token: str, path: Path):
    """Recognize a local audio file with AudD (standard API: up to 10 MB)."""
    upload_path = path
    temp_path = None
    try:
        if path.stat().st_size > 10 * 1024 * 1024:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                print("  [AUDD SKIPPED] Файл больше 10 МБ, а ffmpeg не найден.")
                return None
            fd, temp_name = tempfile.mkstemp(suffix=".m4a")
            os.close(fd)
            temp_path = Path(temp_name)
            proc = subprocess.run(
                [ffmpeg, "-y", "-i", str(path), "-t", "12", "-vn",
                 "-c:a", "aac", "-b:a", "128k", str(temp_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=90,
            )
            if proc.returncode != 0 or not temp_path.exists() or temp_path.stat().st_size == 0:
                print("  [AUDD SKIPPED] Не удалось подготовить 12-секундный фрагмент.")
                return None
            upload_path = temp_path

        if upload_path.stat().st_size > 10 * 1024 * 1024:
            print("  [AUDD SKIPPED] Фрагмент всё ещё превышает лимит 10 МБ.")
            return None

        with upload_path.open("rb") as audio_file:
            response = session.post(
                AUDD_LOOKUP,
                data={"api_token": api_token, "return": "musicbrainz"},
                files={"file": (
                    upload_path.name,
                    audio_file,
                    "audio/mpeg" if upload_path.suffix.lower() == ".mp3" else "audio/mp4",
                )},
                timeout=90,
            )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "success":
            raise RuntimeError(data.get("error", {}).get("error_message") or str(data))
        result = data.get("result")
        if not result or not result.get("title"):
            return None
        artist = result.get("artist") or ""
        title = result.get("title") or ""
        print(f"  [AUDD] Распознано: {artist or 'Исполнитель неизвестен'} — {title}")
        return result
    finally:
        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def musicbrainz_search_by_metadata(title: str, artist: str):
    """Find a MusicBrainz recording using AudD's recognized title and artist."""
    query = f'recording:"{title}"'
    if artist:
        query += f' AND artist:"{artist}"'
    response = session.get(
        f"{MB_ROOT}/recording/",
        params={"query": query, "fmt": "json", "limit": 10},
        timeout=30,
    )
    response.raise_for_status()
    recordings = response.json().get("recordings") or []
    if not recordings or not recordings[0].get("id"):
        return None
    best = recordings[0]
    print(f"  [MUSICBRAINZ] Найдена запись: {best.get('title', title)} "
          f"(совпадение {best.get('score', 0)}%)")
    return best["id"]


def collect_candidates(results):
    candidates = {}
    for result in results:
        score = float(result.get("score", 0))
        for rec in result.get("recordings", []):
            rec_id = rec.get("id")
            if not rec_id:
                continue
            artists = rec.get("artists") or []
            artist = ", ".join(a.get("name", "") for a in artists if a.get("name"))
            release_groups = rec.get("releasegroups") or []
            releases = rec.get("releases") or []
            album = releases[0].get("title") if releases else (
                release_groups[0].get("title") if release_groups else ""
            )
            item = {
                "score": score,
                "recording_id": rec_id,
                "title": rec.get("title", ""),
                "artist": artist,
                "album": album or "",
            }
            old = candidates.get(rec_id)
            if old is None or score > old["score"]:
                candidates[rec_id] = item
    return sorted(candidates.values(), key=lambda x: x["score"], reverse=True)


def choose_candidate(candidates, min_score):
    """Automatically choose the highest-scoring sound match; never prompt."""
    candidates = [c for c in candidates if c["score"] >= min_score]
    if not candidates:
        return None
    match = candidates[0]
    print(
        f"  Автоматически выбрано по звучанию: "
        f"{match['artist'] or 'Исполнитель неизвестен'} — "
        f"{match['title'] or 'Название неизвестно'} ({match['score']:.0%})"
    )
    return match


def musicbrainz_search_by_title(filename: str):
    """Find the best MusicBrainz recording using the original file name."""
    # Strip common leading track numbers such as "01 - " or "03. ".
    query_title = re.sub(r"^\s*\d{1,3}\s*[-._)]\s*", "", filename).strip()
    query_title = Path(query_title).stem.strip()
    if not query_title:
        return None

    response = session.get(
        f"{MB_ROOT}/recording/",
        params={"query": query_title, "fmt": "json", "limit": 10},
        timeout=30,
    )
    response.raise_for_status()
    recordings = response.json().get("recordings") or []
    if not recordings:
        return None

    # MusicBrainz returns its relevance score in descending order.
    best = recordings[0]
    if int(best.get("score", 0)) < 70 or not best.get("id"):
        return None

    print(
        f"  [TITLE SEARCH] Найдено по имени файла: "
        f"{best.get('title', 'Без названия')} "
        f"(совпадение {best.get('score', 0)}%)"
    )
    return best["id"]


def safe_filename_part(value: str) -> str:
    """Remove characters that Windows does not allow in file names."""
    value = re.sub(r'[<>:"/\\\\|?*]', "", value or "")
    value = re.sub(r"\\s+", " ", value).strip().rstrip(".")
    return value


def rename_track(path: Path, recording: dict, apply: bool) -> Path:
    """Rename to 'Artist - Title.m4a', avoiding overwriting existing files."""
    artist_credit = recording.get("artist-credit") or []
    artist = "".join(
        part.get("name", "") if isinstance(part, dict) else str(part)
        for part in artist_credit
    ).strip()
    title = (recording.get("title") or "").strip()
    base = " - ".join(part for part in (
        safe_filename_part(artist), safe_filename_part(title)
    ) if part)

    if not base:
        print("  [RENAME SKIPPED] Не удалось сформировать имя файла.")
        return path

    target = path.with_name(base + path.suffix.lower())
    if target == path:
        print(f"  [RENAME] Имя уже правильное: {target.name}")
        return path

    # Never overwrite another track.
    if target.exists():
        stem = target.stem
        counter = 2
        while target.with_name(f"{stem} ({counter}){target.suffix}").exists():
            counter += 1
        target = target.with_name(f"{stem} ({counter}){target.suffix}")

    if not apply:
        print(f"  PREVIEW: файл будет переименован: {path.name} -> {target.name}")
        return path

    path.rename(target)
    print(f"  [RENAMED] {path.name} -> {target.name}")
    return target


def musicbrainz_recording(recording_id):
    url = f"{MB_ROOT}/recording/{recording_id}"
    response = session.get(
        url,
        params={"fmt": "json", "inc": "artists+releases"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def get_release_details(recording):
    releases = recording.get("releases") or []
    # Prefer an official album release, then any release with a usable title.
    releases.sort(key=lambda r: (
        0 if (r.get("status") or "").casefold() == "official" else 1,
        0 if (r.get("release-group") or {}).get("type") == "Album" else 1,
        r.get("date") or "9999",
    ))
    for release in releases:
        release_id = release.get("id")
        if not release_id:
            continue
        try:
            art = session.get(
                f"{CAA_ROOT}/release/{release_id}/front-500",
                timeout=25,
                allow_redirects=True,
            )
            if art.status_code == 200 and art.headers.get("Content-Type", "").lower().startswith("image/"):
                ctype = art.headers.get("Content-Type", "").lower()
                fmt = MP4Cover.FORMAT_PNG if "png" in ctype else MP4Cover.FORMAT_JPEG
                return release, MP4Cover(art.content, imageformat=fmt)
        except requests.RequestException:
            pass
        time.sleep(1.05)
    return (releases[0], None) if releases else (None, None)


def text_value(obj, key):
    values = obj.get(key) or []
    return str(values[0]).strip() if values else ""


def write_tags(path, recording, release, cover, apply, overwrite):
    """Write metadata to MP3 (ID3) or M4A (MP4 atoms), without re-encoding."""
    artist_credit = recording.get("artist-credit") or []
    artist = "".join(
        part.get("name", "") if isinstance(part, dict) else str(part)
        for part in artist_credit
    ).strip()
    title = recording.get("title", "")
    album = (release or {}).get("title", "")
    album_artist_credit = (release or {}).get("artist-credit") or artist_credit
    album_artist = "".join(
        part.get("name", "") if isinstance(part, dict) else str(part)
        for part in album_artist_credit
    ).strip()
    date = (release or {}).get("date", "")

    if path.suffix.lower() == ".mp3":
        audio = MP3(path, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        values = {"TIT2": title, "TPE1": artist, "TALB": album,
                  "TPE2": album_artist, "TDRC": date}

        if not apply:
            print("  PREVIEW: будут заполнены: " + ", ".join(
                f"{k}={v}" for k, v in values.items() if v
            ))
            print("  Обложка: " + ("найдена" if cover else "не найдена"))
            return

        changed = False
        frame_types = {"TIT2": TIT2, "TPE1": TPE1, "TALB": TALB,
                       "TPE2": TPE2, "TDRC": TDRC}
        for key, value in values.items():
            if value and (overwrite or not tags.getall(key)):
                tags.delall(key)
                tags.add(frame_types[key](encoding=3, text=value))
                changed = True

        if cover and (overwrite or not tags.getall("APIC")):
            tags.delall("APIC")
            mime = "image/png" if getattr(cover, "imageformat", None) == MP4Cover.FORMAT_PNG else "image/jpeg"
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=bytes(cover)))
            changed = True

        if changed:
            audio.save()
            print("  [SAVED] Метаданные записаны в MP3 без перекодирования.")
        else:
            print("  [UNCHANGED] Поля уже заполнены; изменений нет.")
        return

    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    values = {"\xa9nam": title, "\xa9ART": artist, "\xa9alb": album,
              "aART": album_artist, "\xa9day": date}

    if not apply:
        print("  PREVIEW: будут заполнены: " + ", ".join(
            f"{k}={v}" for k, v in values.items() if v
        ))
        print("  Обложка: " + ("найдена" if cover else "не найдена"))
        return

    changed = False
    for key, value in values.items():
        if value and (overwrite or not tags.get(key)):
            tags[key] = [value]
            changed = True
    if cover and (overwrite or not tags.get("covr")):
        tags["covr"] = [cover]
        changed = True

    if changed:
        audio.save()
        print("  [SAVED] Метаданные записаны в M4A без перекодирования.")
    else:
        print("  [UNCHANGED] Поля уже заполнены; изменений нет.")


def main():
    parser = argparse.ArgumentParser(
        description="Recognize M4A and MP3 music by sound and fill metadata/artwork."
    )
    parser.add_argument("source", type=Path, help="Folder with M4A/MP3 files")
    parser.add_argument("audd_command", nargs="?", choices=["aud_off"], help="aud_off отключает распознавание AudD")
    parser.add_argument("--aud-off", action="store_true", help="Disable AudD recognition")
    parser.add_argument("--apply", action="store_true", help="Write metadata; default is preview")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing tags and cover")
    parser.add_argument("--fpcalc", default="fpcalc", help="Path to fpcalc.exe if it is not in PATH")
    parser.add_argument("--length", type=int, default=120, help="Seconds used for fingerprint (default: 120)")
    parser.add_argument("--min-score", type=float, default=0.70, help="Minimum AcoustID match score (default: 0.70)")
    args = parser.parse_args()

    if not args.source.is_dir():
        raise SystemExit(f"Папка не найдена: {args.source}")

    audd_disabled = args.aud_off or args.audd_command == "aud_off"
    audd_token = ""
    if audd_disabled:
        print("[AUDD OFF] Распознавание через AudD отключено командой aud_off.")
    else:
        audd_token = os.environ.get("AUDD_API_TOKEN", "").strip()
        if not audd_token:
            print("AudD: введи API Token (Enter — пропустить AudD).")
            audd_token = input("AudD API Token: ").strip()

    api_key = os.environ.get("ACOUSTID_API_KEY", "").strip()
    if not api_key:
        print("ВНИМАНИЕ: вводимый ключ будет виден на экране.")
        api_key = input("Вставь AcoustID Application API Key: ").strip()
    if not api_key:
        raise SystemExit("Не указан AcoustID API key.")

    try:
        subprocess.run([args.fpcalc, "-version"], capture_output=True, timeout=10)
    except FileNotFoundError:
        raise SystemExit(
            "Не найден fpcalc.exe. Установи Chromaprint и добавь fpcalc.exe в PATH "
            "или укажи путь параметром --fpcalc."
        )

    supported_extensions = {".m4a", ".mp3"}
    files = sorted(
        p for p in args.source.rglob("*")
        if p.is_file() and p.suffix.lower() in supported_extensions
    )
    if not files:
        print("Файлы M4A или MP3 не найдены.")
        return

    print(f"Найдено файлов: {len(files)}")
    print("РЕЖИМ: " + ("ЗАПИСЬ ТЕГОВ (без резервных копий)" if args.apply else "ПРОСМОТР — файлы не изменяются"))
    print("Аудио не перекодируется. Резервные копии не создаются.\n")

    stats = {"updated": 0, "preview": 0, "not_found": 0, "error": 0}
    for index, path in enumerate(files, 1):
        print(f"\n[{index}/{len(files)}] {path.name}")
        try:
            audd_result = None
            if audd_token:
                try:
                    audd_result = audd_lookup(audd_token, path)
                except requests.RequestException as exc:
                    print(f"  [AUDD ERROR] {exc}. Перехожу к AcoustID.")
                except Exception as exc:
                    print(f"  [AUDD ERROR] {type(exc).__name__}: {exc}. Перехожу к AcoustID.")

            recording_id = None
            recording = None
            release = None
            cover = None
            if audd_result:
                try:
                    recording_id = musicbrainz_search_by_metadata(
                        audd_result.get("title", ""), audd_result.get("artist", "")
                    )
                except requests.RequestException as exc:
                    print(f"  [MUSICBRAINZ ERROR] {exc}")
                if recording_id:
                    recording = musicbrainz_recording(recording_id)
                    time.sleep(1.05)
                    release, cover = get_release_details(recording)
                else:
                    # AudD identified the song, even if MusicBrainz has no matching entry.
                    artist = audd_result.get("artist") or ""
                    recording = {
                        "title": audd_result.get("title", ""),
                        "artist-credit": ([{"name": artist}] if artist else []),
                    }
                    release = {
                        "title": audd_result.get("album", "") or "",
                        "date": audd_result.get("release_date", "") or "",
                        "artist-credit": ([{"name": artist}] if artist else []),
                    }
                    print("  [AUDD ONLY] Использую данные AudD; запись MusicBrainz не найдена.")

            if recording is None:
                duration, fingerprint = fingerprint_file(path, args.fpcalc, args.length)
                results = acoustid_lookup(api_key, duration, fingerprint)
                candidates = collect_candidates(results)
                match = choose_candidate(candidates, args.min_score)

                if match:
                    print(f"  Выбрано по звучанию: {match['artist']} — {match['title']} ({match['score']:.0%})")
                    recording_id = match["recording_id"]
                else:
                    print("  [FALLBACK] По звучанию не найдено. Пробую поиск по названию файла...")
                    recording_id = musicbrainz_search_by_title(path.name)
                    if not recording_id:
                        print("  [NOT FOUND] Не удалось найти трек ни одной системой.")
                        stats["not_found"] += 1
                        time.sleep(0.4)
                        continue

                recording = musicbrainz_recording(recording_id)
                time.sleep(1.05)  # MusicBrainz asks clients to stay near 1 request/second.
                release, cover = get_release_details(recording)
            write_tags(path, recording, release, cover, args.apply, args.overwrite)
            rename_track(path, recording, args.apply)
            stats["updated" if args.apply else "preview"] += 1
        except requests.HTTPError as exc:
            print(f"  [HTTP ERROR] {exc}")
            stats["error"] += 1
        except subprocess.TimeoutExpired:
            print("  [ERROR] fpcalc превысил лимит времени.")
            stats["error"] += 1
        except Exception as exc:
            print(f"  [ERROR] {type(exc).__name__}: {exc}")
            stats["error"] += 1
        time.sleep(1.05)  # Keep AcoustID requests comfortably below its rate limit.

    print("\nИТОГ: " + ", ".join(f"{k}={v}" for k, v in stats.items()))


if __name__ == "__main__":
    main()
