#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import sys
import zipfile


ROOT = Path(__file__).resolve().parent
EGG_PATH = ROOT / "egg-angels-fall-first.json"
INSTALL_PATH = ROOT / "install.sh"
START_PATH = ROOT / "start.sh"
ARCHIVE_NAME = "angels-fall-first-pterodactyl-egg.zip"
FIXED_ZIP_TIME = (2026, 11, 18, 0, 0, 0)
START_MARKER = "AFF_START_SH"

SOURCE_FILES = (
    ".gitignore",
    "LICENSE",
    "README.md",
    "THIRD_PARTY_NOTICES.md",
    "build_artifacts.py",
    "egg-angels-fall-first.json",
    "install.sh",
    "start.sh",
    "tests/__init__.py",
    "tests/test_egg.py",
)
ARCHIVE_FILES = SOURCE_FILES + ("SHA256SUMS",)
EXECUTABLE_FILES = {"build_artifacts.py", "install.sh", "start.sh"}

MAPS = (
    "AFF-Anaster-Territories",
    "AFF-Ascella",
    "AFF-Darsalaam-Territories",
    "AFF-Darsalaam",
    "AFF-Delta-Territories",
    "AFF-Errah-Territories",
    "AFF-Errah",
    "AFF-EyeOfGod-Territories",
    "AFF-Fortress-Conquest",
    "AFF-Fortress-Territories",
    "AFF-Fortress",
    "AFF-Igneous",
    "AFF-Irega-Territories",
    "AFF-Irega",
    "AFF-Ixion-Territories",
    "AFF-Ixion",
    "AFF-Kerrat-Territories",
    "AFF-Kerrat",
    "AFF-LaCroix-Territories",
    "AFF-LaCroix",
    "AFF-Lellebah-Territories",
    "AFF-Lukather-Territories",
    "AFF-Lukather",
    "AFF-Majuro-Territories",
    "AFF-Mazikeen-Territories",
    "AFF-Meudeverre-Territories",
    "AFF-Meudeverre",
    "AFF-Mirnoide",
    "AFF-NB3345-Territories",
    "AFF-Ossidiace-Territories",
    "AFF-Plains-Territories",
    "AFF-Plains",
    "AFF-Poranti",
    "AFF-STR-space",
    "AFF-STR",
    "AFF-Tannhauser",
    "AFF-Universe",
)


def read_text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def variable(
    env_variable: str,
    title: str,
    description: str,
    default: str,
    rules: str,
    *,
    visible: bool = True,
    editable: bool = True,
) -> dict[str, object]:
    return {
        "name": title,
        "description": description,
        "env_variable": env_variable,
        "default_value": default,
        "user_viewable": visible,
        "user_editable": editable,
        "rules": rules,
        "field_type": "text",
    }


def variables() -> list[dict[str, object]]:
    map_csv = ",".join(MAPS)
    return [
        variable(
            "SERVER_NAME",
            "Server Name",
            "Public server name written to [AFFGame.AFFGameReplicationInfo]. Quotes, control characters, and path separators are rejected at runtime.",
            "Angels Fall First Server",
            "required|string|max:80",
        ),
        variable(
            "SERVER_DESCRIPTION",
            "Server Description",
            "Public server description written to [AFFGame.AFFGameReplicationInfo].",
            "Community Angels Fall First server",
            "required|string|max:255",
        ),
        variable(
            "MOTD",
            "Message Of The Day",
            "Message shown to joining players; written as MessageOfTheDay in [AFFGame.AFFGameReplicationInfo].",
            "Welcome to Angels Fall First!",
            "required|string|max:255",
        ),
        variable(
            "ADMIN_PASSWORD",
            "Admin Password",
            "Required in-game administrator password for AdminLogin. No default is supplied; use a unique value of at least 8 characters.",
            "",
            "required|string|min:8|max:64|not_in:password,changeme,admin123,admin,password1",
        ),
        variable(
            "DEFAULT_MAP",
            "Default Map",
            "Initial map. The allowlist exactly matches the map packages observed in App 407480 build 25627237.",
            "AFF-Ixion",
            f"required|string|in:{map_csv}",
        ),
        variable(
            "MAP_CYCLE",
            "Map Cycle",
            "Comma-separated installed map-package tokens with no spaces or quotes; converted to GameSpecificMapCycles in [UTGame.UTGame].",
            "AFF-Ixion,AFF-Irega,AFF-Errah,AFF-Fortress,AFF-Plains,AFF-Meudeverre",
            "required|string|max:512|regex:/\\A[A-Za-z0-9-]+(?:,[A-Za-z0-9-]+)*\\z/",
        ),
        variable(
            "QUERY_PORT",
            "Query Port",
            "Steam query UDP port. Add this exact port as a separate Pterodactyl allocation; it must differ from the primary SERVER_PORT allocation.",
            "27015",
            "required|integer|between:1024,65535",
        ),
        variable(
            "MAX_PLAYERS",
            "Maximum Players",
            "Maximum joining players from the documented MaxPlayers URL option.",
            "32",
            "required|integer|between:2,64",
        ),
        variable(
            "NUM_PLAY",
            "Minimum Bodies",
            "Minimum combined human and bot body count from NumPlay. The runtime also requires this not to exceed Maximum Players.",
            "16",
            "required|integer|between:0,64",
        ),
        variable(
            "BALANCE_BOTS",
            "Balance Bots",
            "Add bots to keep teams balanced. Accepted values are true and false.",
            "true",
            "required|string|in:true,false",
        ),
        variable(
            "AI_LEVEL",
            "AI Level",
            "Bot difficulty from 1 through 5.",
            "3",
            "required|integer|between:1,5",
        ),
        variable(
            "BRIEFING_TIME",
            "Briefing Time",
            "Briefing phase duration in seconds. Zero skips the briefing.",
            "60",
            "required|integer|min:0|max:2147483647",
        ),
        variable(
            "RANDOM_COMMANDER",
            "Random Commander",
            "Automatically assign command to Milnet AI. Accepted values are true and false.",
            "true",
            "required|string|in:true,false",
        ),
        variable(
            "RED_STARTING_TICKETS",
            "Red Starting Tickets",
            "Starting tickets for the ULA team; minimum 10.",
            "500",
            "required|integer|min:10|max:2147483647",
        ),
        variable(
            "BLUE_STARTING_TICKETS",
            "Blue Starting Tickets",
            "Starting tickets for the AIA team; minimum 10.",
            "500",
            "required|integer|min:10|max:2147483647",
        ),
        variable(
            "FRIENDLY_FIRE",
            "Friendly Fire",
            "Friendly-fire percentage from 0 through 100. The developer warns this option was not extensively tested.",
            "0",
            "required|integer|between:0,100",
        ),
        variable(
            "AFF_AUTO_UPDATE",
            "Auto Update",
            "1 asks start.sh to run a supervised anonymous Windows SteamCMD update before launch; 0 starts the installed build.",
            "1",
            "required|integer|in:0,1",
        ),
        variable(
            "VALIDATE",
            "Validate On Update",
            "1 adds SteamCMD validate during automatic updates; 0 performs the normal update check.",
            "0",
            "required|integer|in:0,1",
        ),
        variable(
            "STEAM_APP_ID",
            "Steam App ID",
            "Fixed Angels Fall First Dedicated Server App ID.",
            "407480",
            "required|string|in:407480",
            visible=False,
            editable=False,
        ),
        variable(
            "STEAM_USER",
            "Steam User",
            "The dedicated server installs anonymously.",
            "anonymous",
            "required|string|in:anonymous",
            visible=False,
            editable=False,
        ),
        variable(
            "WINDOWS_INSTALL",
            "Windows Platform",
            "Forces the Windows Steam depot required by the Wine runtime.",
            "1",
            "required|string|in:1",
            visible=False,
            editable=False,
        ),
        variable(
            "WINETRICKS_RUN",
            "Wine Runtime Components",
            "Disables the Wine image entrypoint hook; start.sh conditionally installs vcrun2022 under its supervisor.",
            "",
            "nullable|string|max:0",
            visible=False,
            editable=False,
        ),
        variable(
            "AUTO_UPDATE",
            "Image Auto Update",
            "Fixed off so the Wine image entrypoint cannot update outside start.sh supervision.",
            "0",
            "required|integer|in:0",
            visible=False,
            editable=False,
        ),
        variable(
            "WINEARCH",
            "Wine Architecture",
            "Fixed 64-bit Wine prefix architecture.",
            "win64",
            "required|string|in:win64",
            visible=False,
            editable=False,
        ),
        variable(
            "WINEDEBUG",
            "Wine Debug",
            "Suppresses routine Wine debug channels.",
            "-all",
            "required|string|in:-all",
            visible=False,
            editable=False,
        ),
        variable(
            "WINEPREFIX",
            "Wine Prefix",
            "Persistent Wine prefix used by the runtime image.",
            "/home/container/.wine",
            "required|string|in:/home/container/.wine",
            visible=False,
            editable=False,
        ),
    ]


def egg_document(install_source: str) -> dict[str, object]:
    return {
        "_comment": "DO NOT EDIT: generated deterministically from the readable sources in this repository.",
        "meta": {"version": "PTDL_v2", "update_url": None},
        "exported_at": "2026-11-18T00:00:00Z",
        "name": "Angels Fall First Dedicated Server",
        "author": "community@example.invalid",
        "description": "Community PTDL_v2 egg for Angels Fall First Dedicated Server App 407480 under Wine. Wine 11 launch, map load, UDP listeners, direct A2S query, and log readiness were observed; real-client acceptance remains unverified.",
        "features": ["steam_disk_space"],
        "docker_images": {
            "Wine Latest (Pelican Eggs)": "ghcr.io/pelican-eggs/yolks:wine_latest"
        },
        "file_denylist": [],
        "startup": "bash ./start.sh",
        "config": {
            "files": json.dumps({}, separators=(",", ":")),
            "startup": json.dumps({"done": "[AFF-EGG] SERVER READY 8f31c2"}, separators=(",", ":")),
            "logs": json.dumps(
                {"custom": True, "location": "AFFGame/Logs/Launch.log"},
                separators=(",", ":"),
            ),
            "stop": "^C",
        },
        "scripts": {
            "installation": {
                "script": install_source,
                "container": "ghcr.io/pelican-eggs/installers:debian",
                "entrypoint": "bash",
            }
        },
        "variables": variables(),
    }


def rendered_egg(install_source: str) -> str:
    return json.dumps(egg_document(install_source), indent=4, ensure_ascii=False) + "\n"


def render_install(existing: str, start_source: str) -> str:
    opener = f"cat > \"$START_TMP\" <<'{START_MARKER}'\n"
    body_start = existing.find(opener)
    if body_start < 0:
        raise ValueError(f"install.sh is missing the quoted {START_MARKER} heredoc")
    body_start += len(opener)
    closing = f"{START_MARKER}\n"
    body_end = existing.find(closing, body_start)
    if body_end < 0:
        raise ValueError(f"install.sh is missing the closing {START_MARKER} marker")
    if START_MARKER in start_source:
        raise ValueError(f"start.sh contains reserved marker {START_MARKER}")
    return existing[:body_start] + start_source + existing[body_end:]


def extract_start(install_source: str) -> str:
    opener = f"cat > \"$START_TMP\" <<'{START_MARKER}'\n"
    body_start = install_source.index(opener) + len(opener)
    body_end = install_source.index(f"\n{START_MARKER}\n", body_start) + 1
    return install_source[body_start:body_end]


def synchronize() -> None:
    start_source = read_text("start.sh")
    install_source = render_install(read_text("install.sh"), start_source)
    INSTALL_PATH.write_text(install_source, encoding="utf-8", newline="\n")
    EGG_PATH.write_text(rendered_egg(install_source), encoding="utf-8", newline="\n")
    INSTALL_PATH.chmod(0o755)
    START_PATH.chmod(0o755)


def validate_sources() -> None:
    install_source = read_text("install.sh")
    start_source = read_text("start.sh")
    if extract_start(install_source) != start_source:
        raise ValueError("start.sh is not embedded byte-for-byte in install.sh; run build_artifacts.py --sync")
    expected_egg = rendered_egg(install_source)
    if not EGG_PATH.is_file() or EGG_PATH.read_text(encoding="utf-8") != expected_egg:
        raise ValueError("egg-angels-fall-first.json is out of sync; run build_artifacts.py --sync")

    for name in SOURCE_FILES:
        path = ROOT / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"required source is missing or unsafe: {name}")
        if b"\r" in path.read_bytes():
            raise ValueError(f"CR bytes are not permitted: {name}")


def write_source_checksums() -> None:
    lines = []
    for name in SOURCE_FILES:
        digest = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}")
    (ROOT / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


def write_archive(output_dir: Path) -> tuple[Path, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise ValueError("output directory must be a real directory")
    archive_path = output_dir / ARCHIVE_NAME
    if archive_path.is_symlink():
        raise ValueError("refusing symlinked archive destination")
    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for name in ARCHIVE_FILES:
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
            info.create_system = 3
            permissions = 0o755 if name in EXECUTABLE_FILES else 0o644
            info.external_attr = (stat.S_IFREG | permissions) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (ROOT / name).read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path = output_dir / "SHA256SUMS"
    if checksum_path.is_symlink():
        raise ValueError("refusing symlinked checksum destination")
    checksum_path.write_text(f"{digest}  {ARCHIVE_NAME}\n", encoding="ascii", newline="\n")
    return archive_path, digest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build deterministic Angels Fall First egg artifacts.")
    parser.add_argument("--sync", action="store_true", help="synchronize install.sh and the generated egg first")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist",
        help="artifact directory (default: ./dist)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.sync:
            synchronize()
        validate_sources()
        write_source_checksums()
        archive_path, digest = write_archive(args.output_dir)
    except (OSError, ValueError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"created {archive_path}")
    print(f"sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
