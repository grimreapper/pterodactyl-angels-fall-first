from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import time
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
EGG_PATH = ROOT / "egg-angels-fall-first.json"
INSTALL_PATH = ROOT / "install.sh"
START_PATH = ROOT / "start.sh"
READY_TOKEN = "[AFF-EGG] SERVER READY 8f31c2"

SPEC = importlib.util.spec_from_file_location("build_artifacts", ROOT / "build_artifacts.py")
assert SPEC and SPEC.loader
build_artifacts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_artifacts)

EXPECTED_MAPS = {
    "AFF-Anaster-Territories", "AFF-Ascella", "AFF-Darsalaam-Territories", "AFF-Darsalaam",
    "AFF-Delta-Territories", "AFF-Errah-Territories", "AFF-Errah", "AFF-EyeOfGod-Territories",
    "AFF-Fortress-Conquest", "AFF-Fortress-Territories", "AFF-Fortress", "AFF-Igneous",
    "AFF-Irega-Territories", "AFF-Irega", "AFF-Ixion-Territories", "AFF-Ixion",
    "AFF-Kerrat-Territories", "AFF-Kerrat", "AFF-LaCroix-Territories", "AFF-LaCroix",
    "AFF-Lellebah-Territories", "AFF-Lukather-Territories", "AFF-Lukather",
    "AFF-Majuro-Territories", "AFF-Mazikeen-Territories", "AFF-Meudeverre-Territories",
    "AFF-Meudeverre", "AFF-Mirnoide", "AFF-NB3345-Territories", "AFF-Ossidiace-Territories",
    "AFF-Plains-Territories", "AFF-Plains", "AFF-Poranti", "AFF-STR-space", "AFF-STR",
    "AFF-Tannhauser", "AFF-Universe",
}


def load_egg() -> dict[str, object]:
    return json.loads(EGG_PATH.read_text(encoding="utf-8"))


def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, **kwargs,
    )


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_path(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size:
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path.name}")


def wait_for_text(path: Path, needle: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and needle in path.read_text(encoding="utf-8"):
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {needle}")


class EggDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.egg = load_egg()
        cls.install = INSTALL_PATH.read_text(encoding="utf-8")
        cls.start = START_PATH.read_text(encoding="utf-8")
        cls.variables = {item["env_variable"]: item for item in cls.egg["variables"]}  # type: ignore[index]

    def test_json_and_public_metadata(self) -> None:
        self.assertEqual(self.egg["meta"], {"version": "PTDL_v2", "update_url": None})
        self.assertEqual(self.egg["author"], "community@example.invalid")
        self.assertEqual(self.egg["exported_at"], "2026-11-18T00:00:00Z")
        self.assertEqual(self.egg["startup"], "bash ./start.sh")
        self.assertIn("direct A2S query", self.egg["description"])
        self.assertIn("real-client acceptance remains unverified", self.egg["description"])

    def test_ptdl_v2_config_fields_and_readiness(self) -> None:
        config = self.egg["config"]
        for key in ("files", "startup", "logs"):
            self.assertIsInstance(config[key], str)  # type: ignore[index]
            json.loads(config[key])  # type: ignore[index]
        self.assertEqual(json.loads(config["files"]), {})  # type: ignore[index]
        self.assertEqual(json.loads(config["startup"]), {"done": READY_TOKEN})  # type: ignore[index]
        self.assertEqual(
            json.loads(config["logs"]),  # type: ignore[index]
            {"custom": True, "location": "AFFGame/Logs/Launch.log"},
        )
        self.assertEqual(config["stop"], "^C")  # type: ignore[index]

    def test_embedded_scripts_are_byte_exact(self) -> None:
        embedded_install = self.egg["scripts"]["installation"]["script"]  # type: ignore[index]
        self.assertEqual(embedded_install, self.install)
        self.assertEqual(build_artifacts.extract_start(self.install), self.start)
        self.assertEqual(build_artifacts.render_install(self.install, self.start), self.install)

    def test_shell_syntax_for_all_script_copies(self) -> None:
        for path in (INSTALL_PATH, START_PATH):
            result = run(["bash", "-n", str(path)])
            self.assertEqual(result.returncode, 0, result.stderr)
        with tempfile.TemporaryDirectory(prefix=".test-embedded-", dir=ROOT) as temporary:
            embedded = Path(temporary) / "embedded-install.sh"
            embedded.write_text(
                self.egg["scripts"]["installation"]["script"],  # type: ignore[index]
                encoding="utf-8", newline="\n",
            )
            result = run(["bash", "-n", str(embedded)])
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_dependency_order_and_runtime_contract(self) -> None:
        for source in (self.install, self.start):
            login = source.index("+login anonymous")
            prerequisite = source.index("+app_update 1007", login)
            windows = source.index("+@sSteamCmdForcePlatformType windows", prerequisite)
            server = source.index("+app_update 407480", windows)
            self.assertLess(login, prerequisite)
            self.assertLess(prerequisite, windows)
            self.assertLess(windows, server)
            self.assertIn("appmanifest_1007.acf", source)
            self.assertIn("appmanifest_407480.acf", source)
            self.assertIn("Binaries/Win64/AFFGameServer.exe", source)

        self.assertIn('"OnlineSubsystemSteamworks.OnlineSubsystemSteamworks" "QueryPort"', self.start)
        self.assertIn('"-Port=$SERVER_PORT"', self.start)
        self.assertIn("' aff-game", self.start)
        self.assertIn('kill -STOP "$$"', self.start)
        self.assertIn("run_startup_command", self.start)
        for command in ('"SteamCMD update"', '"wineboot -u"', '"wineserver -w"', '"winetricks vcrun2022"'):
            self.assertIn(f"run_startup_command {command}", self.start)
        self.assertNotIn("exec wine", self.start)
        self.assertIn("trap 'handle_shutdown_signal INT' INT", self.start)
        self.assertIn("trap 'handle_shutdown_signal TERM' TERM", self.start)
        self.assertIn("GAME_GROUP_CONFIRMED=0", self.start)
        self.assertIn('kill -TERM -- "-$GAME_PGID"', self.start)
        self.assertIn("wineserver -k", self.start)
        self.assertIn('kill -KILL -- "-$GAME_PGID"', self.start)
        self.assertIn("coproc AFF_READINESS_RELAY", self.start)
        self.assertIn('exec {READINESS_WRITE_FD}> "/proc/self/fd/$relay_write_fd"', self.start)
        self.assertIn('trap \'\' USR1 USR2', self.start)
        self.assertNotIn("handle_readiness_signal", self.start)
        self.assertNotIn("kill -USR1", self.start)
        self.assertNotIn("kill -USR2", self.start)
        self.assertNotRegex(self.start, r"\b(?:pkill|killall)\b")
        self.assertNotRegex(self.start, r"(^|[;&|[:space:]])eval([;&|[:space:]]|$)")
        self.assertIn('getline wanted_value < "/dev/stdin"', self.start)
        self.assertNotIn("-v wanted_value", self.start)
        self.assertNotIn('${path}.tmp.', self.start)

    def test_exact_live_map_set_everywhere(self) -> None:
        self.assertEqual(len(EXPECTED_MAPS), 37)
        self.assertEqual(set(build_artifacts.MAPS), EXPECTED_MAPS)
        default_rules = self.variables["DEFAULT_MAP"]["rules"]
        self.assertIsInstance(default_rules, str)
        egg_maps = set(default_rules.split("in:", 1)[1].split(","))
        self.assertEqual(egg_maps, EXPECTED_MAPS)

        match = re.search(r"is_known_map\(\).*?case \"\$1\" in\s+([^\n]+)\)", self.start, re.S)
        self.assertIsNotNone(match)
        runtime_maps = set(match.group(1).strip().split("|"))  # type: ignore[union-attr]
        self.assertEqual(runtime_maps, EXPECTED_MAPS)
        self.assertIn('require_regular_file "$COOKED_ROOT/$DEFAULT_MAP.upk"', self.start)
        self.assertIn('require_regular_file "$COOKED_ROOT/$map_token.upk"', self.start)

    def test_variable_set_defaults_and_rules(self) -> None:
        required_operator = {
            "SERVER_NAME", "SERVER_DESCRIPTION", "MOTD", "ADMIN_PASSWORD",
            "DEFAULT_MAP", "MAP_CYCLE", "QUERY_PORT", "MAX_PLAYERS", "NUM_PLAY", "BALANCE_BOTS",
            "AI_LEVEL", "BRIEFING_TIME", "RANDOM_COMMANDER", "RED_STARTING_TICKETS",
            "BLUE_STARTING_TICKETS", "FRIENDLY_FIRE", "AFF_AUTO_UPDATE", "VALIDATE",
        }
        self.assertTrue(required_operator.issubset(self.variables))
        self.assertEqual(self.variables["AFF_AUTO_UPDATE"]["rules"], "required|integer|in:0,1")
        self.assertEqual(self.variables["VALIDATE"]["rules"], "required|integer|in:0,1")
        self.assertEqual(self.variables["QUERY_PORT"]["default_value"], "27015")
        self.assertEqual(self.variables["DEFAULT_MAP"]["default_value"], "AFF-Ixion")
        self.assertEqual(self.variables["ADMIN_PASSWORD"]["default_value"], "")
        self.assertIn("min:8", self.variables["ADMIN_PASSWORD"]["rules"])
        removed_secret_name = "GAME_" + "PASSWORD"
        self.assertNotIn(removed_secret_name, self.variables)
        for source in (self.start, self.install, json.dumps(self.egg)):
            self.assertNotIn(removed_secret_name, source)

        hidden = {
            "STEAM_APP_ID": "407480", "STEAM_USER": "anonymous", "WINDOWS_INSTALL": "1",
            "WINETRICKS_RUN": "", "AUTO_UPDATE": "0", "WINEARCH": "win64",
            "WINEDEBUG": "-all", "WINEPREFIX": "/home/container/.wine",
        }
        for name, default in hidden.items():
            self.assertEqual(self.variables[name]["default_value"], default)
            self.assertFalse(self.variables[name]["user_viewable"])
            self.assertFalse(self.variables[name]["user_editable"])
        self.assertEqual(self.variables["AUTO_UPDATE"]["rules"], "required|integer|in:0")
        self.assertEqual(self.egg["startup"], "bash ./start.sh")
        self.assertNotRegex(self.egg["startup"], r"\{\{|eval|AFF_AUTO_UPDATE")

    def test_reserved_panel_environment_names_are_excluded(self) -> None:
        reserved = {"SERVER_PORT", "SERVER_IP", "P_SERVER_UUID", "P_SERVER_ALLOCATION_LIMIT"}
        self.assertTrue(reserved.isdisjoint(self.variables))
        self.assertNotIn("SRCDS_APPID", self.variables)

    def test_readme_records_live_and_unverified_scope(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for evidence in (
            "Wine 11", "build `25627237`", "App `1007`", "game UDP listener",
            "query UDP listener", "A2S `0x49`", "`ScriptLog: AFFGameInfo_Incursion_0 Game match has been started`", f"`{READY_TOKEN}`",
        ):
            self.assertIn(evidence, readme)
        for unverified in (
            "real-client direct join", "browser discovery", "`AdminLogin`",
            "public client joins", "map rotation",
        ):
            self.assertIn(unverified, readme)
        self.assertNotIn("empty startup " + "detector", readme)
        self.assertNotIn("has not yet completed a live Steam " + "payload", readme)


class RuntimeInputRejectionTests(unittest.TestCase):
    def run_start(self, overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix=".test-runtime-input-", dir=ROOT) as temporary:
            env = os.environ.copy()
            env.update({
                "SERVER_ROOT": temporary, "WINEPREFIX": f"{temporary}/.wine",
                "SERVER_PORT": "7777", "QUERY_PORT": "27015", "ADMIN_PASSWORD": "StrongAdmin1!",
                "AUTO_UPDATE": "0", "AFF_AUTO_UPDATE": "0", "WINETRICKS_RUN": "", "VALIDATE": "0",
            })
            env.update(overrides)
            return run(["bash", str(START_PATH)], env=env)

    def test_control_quote_path_and_interpolation_injection_rejected(self) -> None:
        values = [
            "bad\nname", "bad\rname", 'bad"name', "bad'name", "bad/name", "bad\\name",
            "$(touch PWNED)", "`touch PWNED`", "name;touch PWNED",
        ]
        for value in values:
            with self.subTest(value=repr(value)):
                result = self.run_start({"SERVER_NAME": value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("forbidden", result.stderr.lower())
        self.assertFalse((ROOT / "PWNED").exists())

    def test_unknown_and_malformed_maps_rejected(self) -> None:
        cases = [
            {"DEFAULT_MAP": "AFF-Unknown"}, {"DEFAULT_MAP": "../../AFF-Ixion"},
            {"MAP_CYCLE": "AFF-Ixion,AFF-Unknown"}, {"MAP_CYCLE": "AFF-Ixion,,AFF-Irega"},
            {"MAP_CYCLE": 'AFF-Ixion,"AFF-Irega"'},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                result = self.run_start(overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(result.stderr, r"(map|MAP_CYCLE|forbidden)")

    def test_numeric_boolean_and_port_domains_rejected(self) -> None:
        cases = [
            {"SERVER_PORT": "0"}, {"QUERY_PORT": "7777"}, {"QUERY_PORT": "27015;id"},
            {"MAX_PLAYERS": "65"}, {"NUM_PLAY": "33", "MAX_PLAYERS": "32"},
            {"BALANCE_BOTS": "yes"}, {"AI_LEVEL": "6"}, {"BRIEFING_TIME": "-1"},
            {"RANDOM_COMMANDER": "1"}, {"RED_STARTING_TICKETS": "9"},
            {"BLUE_STARTING_TICKETS": "9"}, {"FRIENDLY_FIRE": "101"},
            {"AUTO_UPDATE": "1"}, {"AFF_AUTO_UPDATE": "true"}, {"VALIDATE": "validate"},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                result = self.run_start(overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ERROR:", result.stderr)

    def test_admin_password_is_required_and_placeholders_rejected(self) -> None:
        for value in ("", "short", "password", "ChangeMe", "admin123"):
            with self.subTest(value=value):
                result = self.run_start({"ADMIN_PASSWORD": value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ADMIN_PASSWORD", result.stderr)


class RuntimeHarness:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix=".test-runtime-live-", dir=ROOT)
        self.root = Path(self.temporary.name)
        self.server = self.root / "server"
        self.fake_bin = self.root / "fake-bin"
        self.events = self.root / "events.log"
        self.steam_args = self.root / "steam-args.log"
        self.wine_pid = self.root / "wine.pid"
        self.tail_pid = self.root / "tail.pid"
        self.filter_pid = self.root / "filter.pid"
        self.descendant_pid = self.root / "descendant.pid"
        self.startup_descendant_pid = self.root / "startup-descendant.pid"
        self.steam_pid = self.root / "steam.pid"
        self.wineboot_pid = self.root / "wineboot.pid"
        self.wineserver_pid = self.root / "wineserver.pid"
        self.winetricks_pid = self.root / "winetricks.pid"
        self.wine_args = self.root / "wine-args.bin"
        self.awk_pid = self.root / "awk.pid"
        self.awk_gate = self.root / "awk.gate"
        self.awk_seen = self.root / "awk.seen"
        self.attack_helper = self.root / "child-attack.sh"
        self.probe_log = self.root / "child-probes.log"
        self.ipc_pid = self.root / "readiness-relay.pid"
        self._prepare_server()
        self._prepare_commands()

    def __enter__(self) -> "RuntimeHarness":
        return self

    def __exit__(self, *_: object) -> None:
        self.temporary.cleanup()

    @staticmethod
    def manifest(app_id: str) -> str:
        return f'"AppState"\n{{\n    "appid" "{app_id}"\n    "StateFlags" "4"\n}}\n'

    def _prepare_server(self) -> None:
        (self.server / "steamapps").mkdir(parents=True)
        (self.server / "steamapps/appmanifest_1007.acf").write_text(self.manifest("1007"), encoding="utf-8")
        (self.server / "steamapps/appmanifest_407480.acf").write_text(self.manifest("407480"), encoding="utf-8")
        (self.server / "steamcmd").mkdir()
        (self.server / "Binaries/Win64").mkdir(parents=True)
        (self.server / "Binaries/Win64/AFFGameServer.exe").write_text("fake\n", encoding="utf-8")
        cooked = self.server / "AFFGame/CookedPCServer"
        cooked.mkdir(parents=True)
        for token in EXPECTED_MAPS:
            (cooked / f"{token}.upk").write_text("fake map\n", encoding="utf-8")
        templates = self.server / "AFFGame/Config/PCServer/Cooked"
        templates.mkdir(parents=True)
        (templates / "PCServer-AFFGame.ini").write_text(
            "[AFFGame.AFFGameReplicationInfo]\nServerName=Vendor\n"
            "[Engine.AccessControl]\nAdminPassword=Vendor\n"
            "[UTGame.UTGame]\nGameSpecificMapCycles=()\n", encoding="utf-8",
        )
        (templates / "PCServer-AFFEngine.ini").write_text(
            "[URL]\nPort=7777\n[OnlineSubsystemSteamworks.OnlineSubsystemSteamworks]\nQueryPort=27015\n",
            encoding="utf-8",
        )
        runtime = self.server / ".wine/drive_c/windows/system32"
        runtime.mkdir(parents=True)
        (runtime / "vcruntime140.dll").write_text("fake\n", encoding="utf-8")
        (runtime / "msvcp140.dll").write_text("fake\n", encoding="utf-8")

    def _script(self, name: str, source: str) -> None:
        path = self.fake_bin / name
        path.write_text(source, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def _prepare_commands(self) -> None:
        self.fake_bin.mkdir()
        self.attack_helper.write_text(r'''fake_adversarial_probe() {
    local probe_name="$1" descriptor_path descriptor target known_path
    [[ "${FAKE_ADVERSARIAL_CHILDREN:-0}" == "1" ]] || return 0
    printf 'probe:%s\n' "$probe_name" >> "$FAKE_PROBE_LOG"
    kill -USR1 "$PPID" 2>/dev/null || true
    kill -USR2 "$PPID" 2>/dev/null || true
    printf '%s\n' '[9.9] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' '[AFF-EGG] SERVER READY 8f31c2'
    printf '%s\n' '[8.8] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' '[AFF-EGG] SERVER READY 8f31c2' >&2
    for descriptor_path in /proc/$$/fd/*; do
        descriptor="${descriptor_path##*/}"
        target="$(readlink -- "$descriptor_path" 2>/dev/null || true)"
        printf 'fd:%s:%s:%s\n' "$probe_name" "$descriptor" "$target" >> "$FAKE_PROBE_LOG"
    done
    for known_path in \
        "$SERVER_ROOT/.pterodactyl/readiness" \
        "$SERVER_ROOT/.pterodactyl/readiness.pipe"; do
        if printf '%s\n' 'AFF_READINESS_8f31c2' > "$known_path" 2>/dev/null; then
            printf 'path-write:%s:%s\n' "$probe_name" "$known_path" >> "$FAKE_PROBE_LOG"
        fi
    done
}
''', encoding="utf-8", newline="\n")
        self._script("wineboot", r'''#!/usr/bin/env bash
source "$FAKE_ATTACK_HELPER"
fake_adversarial_probe wineboot
printf '%s\n' "$$" > "$FAKE_WINEBOOT_PID"
printf '%s\n' '[1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' '[AFF-EGG] SERVER READY 8f31c2' \
    'ERROR: [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' 'ERROR: [AFF-EGG] SERVER READY 8f31c2' \
    'prefix [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started suffix' 'prefix [AFF-EGG] SERVER READY 8f31c2 suffix' \
    '[1.2] OtherLog: unrelated logger line'
printf '%s\n' '[2.3] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' '[AFF-EGG] SERVER READY 8f31c2' >&2
if [[ "${FAKE_WINEBOOT_MODE:-normal}" == "signal" ]]; then
    printf 'wineboot-signal\n' >> "$FAKE_EVENTS"
    kill -TERM "$PPID"
fi
[[ "${FAKE_WINEBOOT_MODE:-normal}" != "fail" ]] || exit 51
if [[ "${FAKE_WINEBOOT_MODE:-normal}" == "hang" ]]; then
    bash -c 'trap "" TERM INT; while :; do sleep 0.1; done' &
    printf '%s\n' "$!" > "$FAKE_STARTUP_DESCENDANT_PID"
    trap '' TERM INT
    while :; do sleep 0.1; done
fi
exit 0
''')
        self._script("wineserver", r'''#!/usr/bin/env bash
if [[ "${1:-}" == "-w" ]]; then
    source "$FAKE_ATTACK_HELPER"
    fake_adversarial_probe wineserver
    printf '%s\n' "$$" > "$FAKE_WINESERVER_PID"
    printf '%s\n' '[1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' '[AFF-EGG] SERVER READY 8f31c2' \
        'ERROR: [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' 'ERROR: [AFF-EGG] SERVER READY 8f31c2' \
        'prefix [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started suffix' 'prefix [AFF-EGG] SERVER READY 8f31c2 suffix' \
        '[1.2] OtherLog: unrelated logger line'
    printf '%s\n' '[2.3] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' '[AFF-EGG] SERVER READY 8f31c2' >&2
    [[ "${FAKE_WINESERVER_MODE:-normal}" != "fail-wait" ]] || exit 52
    if [[ "${FAKE_WINESERVER_MODE:-normal}" == "hang-wait" ]]; then
        bash -c 'trap "" TERM INT; while :; do sleep 0.1; done' &
        printf '%s\n' "$!" > "$FAKE_STARTUP_DESCENDANT_PID"
        trap '' TERM INT
        while :; do sleep 0.1; done
    fi
fi
if [[ "${FAKE_WINESERVER_WAIT_FOR_TERM:-0}" == "1" && "${1:-}" == "-k" ]]; then
    for ((attempt = 0; attempt < 100; attempt++)); do
        grep -q '^wine-term$' "$FAKE_EVENTS" 2>/dev/null && break
        sleep 0.01
    done
fi
printf 'wineserver:%s\n' "$*" >> "$FAKE_EVENTS"
if [[ "${FAKE_WINESERVER_MODE:-normal}" == "hang" && "${1:-}" == "-k" ]]; then
    trap '' TERM
    while :; do sleep 0.1; done
fi
exit 0
''')
        self._script("winetricks", r'''#!/usr/bin/env bash
source "$FAKE_ATTACK_HELPER"
fake_adversarial_probe winetricks
printf '%s\n' "$$" > "$FAKE_WINETRICKS_PID"
printf '%s\n' '[1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' '[AFF-EGG] SERVER READY 8f31c2' \
    'ERROR: [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' 'ERROR: [AFF-EGG] SERVER READY 8f31c2' \
    'prefix [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started suffix' 'prefix [AFF-EGG] SERVER READY 8f31c2 suffix' \
    '[1.2] OtherLog: unrelated logger line'
printf '%s\n' '[2.3] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' '[AFF-EGG] SERVER READY 8f31c2' >&2
[[ "${FAKE_WINETRICKS_MODE:-normal}" != "fail" ]] || exit 53
if [[ "${FAKE_WINETRICKS_MODE:-normal}" == "hang" ]]; then
    bash -c 'trap "" TERM INT; while :; do sleep 0.1; done' &
    printf '%s\n' "$!" > "$FAKE_STARTUP_DESCENDANT_PID"
    trap '' TERM INT
    while :; do sleep 0.1; done
fi
mkdir -p "$WINEPREFIX/drive_c/windows/system32"
printf 'fake\n' > "$WINEPREFIX/drive_c/windows/system32/vcruntime140.dll"
printf 'fake\n' > "$WINEPREFIX/drive_c/windows/system32/msvcp140.dll"
exit 0
''')
        self._script("wine", r'''#!/usr/bin/env bash
source "$FAKE_ATTACK_HELPER"
fake_adversarial_probe wine
printf '%s\n' "$$" > "$FAKE_WINE_PID"
printf '%s\0' "$@" > "$FAKE_WINE_ARGS"
printf 'wine-start\n' >> "$FAKE_EVENTS"
printf '%s\n' '[1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' '[AFF-EGG] SERVER READY 8f31c2' \
    'ERROR: [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' 'ERROR: [AFF-EGG] SERVER READY 8f31c2' \
    'prefix [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started suffix' 'prefix [AFF-EGG] SERVER READY 8f31c2 suffix' \
    '[1.2] OtherLog: unrelated logger line'
printf '%s\n' '[2.3] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' '[AFF-EGG] SERVER READY 8f31c2' >&2
game_log="$SERVER_ROOT/AFFGame/Logs/Launch.log"
for sibling_pid in $(</proc/$PPID/task/$PPID/children); do
    [[ -r "/proc/$sibling_pid/comm" ]] || continue
    [[ "$(</proc/$sibling_pid/comm)" == "cat" ]] || continue
    printf '%s\n' "$sibling_pid" > "$FAKE_IPC_PID"
    if [[ "${FAKE_ADVERSARIAL_CHILDREN:-0}" == "1" ]]; then
        for ipc_descriptor in /proc/$sibling_pid/fd/*; do
            ipc_target="$(readlink -- "$ipc_descriptor" 2>/dev/null || true)"
            printf 'ipc-fd:%s:%s\n' "${ipc_descriptor##*/}" "$ipc_target" >> "$FAKE_PROBE_LOG"
        done
    fi
done
case "${FAKE_WINE_MODE:-ready}" in
    ready)
        printf '[123.456] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started\n' >> "$game_log"
        printf '[124.000] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started\r\n' >> "$game_log"
        sleep 0.3
        exit "${FAKE_WINE_STATUS:-0}"
        ;;
    ready-crlf)
        printf '[123.456] ScriptLog: AFFGameInfo_Territories_12 Game match has been started\r\n' >> "$game_log"
        sleep 0.3
        exit "${FAKE_WINE_STATUS:-0}"
        ;;
    adversarial)
        printf '%s\n' \
            'ERROR: [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' \
            '[1.2] ScriptLog: NOT START MATCH' \
            '[1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started FAILED' \
            'prefix [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' \
            '[1.2] OtherLog: ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' \
            '[1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started suffix' >> "$game_log"
        sleep 0.3
        exit "${FAKE_WINE_STATUS:-0}"
        ;;
    stale)
        sleep 0.2
        exit "${FAKE_WINE_STATUS:-0}"
        ;;
    output-spoof)
        sleep 0.2
        exit "${FAKE_WINE_STATUS:-0}"
        ;;
    before-ready)
        printf 'wine-before-ready\n' >> "$FAKE_EVENTS"
        trap 'printf "wine-term\\n" >> "$FAKE_EVENTS"; exit 0' TERM INT
        while :; do sleep 0.1; done
        ;;
    stay)
        trap 'printf "wine-term\\n" >> "$FAKE_EVENTS"; exit 0' TERM INT
        printf '[123.456] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started\n' >> "$game_log"
        while :; do sleep 0.1; done
        ;;
    ignore-term)
        trap '' TERM
        printf 'wine-ignore-ready\n' >> "$FAKE_EVENTS"
        printf '[123.456] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started\n' >> "$game_log"
        while :; do sleep 0.1; done
        ;;
    descendant)
        bash -c '
            trap '\''printf "descendant-term\\n" >> "$FAKE_EVENTS"; exit 0'\'' TERM INT
            printf "%s\\n" "$$" > "$FAKE_DESCENDANT_PID"
            while :; do sleep 0.1; done
        ' &
        sleep 0.2
        exit "${FAKE_WINE_STATUS:-37}"
        ;;
    immediate-ignore-descendant)
        bash -c '
            trap "" TERM INT
            printf "%s\\n" "$$" > "$FAKE_DESCENDANT_PID"
            while :; do sleep 0.1; done
        ' &
        for ((attempt = 0; attempt < 100; attempt++)); do
            [[ -s "$FAKE_DESCENDANT_PID" ]] && break
            sleep 0.01
        done
        exit 37
        ;;
esac
''')
        self._script("tail", r'''#!/usr/bin/env bash
printf '%s\n' "$$" > "$FAKE_TAIL_PID"
exec /usr/bin/tail "$@"
''')
        self._script("setsid", r'''#!/usr/bin/env bash
if [[ "${1:-}" == "bash" && "${2:-}" == "-c" ]]; then
    if [[ "${4:-}" == "aff-log-filter" ]]; then
        printf '%s\n' "$$" > "$FAKE_FILTER_PID"
    fi
fi
matches_target=0
case "${FAKE_SETSID_TARGET:-wine}" in
    wine) [[ "${4:-}" == "aff-game" ]] && matches_target=1 ;;
    filter) [[ "${4:-}" == "aff-log-filter" ]] && matches_target=1 ;;
    startup) [[ "${4:-}" == "aff-startup-command" ]] && matches_target=1 ;;
    steamcmd) [[ "${4:-}" == "aff-startup-command" && "${7##*/}" == "steamcmd.sh" ]] && matches_target=1 ;;
    wineboot) [[ "${4:-}" == "aff-startup-command" && "${7:-}" == "wineboot" ]] && matches_target=1 ;;
    wineserver) [[ "${4:-}" == "aff-startup-command" && "${7:-}" == "wineserver" ]] && matches_target=1 ;;
    winetricks) [[ "${4:-}" == "aff-startup-command" && "${7:-}" == "winetricks" ]] && matches_target=1 ;;
esac
case "${FAKE_SETSID_MODE:-normal}" in
    delayed-signal)
        if (( matches_target == 1 )); then
            printf 'setsid-delayed\n' >> "$FAKE_EVENTS"
            kill -TERM "$PPID"
            sleep 1
        fi
        ;;
    failed-signal)
        if (( matches_target == 1 )); then
            printf 'setsid-failed\n' >> "$FAKE_EVENTS"
            kill -TERM "$PPID"
            exec "$@"
        fi
        ;;
esac
exec /usr/bin/setsid "$@"
''')
        self._script("awk", r'''#!/usr/bin/env bash
should_block=0
case "${FAKE_AWK_BLOCK:-none}" in
    patch) [[ "$*" == *'/dev/stdin'* ]] && should_block=1 ;;
    manifest) [[ "$*" != *'/dev/stdin'* ]] && should_block=1 ;;
esac
if (( should_block == 1 )) && [[ ! -e "$FAKE_AWK_SEEN" ]]; then
    : > "$FAKE_AWK_SEEN"
    printf '%s\n' "$$" > "$FAKE_AWK_PID"
    while [[ ! -e "$FAKE_AWK_GATE" ]]; do sleep 0.02; done
fi
exec /usr/bin/awk "$@"
''')
        self._script("steamcmd.sh", r'''#!/usr/bin/env bash
source "$FAKE_ATTACK_HELPER"
fake_adversarial_probe steamcmd
printf '%s\n' "$$" > "$FAKE_STEAM_PID"
printf '%s\n' '[1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' '[AFF-EGG] SERVER READY 8f31c2' \
    'ERROR: [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' 'ERROR: [AFF-EGG] SERVER READY 8f31c2' \
    'prefix [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started suffix' 'prefix [AFF-EGG] SERVER READY 8f31c2 suffix' \
    '[1.2] OtherLog: unrelated logger line'
printf '%s\n' '[2.3] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started' '[AFF-EGG] SERVER READY 8f31c2' >&2
if [[ "${FAKE_STEAM_MODE:-success}" == "hang" ]]; then
    bash -c 'trap "" TERM INT; while :; do sleep 0.1; done' &
    printf '%s\n' "$!" > "$FAKE_STARTUP_DESCENDANT_PID"
    trap '' TERM INT
    while :; do sleep 0.1; done
fi
printf '%s\n' "$@" > "$FAKE_STEAM_ARGS"
while (($#)); do
    case "$1" in
        +login) printf 'login:%s\n' "$2" >> "$FAKE_EVENTS"; shift 2 ;;
        +app_update)
            app_id="$2"
            printf 'update:%s\n' "$app_id" >> "$FAKE_EVENTS"
            if [[ "${FAKE_STEAM_MODE:-success}" == "fail$app_id" ]]; then
                [[ "$app_id" == "1007" ]] && exit 41
                exit 42
            fi
            shift 2
            [[ "${1:-}" != "validate" ]] || shift
            ;;
        +@sSteamCmdForcePlatformType)
            printf 'platform:%s\n' "$2" >> "$FAKE_EVENTS"; shift 2 ;;
        *) shift ;;
    esac
done
exit 0
''')
        steamcmd = self.server / "steamcmd/steamcmd.sh"
        steamcmd.write_bytes((self.fake_bin / "steamcmd.sh").read_bytes())
        steamcmd.chmod(0o755)

    def environment(self, **overrides: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "PATH": f"{self.fake_bin}:/usr/bin:/bin", "SERVER_ROOT": str(self.server),
            "WINEPREFIX": str(self.server / ".wine"), "SERVER_PORT": "27777",
            "QUERY_PORT": "27095", "ADMIN_PASSWORD": "StrongAdmin1!", "AUTO_UPDATE": "0",
            "AFF_AUTO_UPDATE": "0", "WINETRICKS_RUN": "", "VALIDATE": "0",
            "FAKE_EVENTS": str(self.events),
            "FAKE_STEAM_ARGS": str(self.steam_args), "FAKE_WINE_PID": str(self.wine_pid),
            "FAKE_TAIL_PID": str(self.tail_pid), "FAKE_DESCENDANT_PID": str(self.descendant_pid),
            "FAKE_WINE_ARGS": str(self.wine_args), "FAKE_FILTER_PID": str(self.filter_pid),
            "FAKE_STARTUP_DESCENDANT_PID": str(self.startup_descendant_pid),
            "FAKE_STEAM_PID": str(self.steam_pid), "FAKE_WINEBOOT_PID": str(self.wineboot_pid),
            "FAKE_WINESERVER_PID": str(self.wineserver_pid),
            "FAKE_WINETRICKS_PID": str(self.winetricks_pid), "FAKE_AWK_PID": str(self.awk_pid),
            "FAKE_AWK_GATE": str(self.awk_gate), "FAKE_AWK_SEEN": str(self.awk_seen),
            "FAKE_ATTACK_HELPER": str(self.attack_helper), "FAKE_PROBE_LOG": str(self.probe_log),
            "FAKE_IPC_PID": str(self.ipc_pid),
        })
        env.update(overrides)
        return env

    def run(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        return run(["bash", str(START_PATH)], env=self.environment(**overrides), timeout=12)


class RuntimeUpdateAndMapTests(unittest.TestCase):
    def test_runtime_update_exact_order_and_validate_for_both_apps(self) -> None:
        with RuntimeHarness() as harness:
            result = harness.run(AFF_AUTO_UPDATE="1", VALIDATE="1")
            self.assertEqual(result.returncode, 0, result.stderr)
            args = harness.steam_args.read_text(encoding="utf-8").splitlines()
            self.assertEqual(args, [
                "+force_install_dir", str(harness.server), "+login", "anonymous",
                "+app_update", "1007", "validate", "+@sSteamCmdForcePlatformType", "windows",
                "+app_update", "407480", "validate", "+quit",
            ])
            events = harness.events.read_text(encoding="utf-8").splitlines()
            dependency_events = [line for line in events if line.startswith(("login:", "update:", "platform:"))]
            self.assertEqual(
                dependency_events,
                ["login:anonymous", "update:1007", "platform:windows", "update:407480"],
            )

    def test_runtime_update_failure_propagates_for_either_app(self) -> None:
        for mode, expected_status, forbidden_event in (
            ("fail1007", 41, "platform:windows"), ("fail407480", 42, "wine-start"),
        ):
            with self.subTest(mode=mode), RuntimeHarness() as harness:
                result = harness.run(AFF_AUTO_UPDATE="1", FAKE_STEAM_MODE=mode)
                self.assertEqual(result.returncode, expected_status, result.stderr)
                events = harness.events.read_text(encoding="utf-8")
                self.assertNotIn(forbidden_event, events)
                if mode == "fail1007":
                    self.assertNotIn("update:407480", events)

    def test_each_wine_startup_command_preserves_failure_status(self) -> None:
        cases = (
            ("wineboot", {"FAKE_WINEBOOT_MODE": "fail"}, 51),
            ("wineserver", {"FAKE_WINESERVER_MODE": "fail-wait"}, 52),
            ("winetricks", {"FAKE_WINETRICKS_MODE": "fail"}, 53),
        )
        for name, overrides, expected_status in cases:
            with self.subTest(command=name), RuntimeHarness() as harness:
                if name == "winetricks":
                    (harness.server / ".wine/drive_c/windows/system32/vcruntime140.dll").unlink()
                    (harness.server / ".wine/drive_c/windows/system32/msvcp140.dll").unlink()
                result = harness.run(**overrides)
                self.assertEqual(result.returncode, expected_status, result.stderr)
                self.assertFalse(harness.wine_pid.exists())

    def test_both_complete_manifests_are_required(self) -> None:
        for app_id in ("1007", "407480"):
            for mode in ("missing", "incomplete"):
                with self.subTest(app_id=app_id, mode=mode), RuntimeHarness() as harness:
                    manifest = harness.server / f"steamapps/appmanifest_{app_id}.acf"
                    if mode == "missing":
                        manifest.unlink()
                    else:
                        manifest.write_text(
                            harness.manifest(app_id).replace('"StateFlags" "4"', '"StateFlags" "2"'),
                            encoding="utf-8",
                        )
                    result = harness.run()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(app_id, result.stderr)
                    self.assertFalse(harness.wine_pid.exists())

    def test_selected_map_packages_must_be_nonempty_regular_files(self) -> None:
        cases = (("AFF-Ixion", "empty"), ("AFF-Irega", "missing"), ("AFF-Errah", "symlink"))
        for token, mode in cases:
            with self.subTest(token=token, mode=mode), RuntimeHarness() as harness:
                package = harness.server / f"AFFGame/CookedPCServer/{token}.upk"
                if mode == "empty":
                    package.write_bytes(b"")
                elif mode == "missing":
                    package.unlink()
                else:
                    package.unlink()
                    package.symlink_to("AFF-Ixion.upk")
                result = harness.run()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"{token}.upk", result.stderr)
                self.assertFalse(harness.wine_pid.exists())

    def test_hardlinked_instance_ini_and_log_are_rejected(self) -> None:
        for target in (
            "AFFGame/Config/Pterodactyl/PCServer-AFFGame.ini", "AFFGame/Logs/Launch.log",
            ".pterodactyl/startup-diagnostics.log", ".pterodactyl/wine-diagnostics.log",
        ):
            with self.subTest(target=target), RuntimeHarness() as harness:
                destination = harness.server / target
                destination.parent.mkdir(parents=True, exist_ok=True)
                external = harness.root / "external-hardlink"
                external.write_text("[AFFGame.AFFGameReplicationInfo]\nServerName=External\n", encoding="utf-8")
                os.link(external, destination)
                result = harness.run()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("hardlink", result.stderr.lower())
                self.assertFalse(harness.wine_pid.exists())


class RuntimeReadinessAndSupervisorTests(unittest.TestCase):
    def assert_process_gone(self, pid_file: Path) -> None:
        if not pid_file.exists():
            return
        pid = int(pid_file.read_text(encoding="ascii"))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and process_exists(pid):
            time.sleep(0.02)
        self.assertFalse(process_exists(pid), f"process {pid} remained alive")

    def assert_follower_gone(self, harness: RuntimeHarness) -> None:
        self.assert_process_gone(harness.filter_pid)
        self.assert_process_gone(harness.tail_pid)

    def test_exact_fresh_readiness_emits_one_canonical_marker(self) -> None:
        for mode in ("ready", "ready-crlf"):
            with self.subTest(mode=mode), RuntimeHarness() as harness:
                result = harness.run(FAKE_WINE_MODE=mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.count(READY_TOKEN), 1)
                self.assertNotIn("ScriptLog: AFFGameInfo_Incursion_0 Game match has been started", result.stdout)
                self.assertNotIn("[123.456]", result.stdout)
                self.assertIn(READY_TOKEN, (harness.server / ".pterodactyl/wine-diagnostics.log").read_text())
                self.assert_follower_gone(harness)

    def test_adversarial_and_stale_readiness_lines_emit_nothing(self) -> None:
        with RuntimeHarness() as harness:
            result = harness.run(FAKE_WINE_MODE="adversarial")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(READY_TOKEN, result.stdout)
            self.assertNotIn("ScriptLog: AFFGameInfo_Incursion_0 Game match has been started", result.stdout)
            self.assert_follower_gone(harness)

        with RuntimeHarness() as harness:
            logs = harness.server / "AFFGame/Logs"
            logs.mkdir()
            (logs / "Launch.log").write_text("[123.456] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started\n", encoding="utf-8")
            result = harness.run(FAKE_WINE_MODE="stale")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(READY_TOKEN, result.stdout)
            self.assertNotIn("ScriptLog: AFFGameInfo_Incursion_0 Game match has been started", result.stdout)
            self.assert_follower_gone(harness)

    def test_all_child_spoof_output_is_private_and_cannot_emit_ready(self) -> None:
        with RuntimeHarness() as harness:
            (harness.server / ".wine/drive_c/windows/system32/vcruntime140.dll").unlink()
            (harness.server / ".wine/drive_c/windows/system32/msvcp140.dll").unlink()
            result = harness.run(AFF_AUTO_UPDATE="1", FAKE_WINE_MODE="output-spoof")
            self.assertEqual(result.returncode, 0, result.stderr)
            for spoof in (
                "ScriptLog: AFFGameInfo_Incursion_0 Game match has been started", READY_TOKEN, "ERROR: [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started",
                f"ERROR: {READY_TOKEN}", "prefix [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started suffix",
                f"prefix {READY_TOKEN} suffix", "OtherLog: unrelated logger line",
            ):
                self.assertNotIn(spoof, result.stdout)
            startup_log = harness.server / ".pterodactyl/startup-diagnostics.log"
            wine_log = harness.server / ".pterodactyl/wine-diagnostics.log"
            self.assertIn(READY_TOKEN, startup_log.read_text(encoding="utf-8"))
            self.assertIn(READY_TOKEN, wine_log.read_text(encoding="utf-8"))
            self.assertIn("ERROR: [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started", startup_log.read_text(encoding="utf-8"))
            self.assertIn("prefix [1.2] ScriptLog: AFFGameInfo_Incursion_0 Game match has been started suffix", wine_log.read_text(encoding="utf-8"))
            for private_log in (startup_log, wine_log):
                self.assertEqual(private_log.stat().st_mode & 0o777, 0o600)
                self.assertEqual(private_log.stat().st_nlink, 1)
            for pid_file in (
                harness.steam_pid, harness.wineboot_pid, harness.wineserver_pid,
                harness.winetricks_pid, harness.wine_pid,
            ):
                self.assertTrue(pid_file.exists(), pid_file)
            self.assert_follower_gone(harness)

    def test_all_startup_children_and_wine_cannot_spoof_private_readiness_ipc(self) -> None:
        with RuntimeHarness() as harness:
            (harness.server / ".wine/drive_c/windows/system32/vcruntime140.dll").unlink()
            (harness.server / ".wine/drive_c/windows/system32/msvcp140.dll").unlink()
            result = harness.run(
                AFF_AUTO_UPDATE="1", FAKE_ADVERSARIAL_CHILDREN="1",
                FAKE_WINE_MODE="output-spoof",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(READY_TOKEN, result.stdout)
            probes = harness.probe_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                {line.split(":", 1)[1] for line in probes if line.startswith("probe:")},
                {"steamcmd", "wineboot", "wineserver", "winetricks", "wine"},
            )
            ipc_targets = {
                line.split(":", 2)[2] for line in probes
                if line.startswith(("ipc-fd:0:", "ipc-fd:1:")) and "pipe:[" in line
            }
            wine_targets = {
                line.split(":", 3)[3] for line in probes
                if line.startswith("fd:wine:") and "pipe:[" in line
            }
            self.assertEqual(len(ipc_targets), 2, probes)
            self.assertTrue(ipc_targets.isdisjoint(wine_targets), probes)
            path_writers = {
                line.split(":", 2)[1] for line in probes if line.startswith("path-write:")
            }
            self.assertEqual(
                path_writers,
                {"steamcmd", "wineboot", "wineserver", "winetricks", "wine"},
            )
            self.assert_follower_gone(harness)

    def test_admin_password_uses_only_private_stdin_not_child_argv_or_environment(self) -> None:
        with RuntimeHarness() as harness:
            secret = "LiveProcSecret-8f31c2!"
            process = subprocess.Popen(
                ["bash", str(START_PATH)], cwd=ROOT,
                env=harness.environment(ADMIN_PASSWORD=secret, FAKE_AWK_BLOCK="patch"),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            )
            wait_for_path(harness.awk_pid)
            awk_pid = int(harness.awk_pid.read_text(encoding="ascii"))
            cmdline = Path(f"/proc/{awk_pid}/cmdline").read_bytes()
            environ = Path(f"/proc/{awk_pid}/environ").read_bytes()
            process_table = run(["ps", "-o", "args=", "-p", str(awk_pid)])
            secret_bytes = secret.encode("utf-8")
            self.assertNotIn(secret_bytes, cmdline)
            self.assertNotIn(secret_bytes, environ)
            self.assertNotIn(secret, process_table.stdout)
            self.assertNotIn(b"ADMIN_PASSWORD=", environ)
            harness.awk_gate.write_text("continue\n", encoding="ascii")
            stdout, stderr = process.communicate(timeout=12)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(stdout.count(READY_TOKEN), 1)

            game_ini = harness.server / "AFFGame/Config/Pterodactyl/PCServer-AFFGame.ini"
            self.assertIn(f"AdminPassword={secret}", game_ini.read_text(encoding="utf-8"))
            self.assertEqual(game_ini.stat().st_mode & 0o777, 0o600)
            secret_locations = []
            for path in harness.server.rglob("*"):
                if path.is_file() and secret_bytes in path.read_bytes():
                    secret_locations.append(path)
            self.assertEqual(secret_locations, [game_ini])
            self.assertFalse(list(game_ini.parent.glob("*.tmp.*")))
            self.assert_follower_gone(harness)

    def test_removed_player_secret_is_absent_from_variables_and_argv(self) -> None:
        with RuntimeHarness() as harness:
            secret_name = "GAME_" + "PASSWORD"
            secret_value = "NeverPlaceThisInArgv-7391"
            result = harness.run(**{secret_name: secret_value})
            self.assertEqual(result.returncode, 0, result.stderr)
            argv = harness.wine_args.read_bytes().split(b"\0")
            self.assertFalse(any(secret_value.encode("ascii") in argument for argument in argv))
            variables = {item["env_variable"] for item in load_egg()["variables"]}  # type: ignore[index]
            self.assertNotIn(secret_name, variables)

    def test_natural_exit_status_is_preserved_and_follower_reaped(self) -> None:
        with RuntimeHarness() as harness:
            result = harness.run(FAKE_WINE_MODE="ready", FAKE_WINE_STATUS="23")
            self.assertEqual(result.returncode, 23, result.stderr)
            self.assert_process_gone(harness.wine_pid)
            self.assert_follower_gone(harness)

    def test_natural_leader_exit_stops_outliving_group_member_and_follower(self) -> None:
        with RuntimeHarness() as harness:
            result = harness.run(FAKE_WINE_MODE="descendant", FAKE_WINE_STATUS="37")
            self.assertEqual(result.returncode, 37, result.stderr)
            self.assertIn("descendant-term", harness.events.read_text(encoding="utf-8"))
            self.assert_process_gone(harness.wine_pid)
            self.assert_process_gone(harness.descendant_pid)
            self.assert_follower_gone(harness)

    def test_immediate_exit_37_kills_term_ignoring_group_and_private_ipc(self) -> None:
        with RuntimeHarness() as harness:
            started = time.monotonic()
            result = harness.run(FAKE_WINE_MODE="immediate-ignore-descendant")
            elapsed = time.monotonic() - started
            self.assertEqual(result.returncode, 37, result.stderr)
            self.assertLess(elapsed, 6)
            for pid_file in (
                harness.wine_pid, harness.descendant_pid, harness.filter_pid,
                harness.tail_pid, harness.ipc_pid,
            ):
                self.assertTrue(pid_file.exists(), pid_file)
            self.assert_process_gone(harness.wine_pid)
            self.assert_process_gone(harness.descendant_pid)
            self.assert_process_gone(harness.ipc_pid)
            self.assert_follower_gone(harness)
            runtime_root = harness.server / ".pterodactyl"
            self.assertFalse(
                any(stat.S_ISFIFO(path.stat().st_mode) for path in runtime_root.iterdir())
            )

    def test_bounded_stop_before_game_spawn(self) -> None:
        with RuntimeHarness() as harness:
            started = time.monotonic()
            result = harness.run(FAKE_WINEBOOT_MODE="signal")
            elapsed = time.monotonic() - started
            self.assertEqual(result.returncode, 143, result.stderr)
            self.assertLess(elapsed, 5)
            self.assertFalse(harness.wine_pid.exists())
            self.assert_follower_gone(harness)

    def test_signal_after_follower_spawn_but_before_game_launch(self) -> None:
        with RuntimeHarness() as harness:
            started = time.monotonic()
            result = harness.run(
                FAKE_SETSID_MODE="delayed-signal", FAKE_SETSID_TARGET="filter",
            )
            elapsed = time.monotonic() - started
            self.assertEqual(result.returncode, 143, result.stderr)
            self.assertLess(elapsed, 6)
            self.assertIn("setsid-delayed", harness.events.read_text(encoding="utf-8"))
            self.assertFalse(harness.wine_pid.exists())
            self.assert_process_gone(harness.ipc_pid)
            self.assert_follower_gone(harness)

    def test_signal_before_startup_launch_prevents_all_supervised_commands(self) -> None:
        with RuntimeHarness() as harness:
            process = subprocess.Popen(
                ["bash", str(START_PATH)], cwd=ROOT,
                env=harness.environment(FAKE_AWK_BLOCK="manifest"),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            )
            wait_for_path(harness.awk_pid)
            for pid_file in (
                harness.steam_pid, harness.wineboot_pid, harness.wineserver_pid,
                harness.winetricks_pid, harness.wine_pid,
            ):
                self.assertFalse(pid_file.exists(), pid_file)
            os.kill(process.pid, signal.SIGTERM)
            harness.awk_gate.write_text("continue\n", encoding="ascii")
            _, stderr = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 143, stderr)
            self.assertFalse(harness.wineboot_pid.exists())
            self.assertFalse(harness.wine_pid.exists())
            self.assert_follower_gone(harness)

    def test_each_startup_command_has_bounded_group_kill_for_term_ignoring_descendants(self) -> None:
        cases = (
            ("steamcmd", {"AFF_AUTO_UPDATE": "1", "FAKE_STEAM_MODE": "hang"}, "steam_pid"),
            ("wineboot", {"FAKE_WINEBOOT_MODE": "hang"}, "wineboot_pid"),
            ("wineserver", {"FAKE_WINESERVER_MODE": "hang-wait"}, "wineserver_pid"),
            ("winetricks", {"FAKE_WINETRICKS_MODE": "hang"}, "winetricks_pid"),
        )
        for name, overrides, pid_attribute in cases:
            with self.subTest(command=name), RuntimeHarness() as harness:
                if name == "winetricks":
                    (harness.server / ".wine/drive_c/windows/system32/vcruntime140.dll").unlink()
                    (harness.server / ".wine/drive_c/windows/system32/msvcp140.dll").unlink()
                process = subprocess.Popen(
                    ["bash", str(START_PATH)], cwd=ROOT, env=harness.environment(**overrides),
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                )
                leader_pid_file = getattr(harness, pid_attribute)
                wait_for_path(leader_pid_file)
                wait_for_path(harness.startup_descendant_pid)
                leader_pid = int(leader_pid_file.read_text(encoding="ascii"))
                self.assertEqual(os.getpgid(leader_pid), leader_pid)
                started = time.monotonic()
                os.kill(process.pid, signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=10)
                elapsed = time.monotonic() - started
                self.assertEqual(process.returncode, 143, stderr)
                self.assertLess(elapsed, 8)
                self.assertNotIn(READY_TOKEN, stdout)
                self.assert_process_gone(leader_pid_file)
                self.assert_process_gone(harness.startup_descendant_pid)
                self.assertFalse(harness.wine_pid.exists())
                events = harness.events.read_text(encoding="utf-8") if harness.events.exists() else ""
                if name == "steamcmd":
                    self.assertNotIn("wineserver:-k", events)
                else:
                    self.assertIn("wineserver:-k", events)
                self.assert_follower_gone(harness)

    def test_bounded_stop_during_delayed_startup_setsid_uses_only_positive_pid(self) -> None:
        with RuntimeHarness() as harness:
            started = time.monotonic()
            result = harness.run(
                FAKE_SETSID_MODE="delayed-signal", FAKE_SETSID_TARGET="wineboot",
                FAKE_WINESERVER_MODE="hang",
            )
            elapsed = time.monotonic() - started
            self.assertEqual(result.returncode, 143, result.stderr)
            self.assertLess(elapsed, 8)
            self.assertIn("setsid-delayed", harness.events.read_text(encoding="utf-8"))
            self.assertFalse(harness.wineboot_pid.exists())
            self.assertFalse(harness.wine_pid.exists())
            self.assert_follower_gone(harness)

    def test_bounded_stop_during_delayed_or_failed_setsid_uses_positive_pid(self) -> None:
        for mode, event in (("delayed-signal", "setsid-delayed"), ("failed-signal", "setsid-failed")):
            with self.subTest(mode=mode), RuntimeHarness() as harness:
                started = time.monotonic()
                result = harness.run(
                    FAKE_SETSID_MODE=mode, FAKE_WINE_MODE="stay", FAKE_WINESERVER_MODE="hang",
                )
                elapsed = time.monotonic() - started
                self.assertEqual(result.returncode, 143, result.stderr)
                self.assertLess(elapsed, 8)
                events = harness.events.read_text(encoding="utf-8")
                self.assertIn(event, events)
                self.assertNotIn("wineserver:-k", events)
                self.assert_process_gone(harness.wine_pid)
                self.assert_follower_gone(harness)

    def test_bounded_stop_after_confirmed_group_before_readiness(self) -> None:
        with RuntimeHarness() as harness:
            process = subprocess.Popen(
                ["bash", str(START_PATH)], cwd=ROOT, env=harness.environment(FAKE_WINE_MODE="before-ready"),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            )
            wait_for_path(harness.wine_pid)
            wait_for_path(harness.filter_pid)
            wait_for_text(harness.events, "wine-before-ready")
            wine_pid = int(harness.wine_pid.read_text(encoding="ascii"))
            self.assertEqual(os.getpgid(wine_pid), wine_pid)
            os.kill(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 143, stderr)
            self.assertNotIn("ScriptLog: AFFGameInfo_Incursion_0 Game match has been started", stdout)
            self.assert_process_gone(harness.wine_pid)
            self.assert_follower_gone(harness)

    def test_bounded_stop_after_spawn_orders_term_then_wineserver(self) -> None:
        with RuntimeHarness() as harness:
            process = subprocess.Popen(
                ["bash", str(START_PATH)], cwd=ROOT,
                env=harness.environment(FAKE_WINE_MODE="stay", FAKE_WINESERVER_WAIT_FOR_TERM="1"),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            )
            wait_for_path(harness.wine_pid)
            wait_for_path(harness.tail_pid)
            started = time.monotonic()
            os.kill(process.pid, signal.SIGINT)
            stdout, stderr = process.communicate(timeout=10)
            elapsed = time.monotonic() - started
            self.assertEqual(process.returncode, 130, stderr)
            self.assertLess(elapsed, 8)
            self.assertIn("Shutdown requested (INT)", stdout)
            events = harness.events.read_text(encoding="utf-8").splitlines()
            self.assertLess(events.index("wine-term"), events.index("wineserver:-k"))
            self.assert_process_gone(harness.wine_pid)
            self.assert_follower_gone(harness)

    def test_unresponsive_game_is_killed_only_after_bounded_grace(self) -> None:
        with RuntimeHarness() as harness:
            process = subprocess.Popen(
                ["bash", str(START_PATH)], cwd=ROOT,
                env=harness.environment(FAKE_WINE_MODE="ignore-term"),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            )
            wait_for_path(harness.wine_pid)
            wait_for_path(harness.tail_pid)
            wait_for_text(harness.events, "wine-ignore-ready")
            started = time.monotonic()
            os.kill(process.pid, signal.SIGTERM)
            _, stderr = process.communicate(timeout=10)
            elapsed = time.monotonic() - started
            self.assertEqual(process.returncode, 143, stderr)
            self.assertGreaterEqual(elapsed, 1.5)
            self.assertLess(elapsed, 6)
            events = harness.events.read_text(encoding="utf-8")
            self.assertIn("wineserver:-k", events)
            self.assertNotIn("wine-term", events)
            self.assert_process_gone(harness.wine_pid)
            self.assert_follower_gone(harness)


class InstallerFailClosedTests(unittest.TestCase):
    def make_fake_toolchain(self, root: Path) -> Path:
        fake_bin = root / "fake-bin"
        fake_bin.mkdir()
        scripts = {
            "curl": r'''#!/usr/bin/env bash
set -e
output=""
while (($#)); do
    if [[ "$1" == "--output" ]]; then output="$2"; shift 2; else shift; fi
done
[[ -n "$output" ]]
: > "$output"
''',
            "stat": r'''#!/usr/bin/env bash
last="${@: -1}"
if [[ "$last" == *.tar.gz ]]; then printf '2428561\n'; else exec /usr/bin/stat "$@"; fi
''',
            "sha256sum": r'''#!/usr/bin/env bash
cat >/dev/null
exit 0
''',
            "tar": r'''#!/usr/bin/env bash
set -e
directory=""
while (($#)); do
    if [[ "$1" == "--directory" ]]; then directory="$2"; shift 2; else shift; fi
done
mkdir -p "$directory"
cat > "$directory/steamcmd.sh" <<'FAKE_STEAMCMD'
#!/usr/bin/env bash
set -e
printf '%s\n' "$@" > "$FAKE_STEAM_ARGS"
root=""
while (($#)); do
    case "$1" in
        +force_install_dir) root="$2"; shift 2 ;;
        +login) printf 'login:%s\n' "$2" >> "$FAKE_STEAM_EVENTS"; shift 2 ;;
        +app_update)
            app_id="$2"
            printf 'update:%s\n' "$app_id" >> "$FAKE_STEAM_EVENTS"
            if [[ "$FAKE_STEAM_MODE" == "fail$app_id" ]]; then
                [[ "$app_id" == "1007" ]] && exit 41
                exit 42
            fi
            shift 2
            [[ "${1:-}" != "validate" ]] || shift
            ;;
        +@sSteamCmdForcePlatformType)
            printf 'platform:%s\n' "$2" >> "$FAKE_STEAM_EVENTS"; shift 2 ;;
        *) shift ;;
    esac
done
[[ -n "$root" ]]
mkdir -p "$root/steamapps"
write_manifest() {
    local app_id="$1"
    local state_flags=4
    [[ "$FAKE_STEAM_MODE" != "incomplete$app_id" ]] || state_flags=2
    cat > "$root/steamapps/appmanifest_$app_id.acf" <<MANIFEST
"AppState"
{
    "appid" "$app_id"
    "StateFlags" "$state_flags"
}
MANIFEST
}
[[ "$FAKE_STEAM_MODE" == "missing1007" ]] || write_manifest 1007
[[ "$FAKE_STEAM_MODE" == "missing407480" ]] || write_manifest 407480
if [[ "$FAKE_STEAM_MODE" != "missing_executable" ]]; then
    mkdir -p "$root/Binaries/Win64" "$root/AFFGame/Config/PCServer/Cooked"
    printf 'fake executable\n' > "$root/Binaries/Win64/AFFGameServer.exe"
    printf '[AFFGame.AFFGameReplicationInfo]\nServerName=Vendor\n' > "$root/AFFGame/Config/PCServer/Cooked/PCServer-AFFGame.ini"
    printf '[URL]\nPort=7777\n' > "$root/AFFGame/Config/PCServer/Cooked/PCServer-AFFEngine.ini"
fi
FAKE_STEAMCMD
chmod 0755 "$directory/steamcmd.sh"
''',
        }
        for name, source in scripts.items():
            path = fake_bin / name
            path.write_text(source, encoding="utf-8", newline="\n")
            path.chmod(0o755)
        return fake_bin

    def run_installer(
        self, mode: str, *, hardlink_start: bool = False
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path, tempfile.TemporaryDirectory[str]]:
        temporary = tempfile.TemporaryDirectory(prefix=".test-installer-", dir=ROOT)
        temp_root = Path(temporary.name)
        fake_bin = self.make_fake_toolchain(temp_root)
        server = temp_root / "server"
        events = temp_root / "events.log"
        args = temp_root / "args.log"
        if hardlink_start:
            server.mkdir(parents=True)
            external = temp_root / "external-start"
            external.write_text("operator data\n", encoding="utf-8")
            os.link(external, server / "start.sh")
        env = os.environ.copy()
        env.update({
            "PATH": f"{fake_bin}:/usr/bin:/bin", "SERVER_DIR": str(server),
            "FAKE_STEAM_MODE": mode, "FAKE_STEAM_EVENTS": str(events),
            "FAKE_STEAM_ARGS": str(args),
        })
        result = run(["bash", str(INSTALL_PATH)], env=env)
        return result, server, events, args, temporary

    def test_hardlinked_start_destination_is_rejected(self) -> None:
        result, _, _, _, temporary = self.run_installer("success", hardlink_start=True)
        try:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("hardlinked", result.stderr.lower())
            self.assertNotIn("Installation complete", result.stdout)
        finally:
            temporary.cleanup()

    def test_exact_dependency_order_and_success_payload(self) -> None:
        result, server, events, args_path, temporary = self.run_installer("success")
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Installation complete", result.stdout)
            self.assertEqual(
                events.read_text(encoding="utf-8").splitlines(),
                ["login:anonymous", "update:1007", "platform:windows", "update:407480"],
            )
            args = args_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(args, [
                "+force_install_dir", str(server), "+login", "anonymous", "+app_update", "1007",
                "validate", "+@sSteamCmdForcePlatformType", "windows", "+app_update", "407480",
                "validate", "+quit",
            ])
            self.assertEqual((server / "start.sh").read_bytes(), START_PATH.read_bytes())
            self.assertTrue((server / "AFFGame/Config/Pterodactyl/PCServer-AFFGame.ini").is_file())
            self.assertTrue((server / "AFFGame/Config/Pterodactyl/PCServer-AFFEngine.ini").is_file())
        finally:
            temporary.cleanup()

    def test_failure_of_either_app_propagates_without_success(self) -> None:
        for mode, status in (("fail1007", 41), ("fail407480", 42)):
            with self.subTest(mode=mode):
                result, _, events, _, temporary = self.run_installer(mode)
                try:
                    self.assertEqual(result.returncode, status, result.stderr)
                    self.assertNotIn("Installation complete", result.stdout)
                    event_text = events.read_text(encoding="utf-8")
                    if mode == "fail1007":
                        self.assertNotIn("platform:windows", event_text)
                        self.assertNotIn("update:407480", event_text)
                finally:
                    temporary.cleanup()

    def test_both_manifests_and_executable_are_required(self) -> None:
        for mode, expected in (
            ("missing1007", "1007"), ("missing407480", "407480"),
            ("incomplete1007", "1007"), ("incomplete407480", "407480"),
            ("missing_executable", "AFFGameServer.exe"),
        ):
            with self.subTest(mode=mode):
                result, _, _, _, temporary = self.run_installer(mode)
                try:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr)
                    self.assertNotIn("Installation complete", result.stdout)
                finally:
                    temporary.cleanup()


class ArtifactTests(unittest.TestCase):
    def test_source_checksums_are_complete_and_valid(self) -> None:
        lines = (ROOT / "SHA256SUMS").read_text(encoding="ascii").splitlines()
        self.assertEqual(len(lines), len(build_artifacts.SOURCE_FILES))
        found = set()
        for line in lines:
            digest, name = line.split("  ", 1)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest)
            found.add(name)
        self.assertEqual(found, set(build_artifacts.SOURCE_FILES))

    def test_archive_contents_bytes_modes_and_integrity(self) -> None:
        archive_path = ROOT / "dist" / build_artifacts.ARCHIVE_NAME
        with zipfile.ZipFile(archive_path) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.namelist(), list(build_artifacts.ARCHIVE_FILES))
            for info in archive.infolist():
                self.assertEqual(info.date_time, build_artifacts.FIXED_ZIP_TIME)
                self.assertEqual(info.create_system, 3)
                expected_mode = 0o755 if info.filename in build_artifacts.EXECUTABLE_FILES else 0o644
                self.assertEqual((info.external_attr >> 16) & 0o777, expected_mode)
                self.assertTrue(stat.S_ISREG(info.external_attr >> 16))
                self.assertEqual(archive.read(info.filename), (ROOT / info.filename).read_bytes())

    def test_outer_archive_checksum(self) -> None:
        archive_path = ROOT / "dist" / build_artifacts.ARCHIVE_NAME
        line = (ROOT / "dist/SHA256SUMS").read_text(encoding="ascii").strip()
        digest, name = line.split("  ", 1)
        self.assertEqual(name, build_artifacts.ARCHIVE_NAME)
        self.assertEqual(digest, hashlib.sha256(archive_path.read_bytes()).hexdigest())

    def test_deterministic_builds(self) -> None:
        with tempfile.TemporaryDirectory(prefix=".test-build-a-", dir=ROOT) as first, tempfile.TemporaryDirectory(
            prefix=".test-build-b-", dir=ROOT
        ) as second:
            result_a = run(["python3", "build_artifacts.py", "--output-dir", first])
            result_b = run(["python3", "build_artifacts.py", "--output-dir", second])
            self.assertEqual(result_a.returncode, 0, result_a.stderr)
            self.assertEqual(result_b.returncode, 0, result_b.stderr)
            self.assertEqual(
                (Path(first) / build_artifacts.ARCHIVE_NAME).read_bytes(),
                (Path(second) / build_artifacts.ARCHIVE_NAME).read_bytes(),
            )
            self.assertEqual((Path(first) / "SHA256SUMS").read_bytes(), (Path(second) / "SHA256SUMS").read_bytes())

    def test_no_payload_private_data_or_task_files(self) -> None:
        forbidden_suffixes = {".exe", ".dll", ".so", ".upk", ".u", ".pak", ".vdf", ".acf"}
        public_files = [ROOT / name for name in build_artifacts.ARCHIVE_FILES]
        task_file = ".codex-" + "task.md"
        remediation_file = ".remediation-" + "task.md"
        final_remediation_file = ".final-" + "remediation.md"
        self.assertFalse((ROOT / task_file).exists())
        self.assertFalse((ROOT / remediation_file).exists())
        self.assertFalse((ROOT / final_remediation_file).exists())
        for path in public_files:
            self.assertNotIn(path.suffix.lower(), forbidden_suffixes, path)
            data = path.read_bytes()
            self.assertNotIn(b"\x00", data, path)
            self.assertLess(len(data), 2_000_000, path)
            text = data.decode("utf-8")
            self.assertNotIn("/home/" + "hermes", text)
            self.assertNotIn("pterodactyl-" + "angels-fall-first", text)
            self.assertNotRegex(
                text,
                r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b",
            )
            self.assertNotRegex(text, r"\b(?:10|127|169\.254|192\.168)\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b")

        email_pattern = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        emails = set()
        for path in public_files:
            emails.update(email_pattern.findall(path.read_text(encoding="utf-8")))
        self.assertEqual(emails, {"community@example.invalid"})

        archive_path = ROOT / "dist" / build_artifacts.ARCHIVE_NAME
        with zipfile.ZipFile(archive_path) as archive:
            self.assertNotIn(task_file, archive.namelist())
            for name in archive.namelist():
                self.assertNotIn(Path(name).suffix.lower(), forbidden_suffixes)
                self.assertNotIn(b"\x00", archive.read(name))

    def test_only_expected_generated_outputs_exist(self) -> None:
        self.assertTrue(EGG_PATH.is_file())
        self.assertTrue((ROOT / "SHA256SUMS").is_file())
        self.assertTrue((ROOT / "dist" / build_artifacts.ARCHIVE_NAME).is_file())
        self.assertTrue((ROOT / "dist/SHA256SUMS").is_file())
        self.assertFalse(any(ROOT.rglob("*.exe")))
        self.assertFalse(any(ROOT.rglob("*.dll")))


if __name__ == "__main__":
    unittest.main()
