#!/usr/bin/env bash
set -Eeuo pipefail

SERVER_DIR="${SERVER_DIR:-/mnt/server}"
WORK_DIR=""
STEAMCMD_URL="https://media.steampowered.com/client/steamcmd_linux.tar.gz"
STEAMCMD_ARCHIVE_SHA256="cebf0046bfd08cf45da6bc094ae47aa39ebf4155e5ede41373b579b8f1071e7c"
STEAMCMD_ARCHIVE_SIZE="2428561"

cleanup() {
    if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
        rm -rf -- "$WORK_DIR"
    fi
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

ensure_real_directory() {
    local path="$1"
    if [[ -L "$path" ]]; then
        fail "Refusing symlinked directory: $path"
    fi
    if [[ ! -e "$path" ]]; then
        mkdir -- "$path"
    fi
    [[ -d "$path" && ! -L "$path" ]] || fail "Required directory is unsafe: $path"
}

ensure_safe_tree() {
    local root="$1" entry
    while IFS= read -r -d '' entry; do
        if [[ -L "$entry" || (! -d "$entry" && ! -f "$entry") ]]; then
            fail "Refusing unsafe entry in $root: $entry"
        fi
    done < <(find "$root" -xdev -mindepth 1 -print0)
}

prepare_regular_destination() {
    local path="$1" links
    if [[ -L "$path" || (-e "$path" && ! -f "$path") ]]; then
        fail "Refusing unsafe file destination: $path"
    fi
    if [[ -e "$path" ]]; then
        links="$(stat -c '%h' -- "$path")" || fail "Could not inspect file destination: $path"
        [[ "$links" == "1" ]] || fail "Refusing hardlinked file destination: $path"
    fi
}

require_regular_file() {
    local path="$1" description="$2"
    [[ -f "$path" && ! -L "$path" && -s "$path" && -r "$path" ]] || \
        fail "$description is missing, empty, unreadable, or unsafe."
}

validate_app_manifest() {
    local path="$1" expected_app_id="$2" manifest_size
    require_regular_file "$path" "Steam manifest for App $expected_app_id"
    manifest_size="$(stat -c '%s' -- "$path")" || return 1
    (( manifest_size <= 65536 )) || return 1

    LC_ALL=C awk -v expected_app_id="$expected_app_id" '
        function record_key(key, value, normalized) {
            normalized = tolower(key)
            if (normalized == "appid") {
                app_id_total++
                if (depth == 1) { app_id_top++; app_id_value = value }
            } else if (normalized == "stateflags") {
                state_flags_total++
                if (depth == 1) { state_flags_top++; state_flags_value = value }
            }
        }
        function string_token(value, key) {
            if (finished || invalid) { invalid = 1; return }
            if (!pending) { pending_key = value; pending = 1; return }
            key = pending_key; pending_key = ""; pending = 0
            if (depth == 0) { invalid = 1; return }
            record_key(key, value)
        }
        function open_object(key) {
            if (finished || invalid || !pending) { invalid = 1; return }
            key = pending_key; pending_key = ""; pending = 0
            record_key(key, "")
            if (depth == 0) {
                if (root_seen || tolower(key) != "appstate") { invalid = 1; return }
                root_seen = 1
            }
            depth++
        }
        function close_object() {
            if (invalid || pending || depth <= 0) { invalid = 1; return }
            depth--
            if (depth == 0) finished = 1
        }
        BEGIN {
            depth = 0; pending = 0; root_seen = 0; finished = 0; invalid = 0
            app_id_total = 0; app_id_top = 0; app_id_value = ""
            state_flags_total = 0; state_flags_top = 0; state_flags_value = ""
        }
        {
            line = $0; line_length = length(line); line_index = 1
            while (line_index <= line_length && !invalid) {
                character = substr(line, line_index, 1)
                next_character = substr(line, line_index + 1, 1)
                if (character ~ /[[:space:]]/) {
                    line_index++
                } else if (character == "/" && next_character == "/") {
                    break
                } else if (character == "{") {
                    open_object(); line_index++
                } else if (character == "}") {
                    close_object(); line_index++
                } else if (character == "\"") {
                    value = ""; closed_quote = 0; line_index++
                    while (line_index <= line_length) {
                        character = substr(line, line_index, 1)
                        if (character == "\\") {
                            line_index++
                            if (line_index > line_length) { invalid = 1; break }
                            escaped = substr(line, line_index, 1)
                            if (escaped == "\"" || escaped == "\\") value = value escaped
                            else if (escaped == "n") value = value "\n"
                            else if (escaped == "t") value = value "\t"
                            else { invalid = 1; break }
                            line_index++
                        } else if (character == "\"") {
                            closed_quote = 1; line_index++; break
                        } else {
                            value = value character; line_index++
                        }
                    }
                    if (!closed_quote) invalid = 1
                    if (!invalid) string_token(value)
                } else {
                    invalid = 1
                }
            }
        }
        END {
            exit !(invalid == 0 && root_seen == 1 && finished == 1 && depth == 0 && pending == 0 && \
                app_id_total == 1 && app_id_top == 1 && app_id_value == expected_app_id && \
                state_flags_total == 1 && state_flags_top == 1 && state_flags_value == "4")
        }
    ' "$path"
}

[[ "$SERVER_DIR" == /* && "$SERVER_DIR" != "/" ]] || fail "SERVER_DIR must be a safe absolute directory."
trap cleanup EXIT
ensure_real_directory "$SERVER_DIR"
ensure_real_directory "$SERVER_DIR/steamcmd"
ensure_real_directory "$SERVER_DIR/steamapps"
ensure_safe_tree "$SERVER_DIR/steamcmd"
WORK_DIR="$(mktemp -d "$SERVER_DIR/.aff-install.XXXXXX")"
ARCHIVE="$WORK_DIR/steamcmd_linux.tar.gz"
START_TMP="$WORK_DIR/start.sh"

curl \
    --fail \
    --location \
    --proto '=https' \
    --proto-redir '=https' \
    --show-error \
    --silent \
    --retry 5 \
    --retry-all-errors \
    --retry-delay 2 \
    --connect-timeout 20 \
    --max-time 300 \
    --max-filesize 10485760 \
    --output "$ARCHIVE" \
    "$STEAMCMD_URL"

[[ "$(stat -c '%s' -- "$ARCHIVE")" == "$STEAMCMD_ARCHIVE_SIZE" ]] || fail "SteamCMD archive size mismatch."
printf '%s  %s\n' "$STEAMCMD_ARCHIVE_SHA256" "$ARCHIVE" | sha256sum --check --status || \
    fail "SteamCMD archive checksum mismatch."
tar --extract --gzip --file "$ARCHIVE" --directory "$SERVER_DIR/steamcmd" --no-same-owner --no-same-permissions
ensure_safe_tree "$SERVER_DIR/steamcmd"

STEAMCMD="$SERVER_DIR/steamcmd/steamcmd.sh"
[[ -f "$STEAMCMD" && ! -L "$STEAMCMD" && -s "$STEAMCMD" && -x "$STEAMCMD" ]] || \
    fail "SteamCMD was not installed correctly."

"$STEAMCMD" \
    +force_install_dir "$SERVER_DIR" \
    +login anonymous \
    +app_update 1007 validate \
    +@sSteamCmdForcePlatformType windows \
    +app_update 407480 validate \
    +quit

PREREQUISITE_MANIFEST="$SERVER_DIR/steamapps/appmanifest_1007.acf"
SERVER_MANIFEST="$SERVER_DIR/steamapps/appmanifest_407480.acf"
validate_app_manifest "$PREREQUISITE_MANIFEST" 1007 || \
    fail "Steam manifest for prerequisite App 1007 is malformed, mismatched, or incomplete."
validate_app_manifest "$SERVER_MANIFEST" 407480 || \
    fail "Steam manifest for App 407480 is malformed, mismatched, or incomplete."
require_regular_file "$SERVER_DIR/Binaries/Win64/AFFGameServer.exe" "Binaries/Win64/AFFGameServer.exe"

CONFIG_ROOT="$SERVER_DIR/AFFGame/Config"
TEMPLATE_ROOT="$CONFIG_ROOT/PCServer/Cooked"
INSTANCE_CONFIG="$CONFIG_ROOT/Pterodactyl"
ensure_real_directory "$SERVER_DIR/AFFGame"
ensure_real_directory "$CONFIG_ROOT"
ensure_real_directory "$CONFIG_ROOT/PCServer"
ensure_real_directory "$TEMPLATE_ROOT"
ensure_real_directory "$INSTANCE_CONFIG"

for required_config in PCServer-AFFGame.ini PCServer-AFFEngine.ini; do
    require_regular_file "$TEMPLATE_ROOT/$required_config" "Vendor configuration template $required_config"
done

for config_name in \
    PCServer-AFFGame.ini \
    PCServer-AFFEngine.ini \
    PCServer-AFFInput.ini \
    PCServer-AFFSystemSettings.ini \
    PCServer-AFFUI.ini; do
    source_config="$TEMPLATE_ROOT/$config_name"
    destination_config="$INSTANCE_CONFIG/$config_name"
    prepare_regular_destination "$destination_config"
    if [[ ! -e "$destination_config" && -f "$source_config" && ! -L "$source_config" && -s "$source_config" ]]; then
        install -m 0600 -- "$source_config" "$destination_config"
    fi
done

require_regular_file "$INSTANCE_CONFIG/PCServer-AFFGame.ini" "Instance game configuration"
require_regular_file "$INSTANCE_CONFIG/PCServer-AFFEngine.ini" "Instance engine configuration"

cat > "$START_TMP" <<'AFF_START_SH'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SERVER_ROOT="${SERVER_ROOT:-/home/container}"
APP_ID="${STEAM_APP_ID-407480}"
AUTO_UPDATE="${AUTO_UPDATE-0}"
AFF_AUTO_UPDATE="${AFF_AUTO_UPDATE-1}"
VALIDATE="${VALIDATE-0}"
STEAM_USER="${STEAM_USER-anonymous}"
WINDOWS_INSTALL="${WINDOWS_INSTALL-1}"
WINETRICKS_RUN="${WINETRICKS_RUN-}"
WINEARCH="${WINEARCH-win64}"
WINEDEBUG="${WINEDEBUG--all}"
WINEPREFIX="${WINEPREFIX-$SERVER_ROOT/.wine}"

SERVER_NAME="${SERVER_NAME-Angels Fall First Server}"
SERVER_DESCRIPTION="${SERVER_DESCRIPTION-Community Angels Fall First server}"
MOTD="${MOTD-Welcome to Angels Fall First!}"
ADMIN_PASSWORD="${ADMIN_PASSWORD-}"
export -n ADMIN_PASSWORD
DEFAULT_MAP="${DEFAULT_MAP-AFF-Ixion}"
MAP_CYCLE="${MAP_CYCLE-AFF-Ixion,AFF-Irega,AFF-Errah,AFF-Fortress,AFF-Plains,AFF-Meudeverre}"
QUERY_PORT="${QUERY_PORT-27015}"
MAX_PLAYERS="${MAX_PLAYERS-32}"
NUM_PLAY="${NUM_PLAY-16}"
BALANCE_BOTS="${BALANCE_BOTS-true}"
AI_LEVEL="${AI_LEVEL-3}"
BRIEFING_TIME="${BRIEFING_TIME-60}"
RANDOM_COMMANDER="${RANDOM_COMMANDER-true}"
RED_STARTING_TICKETS="${RED_STARTING_TICKETS-500}"
BLUE_STARTING_TICKETS="${BLUE_STARTING_TICKETS-500}"
FRIENDLY_FIRE="${FRIENDLY_FIRE-0}"

GAME_PID=""
GAME_PGID=""
GAME_START_TIME=""
GAME_GROUP_CONFIRMED=0
LOG_PID=""
LOG_PGID=""
LOG_GROUP_CONFIRMED=0
STARTING_PROCESS=0
SHUTDOWN_REQUESTED=0
SHUTDOWN_SIGNAL="TERM"
SHUTTING_DOWN=0
GAME_REAPED=0
GAME_LOG_RELATIVE="AFFGame/Logs/Launch.log"
STARTUP_PID=""
STARTUP_PGID=""
STARTUP_START_TIME=""
STARTUP_GROUP_CONFIRMED=0
STARTUP_REAPED=0
STARTUP_NEEDS_WINESERVER=0
WINE_RUNTIME_STARTED=0
STARTUP_DIAGNOSTIC_LOG=""
WINE_DIAGNOSTIC_LOG=""
READINESS_TOKEN='[AFF-EGG] SERVER READY 8f31c2'
READINESS_EMITTED=0
READINESS_MESSAGE='AFF_READINESS_8f31c2'
READINESS_MESSAGE_CONSUMED=0
READINESS_READ_FD=""
READINESS_WRITE_FD=""
READINESS_RELAY_PID=""

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

require_fixed() {
    local name="$1" value="$2" expected="$3"
    [[ "$value" == "$expected" ]] || fail "$name must remain $expected."
}

require_uint() {
    local name="$1" value="$2" minimum="$3" maximum="$4" normalized
    (( ${#value} <= 20 )) || fail "$name must be an integer from $minimum through $maximum."
    [[ "$value" =~ ^[0-9]+$ ]] || fail "$name must be an integer from $minimum through $maximum."
    normalized="${value#"${value%%[!0]*}"}"
    [[ -n "$normalized" ]] || normalized=0
    (( ${#normalized} > ${#minimum} || (${#normalized} == ${#minimum} && 10#$normalized >= 10#$minimum) )) || \
        fail "$name must be an integer from $minimum through $maximum."
    (( ${#normalized} < ${#maximum} || (${#normalized} == ${#maximum} && 10#$normalized <= 10#$maximum) )) || \
        fail "$name must be an integer from $minimum through $maximum."
    printf -v "$name" '%s' "$normalized"
}

require_boolean01() {
    local name="$1" value="$2"
    [[ "$value" == "0" || "$value" == "1" ]] || fail "$name must be 0 or 1."
}

require_game_boolean() {
    local name="$1" value="$2"
    [[ "$value" == "true" || "$value" == "false" ]] || fail "$name must be true or false."
}

has_forbidden_text() {
    local value="$1" LC_ALL=C
    [[ "$value" =~ [[:cntrl:]] || "$value" == *\"* || "$value" == *\'* || \
        "$value" == *'/'* || "$value" == *'\'* || "$value" == *'$'* || \
        "$value" == *'`'* || "$value" == *';'* ]]
}

require_text() {
    local name="$1" value="$2" minimum="$3" maximum="$4"
    (( ${#value} >= minimum && ${#value} <= maximum )) || \
        fail "$name must contain $minimum through $maximum characters."
    if has_forbidden_text "$value"; then
        fail "$name contains a forbidden control, quoting, path, or interpolation character."
    fi
}

require_password() {
    local name="$1" value="$2" minimum="$3"
    require_text "$name" "$value" "$minimum" 64
    [[ "$value" =~ ^[A-Za-z0-9.!@%+=:_-]+$ ]] || \
        fail "$name may use only letters, numbers, and .!@%+=:_- characters."
}

is_known_map() {
    case "$1" in
        AFF-Anaster-Territories|AFF-Ascella|AFF-Darsalaam-Territories|AFF-Darsalaam|AFF-Delta-Territories|AFF-Errah-Territories|AFF-Errah|AFF-EyeOfGod-Territories|AFF-Fortress-Conquest|AFF-Fortress-Territories|AFF-Fortress|AFF-Igneous|AFF-Irega-Territories|AFF-Irega|AFF-Ixion-Territories|AFF-Ixion|AFF-Kerrat-Territories|AFF-Kerrat|AFF-LaCroix-Territories|AFF-LaCroix|AFF-Lellebah-Territories|AFF-Lukather-Territories|AFF-Lukather|AFF-Majuro-Territories|AFF-Mazikeen-Territories|AFF-Meudeverre-Territories|AFF-Meudeverre|AFF-Mirnoide|AFF-NB3345-Territories|AFF-Ossidiace-Territories|AFF-Plains-Territories|AFF-Plains|AFF-Poranti|AFF-STR-space|AFF-STR|AFF-Tannhauser|AFF-Universe)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

validate_map_cycle() {
    local raw="$1" token
    local -a maps
    [[ -n "$raw" && ${#raw} -le 512 ]] || fail "MAP_CYCLE must contain 1 through 512 characters."
    has_forbidden_text "$raw" && fail "MAP_CYCLE cannot contain control characters, quotes, or path separators."
    [[ "$raw" != *, && "$raw" != ,* && "$raw" != *',,'* ]] || \
        fail "MAP_CYCLE must be a comma-separated list without empty entries."
    IFS=',' read -r -a maps <<< "$raw"
    (( ${#maps[@]} >= 1 && ${#maps[@]} <= 32 )) || fail "MAP_CYCLE must contain 1 through 32 maps."
    for token in "${maps[@]}"; do
        is_known_map "$token" || fail "MAP_CYCLE contains an unknown map token: $token"
    done
}

require_regular_file() {
    local path="$1" description="$2"
    [[ -f "$path" && ! -L "$path" && -s "$path" && -r "$path" ]] || \
        fail "$description is missing, empty, unreadable, or unsafe."
}

require_single_link() {
    local path="$1" description="$2" links
    links="$(stat -c '%h' -- "$path")" || fail "Could not inspect $description."
    [[ "$links" == "1" ]] || fail "$description must not be hardlinked."
}

ensure_real_directory() {
    local path="$1"
    if [[ -L "$path" ]]; then
        fail "Refusing symlinked directory: $path"
    fi
    if [[ ! -e "$path" ]]; then
        mkdir -- "$path"
    fi
    [[ -d "$path" && ! -L "$path" ]] || fail "Required directory is unsafe: $path"
}

prepare_private_directory() {
    local path="$1" mode
    ensure_real_directory "$path"
    chmod 0700 -- "$path"
    mode="$(stat -c '%a' -- "$path")" || fail "Could not inspect private runtime directory."
    [[ "$mode" == "700" ]] || fail "Private runtime directory permissions are unsafe: $path"
}

validate_private_log() {
    local path="$1" description="$2" mode
    [[ -f "$path" && ! -L "$path" ]] || fail "$description is unsafe."
    require_single_link "$path" "$description"
    mode="$(stat -c '%a' -- "$path")" || fail "Could not inspect $description."
    [[ "$mode" == "600" ]] || fail "$description must have mode 0600."
}

prepare_private_log() {
    local path="$1" description="$2"
    if [[ -L "$path" || (-e "$path" && ! -f "$path") ]]; then
        fail "Refusing unsafe $description: $path"
    fi
    if [[ ! -e "$path" ]]; then
        : > "$path"
    fi
    [[ -f "$path" && ! -L "$path" ]] || fail "$description is unsafe."
    require_single_link "$path" "$description"
    chmod 0600 -- "$path"
    validate_private_log "$path" "$description"
    : > "$path"
    validate_private_log "$path" "$description"
}

validate_app_manifest() {
    local path="$1" expected_app_id="$2" manifest_size
    require_regular_file "$path" "Steam manifest for App $expected_app_id"
    manifest_size="$(stat -c '%s' -- "$path")" || return 1
    (( manifest_size <= 65536 )) || return 1

    LC_ALL=C awk -v expected_app_id="$expected_app_id" '
        function record_key(key, value, normalized) {
            normalized = tolower(key)
            if (normalized == "appid") {
                app_id_total++
                if (depth == 1) { app_id_top++; app_id_value = value }
            } else if (normalized == "stateflags") {
                state_flags_total++
                if (depth == 1) { state_flags_top++; state_flags_value = value }
            }
        }
        function string_token(value, key) {
            if (finished || invalid) { invalid = 1; return }
            if (!pending) { pending_key = value; pending = 1; return }
            key = pending_key; pending_key = ""; pending = 0
            if (depth == 0) { invalid = 1; return }
            record_key(key, value)
        }
        function open_object(key) {
            if (finished || invalid || !pending) { invalid = 1; return }
            key = pending_key; pending_key = ""; pending = 0
            record_key(key, "")
            if (depth == 0) {
                if (root_seen || tolower(key) != "appstate") { invalid = 1; return }
                root_seen = 1
            }
            depth++
        }
        function close_object() {
            if (invalid || pending || depth <= 0) { invalid = 1; return }
            depth--
            if (depth == 0) finished = 1
        }
        BEGIN {
            depth = 0; pending = 0; root_seen = 0; finished = 0; invalid = 0
            app_id_total = 0; app_id_top = 0; app_id_value = ""
            state_flags_total = 0; state_flags_top = 0; state_flags_value = ""
        }
        {
            line = $0; line_length = length(line); line_index = 1
            while (line_index <= line_length && !invalid) {
                character = substr(line, line_index, 1)
                next_character = substr(line, line_index + 1, 1)
                if (character ~ /[[:space:]]/) {
                    line_index++
                } else if (character == "/" && next_character == "/") {
                    break
                } else if (character == "{") {
                    open_object(); line_index++
                } else if (character == "}") {
                    close_object(); line_index++
                } else if (character == "\"") {
                    value = ""; closed_quote = 0; line_index++
                    while (line_index <= line_length) {
                        character = substr(line, line_index, 1)
                        if (character == "\\") {
                            line_index++
                            if (line_index > line_length) { invalid = 1; break }
                            escaped = substr(line, line_index, 1)
                            if (escaped == "\"" || escaped == "\\") value = value escaped
                            else if (escaped == "n") value = value "\n"
                            else if (escaped == "t") value = value "\t"
                            else { invalid = 1; break }
                            line_index++
                        } else if (character == "\"") {
                            closed_quote = 1; line_index++; break
                        } else {
                            value = value character; line_index++
                        }
                    }
                    if (!closed_quote) invalid = 1
                    if (!invalid) string_token(value)
                } else {
                    invalid = 1
                }
            }
        }
        END {
            exit !(invalid == 0 && root_seen == 1 && finished == 1 && depth == 0 && pending == 0 && \
                app_id_total == 1 && app_id_top == 1 && app_id_value == expected_app_id && \
                state_flags_total == 1 && state_flags_top == 1 && state_flags_value == "4")
        }
    ' "$path"
}

bootstrap_config() {
    local template="$1" destination="$2"
    require_regular_file "$template" "Vendor configuration template"
    if [[ -L "$destination" || (-e "$destination" && ! -f "$destination") ]]; then
        fail "Refusing unsafe configuration destination: $destination"
    fi
    if [[ -e "$destination" ]]; then
        require_single_link "$destination" "Server configuration"
    fi
    if [[ ! -e "$destination" ]]; then
        install -m 0600 -- "$template" "$destination"
    fi
    require_regular_file "$destination" "Server configuration"
}

patch_ini_value() {
    local path="$1" section="$2" key="$3" value="$4" patched
    [[ -f "$path" && ! -L "$path" ]] || fail "Refusing unsafe INI file: $path"
    require_single_link "$path" "INI file $path"
    if ! patched="$(
        printf '%s\n' "$value" |
        LC_ALL=C awk -v wanted_section="$section" -v wanted_key="$key" '
        function trim(value) {
            sub(/^[[:space:]]+/, "", value)
            sub(/[[:space:]]+$/, "", value)
            return value
        }
        BEGIN {
            if ((getline wanted_value < "/dev/stdin") <= 0) exit 90
            in_section = 0; section_seen = 0; key_written = 0
        }
        {
            sub(/\r$/, "", $0)
            if ($0 ~ /^[[:space:]]*\[[^]]+\][[:space:]]*$/) {
                if (in_section && !key_written) { print wanted_key "=" wanted_value; key_written = 1 }
                header = $0
                sub(/^[[:space:]]*\[/, "", header)
                sub(/\][[:space:]]*$/, "", header)
                in_section = (tolower(header) == tolower(wanted_section))
                if (in_section) section_seen = 1
                print $0
                next
            }
            if (in_section) {
                equals = index($0, "=")
                if (equals > 0) {
                    candidate = trim(substr($0, 1, equals - 1))
                    if (tolower(candidate) == tolower(wanted_key)) {
                        if (!key_written) { print wanted_key "=" wanted_value; key_written = 1 }
                        next
                    }
                }
            }
            print $0
        }
        END {
            if (in_section && !key_written) print wanted_key "=" wanted_value
            else if (!section_seen) {
                print ""
                print "[" wanted_section "]"
                print wanted_key "=" wanted_value
            }
        }
        ' "$path"
    )"; then
        fail "Could not patch INI file: $path"
    fi
    [[ -n "$patched" ]] || fail "INI patch produced an empty file: $path"
    chmod 0600 -- "$path"
    [[ "$(stat -c '%a' -- "$path")" == "600" ]] || fail "INI file permissions are unsafe: $path"
    require_single_link "$path" "INI file $path"
    printf '%s\n' "$patched" > "$path" || fail "Could not write INI file: $path"
}

read_process_record() {
    local pid="$1" process_stat process_fields
    [[ "$pid" =~ ^[1-9][0-9]*$ && -r "/proc/$pid/stat" ]] || return 1
    read -r process_stat 2>/dev/null < "/proc/$pid/stat" || return 1
    process_fields="${process_stat##*) }"
    IFS=' ' read -r -a PROCESS_FIELDS <<< "$process_fields"
    (( ${#PROCESS_FIELDS[@]} > 19 )) || return 1
    PROCESS_STATE="${PROCESS_FIELDS[0]}"
    PROCESS_PGID="${PROCESS_FIELDS[2]}"
    PROCESS_START_TIME="${PROCESS_FIELDS[19]}"
}

game_leader_running() {
    [[ -n "$GAME_PID" && "$GAME_REAPED" == "0" ]] || return 1
    read_process_record "$GAME_PID" || return 1
    if [[ -z "$GAME_START_TIME" ]]; then
        GAME_START_TIME="$PROCESS_START_TIME"
    fi
    [[ "$PROCESS_START_TIME" == "$GAME_START_TIME" && "$PROCESS_STATE" != "Z" ]]
}

confirm_game_group() {
    game_leader_running || return 1
    [[ "$PROCESS_STATE" == "T" && "$PROCESS_PGID" == "$GAME_PID" ]] || return 1
    GAME_PGID="$GAME_PID"
    GAME_GROUP_CONFIRMED=1
}

startup_leader_running() {
    [[ -n "$STARTUP_PID" && "$STARTUP_REAPED" == "0" ]] || return 1
    read_process_record "$STARTUP_PID" || return 1
    if [[ -z "$STARTUP_START_TIME" ]]; then
        STARTUP_START_TIME="$PROCESS_START_TIME"
    fi
    [[ "$PROCESS_START_TIME" == "$STARTUP_START_TIME" && "$PROCESS_STATE" != "Z" ]]
}

confirm_startup_group() {
    startup_leader_running || return 1
    [[ "$PROCESS_STATE" == "T" && "$PROCESS_PGID" == "$STARTUP_PID" ]] || return 1
    STARTUP_PGID="$STARTUP_PID"
    STARTUP_GROUP_CONFIRMED=1
}

group_has_live_members() {
    local wanted_group="$1" stat_path process_stat process_fields
    local -a fields
    [[ "$wanted_group" =~ ^[1-9][0-9]*$ ]] || return 1
    for stat_path in /proc/[0-9]*/stat; do
        [[ -r "$stat_path" ]] || continue
        if ! read -r process_stat 2>/dev/null < "$stat_path"; then
            continue
        fi
        process_fields="${process_stat##*) }"
        IFS=' ' read -r -a fields <<< "$process_fields"
        (( ${#fields[@]} > 2 )) || continue
        if [[ "${fields[2]}" == "$wanted_group" && "${fields[0]}" != "Z" ]]; then
            return 0
        fi
    done
    return 1
}

wait_for_leader_exit() {
    local attempts="$1" attempt
    for ((attempt = 0; attempt < attempts; attempt++)); do
        game_leader_running || return 0
        sleep 0.1
    done
    ! game_leader_running
}

wait_for_group_exit() {
    local group="$1" attempts="$2" attempt
    for ((attempt = 0; attempt < attempts; attempt++)); do
        group_has_live_members "$group" || return 0
        sleep 0.1
    done
    ! group_has_live_members "$group"
}

wait_for_startup_leader_exit() {
    local attempts="$1" attempt
    for ((attempt = 0; attempt < attempts; attempt++)); do
        startup_leader_running || return 0
        sleep 0.1
    done
    ! startup_leader_running
}

reap_startup_leader_if_exited() {
    local ignored_status
    if [[ -n "$STARTUP_PID" && "$STARTUP_REAPED" == "0" ]] && ! startup_leader_running; then
        set +e
        wait "$STARTUP_PID" 2>/dev/null
        ignored_status=$?
        set -e
        STARTUP_REAPED=1
    fi
}

reap_game_leader_if_exited() {
    local ignored_status
    if [[ -n "$GAME_PID" && "$GAME_REAPED" == "0" ]] && ! game_leader_running; then
        set +e
        wait "$GAME_PID" 2>/dev/null
        ignored_status=$?
        set -e
        GAME_REAPED=1
    fi
}

bounded_wineserver_stop() {
    if [[ -n "$STARTUP_DIAGNOSTIC_LOG" ]]; then
        timeout --signal=KILL --kill-after=1s 2s wineserver -k >> "$STARTUP_DIAGNOSTIC_LOG" 2>&1 || true
    else
        timeout --signal=KILL --kill-after=1s 2s wineserver -k >/dev/null 2>&1 || true
    fi
}

stop_startup_processes() {
    if (( STARTUP_GROUP_CONFIRMED == 1 )); then
        if group_has_live_members "$STARTUP_PGID"; then
            kill -TERM -- "-$STARTUP_PGID" 2>/dev/null || true
            kill -CONT -- "-$STARTUP_PGID" 2>/dev/null || true
        fi
        (( STARTUP_NEEDS_WINESERVER == 0 )) || bounded_wineserver_stop
        if ! wait_for_group_exit "$STARTUP_PGID" 20; then
            kill -KILL -- "-$STARTUP_PGID" 2>/dev/null || true
            wait_for_group_exit "$STARTUP_PGID" 20 || true
        fi
        reap_startup_leader_if_exited
        return 0
    fi

    if startup_leader_running; then
        kill -TERM "$STARTUP_PID" 2>/dev/null || true
        kill -CONT "$STARTUP_PID" 2>/dev/null || true
    fi
    (( STARTUP_NEEDS_WINESERVER == 0 )) || bounded_wineserver_stop
    if ! wait_for_startup_leader_exit 20; then
        kill -KILL "$STARTUP_PID" 2>/dev/null || true
        wait_for_startup_leader_exit 20 || true
    fi
    reap_startup_leader_if_exited
}

reset_startup_process() {
    STARTUP_PID=""
    STARTUP_PGID=""
    STARTUP_START_TIME=""
    STARTUP_GROUP_CONFIRMED=0
    STARTUP_REAPED=0
    STARTUP_NEEDS_WINESERVER=0
}

run_startup_command() {
    local description="$1" needs_wineserver="$2" command_status group_attempt record_attempt
    shift 2
    (( SHUTDOWN_REQUESTED == 0 )) || shutdown_supervisor "$SHUTDOWN_SIGNAL"
    validate_private_log "$STARTUP_DIAGNOSTIC_LOG" "Startup diagnostic log"
    reset_startup_process
    STARTUP_NEEDS_WINESERVER="$needs_wineserver"
    printf 'Running supervised startup step: %s.\n' "$description"

    STARTING_PROCESS=1
    setsid bash -c '
        readiness_read_fd="$1"
        readiness_write_fd="$2"
        shift 2
        if [[ "$readiness_read_fd" =~ ^[0-9]+$ ]]; then
            exec {readiness_read_fd}<&-
        fi
        if [[ "$readiness_write_fd" =~ ^[0-9]+$ ]]; then
            exec {readiness_write_fd}>&-
        fi
        kill -STOP "$$"
        exec "$@"
    ' aff-startup-command "$READINESS_READ_FD" "$READINESS_WRITE_FD" "$@" \
        >> "$STARTUP_DIAGNOSTIC_LOG" 2>&1 &
    STARTUP_PID=$!
    for ((record_attempt = 0; record_attempt < 20; record_attempt++)); do
        if read_process_record "$STARTUP_PID"; then
            STARTUP_START_TIME="$PROCESS_START_TIME"
            break
        fi
        sleep 0.01
    done
    STARTING_PROCESS=0
    [[ "$STARTUP_PID" =~ ^[1-9][0-9]*$ ]] || fail "Could not track startup command: $description"

    if (( SHUTDOWN_REQUESTED == 1 )); then
        shutdown_supervisor "$SHUTDOWN_SIGNAL"
    fi
    if [[ -z "$STARTUP_START_TIME" ]]; then
        reap_startup_leader_if_exited
        reset_startup_process
        fail "Could not record startup command identity: $description"
    fi

    for ((group_attempt = 0; group_attempt < 100; group_attempt++)); do
        confirm_startup_group && break
        startup_leader_running || break
        sleep 0.02
    done
    if (( STARTUP_GROUP_CONFIRMED == 0 )) && startup_leader_running; then
        stop_startup_processes
        reset_startup_process
        fail "Could not establish a process group for startup command: $description"
    fi
    if (( STARTUP_GROUP_CONFIRMED == 1 )); then
        kill -CONT "$STARTUP_PID" 2>/dev/null || true
    fi

    while :; do
        set +e
        wait "$STARTUP_PID"
        command_status=$?
        set -e
        if (( SHUTDOWN_REQUESTED == 1 )); then
            shutdown_supervisor "$SHUTDOWN_SIGNAL"
        fi
        if ! startup_leader_running; then
            STARTUP_REAPED=1
            break
        fi
    done

    if (( STARTUP_GROUP_CONFIRMED == 1 )) && group_has_live_members "$STARTUP_PGID"; then
        stop_startup_processes
    fi
    validate_private_log "$STARTUP_DIAGNOSTIC_LOG" "Startup diagnostic log"
    reset_startup_process
    return "$command_status"
}

stop_game_processes() {
    if (( GAME_GROUP_CONFIRMED == 1 )); then
        if group_has_live_members "$GAME_PGID"; then
            kill -TERM -- "-$GAME_PGID" 2>/dev/null || true
            kill -CONT -- "-$GAME_PGID" 2>/dev/null || true
        fi
        bounded_wineserver_stop
        if ! wait_for_group_exit "$GAME_PGID" 20; then
            kill -KILL -- "-$GAME_PGID" 2>/dev/null || true
            wait_for_group_exit "$GAME_PGID" 20 || true
        fi
        reap_game_leader_if_exited
        return 0
    fi

    if game_leader_running; then
        kill -TERM "$GAME_PID" 2>/dev/null || true
        kill -CONT "$GAME_PID" 2>/dev/null || true
    fi
    if ! wait_for_leader_exit 20; then
        kill -KILL "$GAME_PID" 2>/dev/null || true
        wait_for_leader_exit 20 || true
    fi
    reap_game_leader_if_exited
}

cleanup_residual_game_group() {
    (( GAME_GROUP_CONFIRMED == 1 )) || return 0
    if group_has_live_members "$GAME_PGID"; then
        kill -TERM -- "-$GAME_PGID" 2>/dev/null || true
        kill -CONT -- "-$GAME_PGID" 2>/dev/null || true
        bounded_wineserver_stop
    fi
    if ! wait_for_group_exit "$GAME_PGID" 20; then
        kill -KILL -- "-$GAME_PGID" 2>/dev/null || true
        wait_for_group_exit "$GAME_PGID" 20 || true
    fi
}

stop_log_follower() {
    local ignored_status
    [[ -n "$LOG_PID" ]] || return 0
    if (( LOG_GROUP_CONFIRMED == 1 )); then
        if group_has_live_members "$LOG_PGID"; then
            kill -TERM -- "-$LOG_PGID" 2>/dev/null || true
        fi
        if ! wait_for_group_exit "$LOG_PGID" 20; then
            kill -KILL -- "-$LOG_PGID" 2>/dev/null || true
            wait_for_group_exit "$LOG_PGID" 20 || true
        fi
    else
        kill -TERM "$LOG_PID" 2>/dev/null || true
        for ((ignored_status = 0; ignored_status < 20; ignored_status++)); do
            kill -0 "$LOG_PID" 2>/dev/null || break
            sleep 0.1
        done
        kill -KILL "$LOG_PID" 2>/dev/null || true
    fi
    set +e
    wait "$LOG_PID" 2>/dev/null
    ignored_status=$?
    set -e
    LOG_PID=""
    LOG_PGID=""
    LOG_GROUP_CONFIRMED=0
}

close_readiness_ipc() {
    local ignored_status
    if [[ "$READINESS_WRITE_FD" =~ ^[0-9]+$ ]]; then
        exec {READINESS_WRITE_FD}>&- 2>/dev/null || true
        READINESS_WRITE_FD=""
    fi
    if [[ "$READINESS_READ_FD" =~ ^[0-9]+$ ]]; then
        exec {READINESS_READ_FD}<&- 2>/dev/null || true
        READINESS_READ_FD=""
    fi
    if [[ "$READINESS_RELAY_PID" =~ ^[1-9][0-9]*$ ]]; then
        kill -TERM "$READINESS_RELAY_PID" 2>/dev/null || true
        set +e
        wait "$READINESS_RELAY_PID" 2>/dev/null
        ignored_status=$?
        set -e
        READINESS_RELAY_PID=""
    fi
}

prepare_readiness_ipc() {
    local relay_read_fd relay_write_fd
    close_readiness_ipc
    coproc AFF_READINESS_RELAY { exec /bin/cat; }
    READINESS_RELAY_PID="$AFF_READINESS_RELAY_PID"
    relay_read_fd="${AFF_READINESS_RELAY[0]}"
    relay_write_fd="${AFF_READINESS_RELAY[1]}"

    exec {READINESS_READ_FD}< "/proc/self/fd/$relay_read_fd"
    exec {READINESS_WRITE_FD}> "/proc/self/fd/$relay_write_fd"
    exec {relay_read_fd}<&-
    exec {relay_write_fd}>&-
    [[ "$READINESS_READ_FD" =~ ^[0-9]+$ && "$READINESS_WRITE_FD" =~ ^[0-9]+$ ]] || \
        fail "Could not establish the private readiness channel."
}

consume_readiness_message() {
    local message
    (( READINESS_MESSAGE_CONSUMED == 0 )) || return 0
    [[ "$READINESS_READ_FD" =~ ^[0-9]+$ ]] || return 0
    if read -r -t 0.05 -u "$READINESS_READ_FD" message; then
        READINESS_MESSAGE_CONSUMED=1
        if [[ "$message" == "$READINESS_MESSAGE" ]] && \
            (( READINESS_EMITTED == 0 && SHUTDOWN_REQUESTED == 0 && \
                LOG_GROUP_CONFIRMED == 1 && GAME_GROUP_CONFIRMED == 1 )) && \
            group_has_live_members "$LOG_PGID" && group_has_live_members "$GAME_PGID" && \
            game_leader_running; then
            printf '%s\n' "$READINESS_TOKEN"
            READINESS_EMITTED=1
        fi
        exec {READINESS_READ_FD}<&- 2>/dev/null || true
        READINESS_READ_FD=""
    fi
}

shutdown_supervisor() {
    local signal_name="$1" exit_status=143
    (( SHUTTING_DOWN == 0 )) || return 0
    SHUTTING_DOWN=1
    SHUTDOWN_REQUESTED=1
    SHUTDOWN_SIGNAL="$signal_name"
    [[ "$signal_name" != "INT" ]] || exit_status=130
    trap '' INT TERM

    printf 'Shutdown requested (%s); stopping Angels Fall First.\n' "$signal_name"
    if [[ -n "$STARTUP_PID" ]]; then
        stop_startup_processes
    elif [[ -n "$GAME_PID" ]]; then
        stop_game_processes
    elif (( WINE_RUNTIME_STARTED == 1 )); then
        bounded_wineserver_stop
    fi
    stop_log_follower
    close_readiness_ipc
    trap - EXIT
    exit "$exit_status"
}

handle_shutdown_signal() {
    SHUTDOWN_REQUESTED=1
    SHUTDOWN_SIGNAL="$1"
    if (( STARTING_PROCESS == 1 )); then
        return 0
    fi
    shutdown_supervisor "$1"
}

cleanup_supervisor() {
    if [[ -n "$STARTUP_PID" ]]; then
        stop_startup_processes
    fi
    stop_log_follower
    close_readiness_ipc
}

log_filter_main() {
    local game_log="$1" readiness_message="$2" readiness_write_fd="$3" line emitted=0 tail_fd
    local readiness_pattern='^\[[0-9]+\.[0-9]+\] ScriptLog: AFFGameInfo_[[:alnum:]_]+_[0-9]+ Game match has been started'$'\r''?$'
    set -o pipefail
    exec {tail_fd}< <(
        exec {readiness_write_fd}>&-
        exec tail --lines=0 --follow=name --retry --sleep-interval=0.05 -- "$game_log"
    )
    while IFS= read -r -u "$tail_fd" line; do
        if (( emitted == 0 )) && [[ "$line" =~ $readiness_pattern ]]; then
            printf '%s\n' "$readiness_message" >&"$readiness_write_fd"
            exec {readiness_write_fd}>&-
            emitted=1
        fi
    done
}

follower_has_log_open() {
    local game_log="$1" stat_path process_stat process_fields descriptor target
    local -a fields
    for stat_path in /proc/[0-9]*/stat; do
        [[ -r "$stat_path" ]] || continue
        if ! read -r process_stat 2>/dev/null < "$stat_path"; then
            continue
        fi
        process_fields="${process_stat##*) }"
        IFS=' ' read -r -a fields <<< "$process_fields"
        (( ${#fields[@]} > 2 )) || continue
        [[ "${fields[2]}" == "$LOG_PGID" && "${fields[0]}" != "Z" ]] || continue
        for descriptor in "${stat_path%/stat}/fd/"*; do
            target="$(readlink -- "$descriptor" 2>/dev/null || true)"
            [[ "$target" == "$game_log" ]] && return 0
        done
    done
    return 1
}

start_log_follower() {
    local game_log="$1" attempt
    export -f log_filter_main
    STARTING_PROCESS=1
    setsid bash -c '
        readiness_write_fd="$1"
        shift
        kill -STOP "$$"
        for descriptor_path in /proc/self/fd/*; do
            descriptor="${descriptor_path##*/}"
            if [[ "$descriptor" =~ ^[0-9]+$ ]] && (( descriptor > 2 )) && \
                [[ "$descriptor" != "$readiness_write_fd" ]]; then
                exec {descriptor}>&- 2>/dev/null || true
            fi
        done
        log_filter_main "$1" "$2" "$readiness_write_fd"
    ' aff-log-filter "$READINESS_WRITE_FD" "$game_log" "$READINESS_MESSAGE" \
        >> "$STARTUP_DIAGNOSTIC_LOG" 2>&1 &
    LOG_PID=$!
    STARTING_PROCESS=0
    export -n -f log_filter_main

    if (( SHUTDOWN_REQUESTED == 1 )); then
        shutdown_supervisor "$SHUTDOWN_SIGNAL"
    fi

    for ((attempt = 0; attempt < 100; attempt++)); do
        kill -0 "$LOG_PID" 2>/dev/null || fail "The Launch.log follower exited before the game started."
        if read_process_record "$LOG_PID" && \
            [[ "$PROCESS_STATE" == "T" && "$PROCESS_PGID" == "$LOG_PID" ]]; then
            LOG_PGID="$LOG_PID"
            LOG_GROUP_CONFIRMED=1
            break
        fi
        sleep 0.02
    done
    (( LOG_GROUP_CONFIRMED == 1 )) || fail "Could not establish the Launch.log follower process group."
    exec {READINESS_WRITE_FD}>&-
    READINESS_WRITE_FD=""
    kill -CONT "$LOG_PID" 2>/dev/null || true

    for ((attempt = 0; attempt < 100; attempt++)); do
        group_has_live_members "$LOG_PGID" || fail "The Launch.log follower exited before the game started."
        follower_has_log_open "$game_log" && return 0
        sleep 0.02
    done
    fail "Could not establish the Launch.log follower before game launch."
}

trap 'handle_shutdown_signal INT' INT
trap 'handle_shutdown_signal TERM' TERM
trap '' USR1 USR2
trap cleanup_supervisor EXIT

require_fixed STEAM_APP_ID "$APP_ID" 407480
require_fixed STEAM_USER "$STEAM_USER" anonymous
require_fixed WINDOWS_INSTALL "$WINDOWS_INSTALL" 1
require_fixed AUTO_UPDATE "$AUTO_UPDATE" 0
require_fixed WINETRICKS_RUN "$WINETRICKS_RUN" ""
require_fixed WINEARCH "$WINEARCH" win64
require_fixed WINEDEBUG "$WINEDEBUG" -all
require_fixed WINEPREFIX "$WINEPREFIX" "$SERVER_ROOT/.wine"
require_boolean01 AFF_AUTO_UPDATE "$AFF_AUTO_UPDATE"
require_boolean01 VALIDATE "$VALIDATE"

require_text SERVER_NAME "$SERVER_NAME" 1 80
require_text SERVER_DESCRIPTION "$SERVER_DESCRIPTION" 1 255
require_text MOTD "$MOTD" 1 255
require_password ADMIN_PASSWORD "$ADMIN_PASSWORD" 8
case "${ADMIN_PASSWORD,,}" in
    password|changeme|admin123|admin|password1)
        fail "ADMIN_PASSWORD must not use a known placeholder."
        ;;
esac
is_known_map "$DEFAULT_MAP" || fail "DEFAULT_MAP is not one of the installed-payload map tokens."
validate_map_cycle "$MAP_CYCLE"
require_uint SERVER_PORT "${SERVER_PORT:?SERVER_PORT is required}" 1024 65535
require_uint QUERY_PORT "$QUERY_PORT" 1024 65535
(( 10#$SERVER_PORT != 10#$QUERY_PORT )) || fail "QUERY_PORT must be a separate allocation from SERVER_PORT."
require_uint MAX_PLAYERS "$MAX_PLAYERS" 2 64
require_uint NUM_PLAY "$NUM_PLAY" 0 64
(( 10#$NUM_PLAY <= 10#$MAX_PLAYERS )) || fail "NUM_PLAY cannot exceed MAX_PLAYERS."
require_game_boolean BALANCE_BOTS "$BALANCE_BOTS"
require_uint AI_LEVEL "$AI_LEVEL" 1 5
require_uint BRIEFING_TIME "$BRIEFING_TIME" 0 2147483647
require_game_boolean RANDOM_COMMANDER "$RANDOM_COMMANDER"
require_uint RED_STARTING_TICKETS "$RED_STARTING_TICKETS" 10 2147483647
require_uint BLUE_STARTING_TICKETS "$BLUE_STARTING_TICKETS" 10 2147483647
require_uint FRIENDLY_FIRE "$FRIENDLY_FIRE" 0 100

[[ -d "$SERVER_ROOT" && ! -L "$SERVER_ROOT" ]] || fail "Container root is not a real directory."
cd "$SERVER_ROOT"

command -v setsid >/dev/null || fail "The runtime image does not provide setsid."
command -v timeout >/dev/null || fail "The runtime image does not provide timeout."
PRIVATE_RUNTIME_ROOT="$SERVER_ROOT/.pterodactyl"
STARTUP_DIAGNOSTIC_LOG="$PRIVATE_RUNTIME_ROOT/startup-diagnostics.log"
WINE_DIAGNOSTIC_LOG="$PRIVATE_RUNTIME_ROOT/wine-diagnostics.log"
prepare_private_directory "$PRIVATE_RUNTIME_ROOT"
prepare_private_log "$STARTUP_DIAGNOSTIC_LOG" "Startup diagnostic log"
prepare_private_log "$WINE_DIAGNOSTIC_LOG" "Wine diagnostic log"

STEAMCMD="$SERVER_ROOT/steamcmd/steamcmd.sh"
if [[ "$AFF_AUTO_UPDATE" == "1" ]]; then
    [[ -x "$STEAMCMD" && ! -L "$STEAMCMD" ]] || fail "SteamCMD is missing or unsafe; reinstall the server."
    update_args=(
        +force_install_dir "$SERVER_ROOT"
        +login anonymous
        +app_update 1007
    )
    [[ "$VALIDATE" == "0" ]] || update_args+=(validate)
    update_args+=(
        +@sSteamCmdForcePlatformType windows
        +app_update 407480
    )
    [[ "$VALIDATE" == "0" ]] || update_args+=(validate)
    update_args+=(+quit)
    run_startup_command "SteamCMD update" 0 "$STEAMCMD" "${update_args[@]}"
fi

PREREQUISITE_MANIFEST="$SERVER_ROOT/steamapps/appmanifest_1007.acf"
SERVER_MANIFEST="$SERVER_ROOT/steamapps/appmanifest_407480.acf"
validate_app_manifest "$PREREQUISITE_MANIFEST" 1007 || \
    fail "Steam manifest for prerequisite App 1007 is malformed, mismatched, or incomplete. Reinstall the server."
validate_app_manifest "$SERVER_MANIFEST" 407480 || \
    fail "Steam manifest for App 407480 is malformed, mismatched, or incomplete. Reinstall the server."
SERVER_EXE="$SERVER_ROOT/Binaries/Win64/AFFGameServer.exe"
require_regular_file "$SERVER_EXE" "Binaries/Win64/AFFGameServer.exe"

COOKED_ROOT="$SERVER_ROOT/AFFGame/CookedPCServer"
ensure_real_directory "$SERVER_ROOT/AFFGame"
ensure_real_directory "$COOKED_ROOT"
require_regular_file "$COOKED_ROOT/$DEFAULT_MAP.upk" "Selected default map package $DEFAULT_MAP.upk"
IFS=',' read -r -a cycle_maps <<< "$MAP_CYCLE"
for map_token in "${cycle_maps[@]}"; do
    require_regular_file "$COOKED_ROOT/$map_token.upk" "Selected map-cycle package $map_token.upk"
done

CONFIG_ROOT="$SERVER_ROOT/AFFGame/Config"
TEMPLATE_ROOT="$CONFIG_ROOT/PCServer/Cooked"
INSTANCE_CONFIG="$CONFIG_ROOT/Pterodactyl"
ensure_real_directory "$SERVER_ROOT/AFFGame"
ensure_real_directory "$CONFIG_ROOT"
ensure_real_directory "$CONFIG_ROOT/PCServer"
ensure_real_directory "$TEMPLATE_ROOT"
ensure_real_directory "$INSTANCE_CONFIG"

GAME_INI="$INSTANCE_CONFIG/PCServer-AFFGame.ini"
ENGINE_INI="$INSTANCE_CONFIG/PCServer-AFFEngine.ini"
bootstrap_config "$TEMPLATE_ROOT/PCServer-AFFGame.ini" "$GAME_INI"
bootstrap_config "$TEMPLATE_ROOT/PCServer-AFFEngine.ini" "$ENGINE_INI"

cycle_entries=""
for map_token in "${cycle_maps[@]}"; do
    [[ -z "$cycle_entries" ]] || cycle_entries+=","
    cycle_entries+="\"$map_token\""
done
map_cycle_value="(GameClassName=\"AFFGameInfo\",Maps=($cycle_entries))"

patch_ini_value "$GAME_INI" "AFFGame.AFFGameReplicationInfo" "ServerName" "$SERVER_NAME"
patch_ini_value "$GAME_INI" "AFFGame.AFFGameReplicationInfo" "ServerDescription" "$SERVER_DESCRIPTION"
patch_ini_value "$GAME_INI" "AFFGame.AFFGameReplicationInfo" "MessageOfTheDay" "$MOTD"
patch_ini_value "$GAME_INI" "Engine.AccessControl" "AdminPassword" "$ADMIN_PASSWORD"
patch_ini_value "$GAME_INI" "UTGame.UTGame" "GameSpecificMapCycles" "$map_cycle_value"
patch_ini_value "$ENGINE_INI" "URL" "Port" "$SERVER_PORT"
patch_ini_value "$ENGINE_INI" "OnlineSubsystemSteamworks.OnlineSubsystemSteamworks" "QueryPort" "$QUERY_PORT"

command -v wine >/dev/null || fail "The runtime image does not provide wine."
command -v wineboot >/dev/null || fail "The runtime image does not provide wineboot."
command -v wineserver >/dev/null || fail "The runtime image does not provide wineserver."
command -v tail >/dev/null || fail "The runtime image does not provide tail."
ensure_real_directory "$WINEPREFIX"
export WINEARCH WINEDEBUG WINEPREFIX
WINE_RUNTIME_STARTED=1
run_startup_command "wineboot -u" 1 wineboot -u
run_startup_command "wineserver -w" 1 wineserver -w

vcruntime="$WINEPREFIX/drive_c/windows/system32/vcruntime140.dll"
msvcp="$WINEPREFIX/drive_c/windows/system32/msvcp140.dll"
if [[ ! -s "$vcruntime" || ! -s "$msvcp" ]]; then
    command -v winetricks >/dev/null || fail "VC++ 2015-2022 runtime is missing and winetricks is unavailable."
    run_startup_command "winetricks vcrun2022" 1 winetricks -q vcrun2022
    run_startup_command "wineserver -w after winetricks" 1 wineserver -w
fi
[[ -f "$vcruntime" && ! -L "$vcruntime" && -s "$vcruntime" ]] || fail "VC++ runtime vcruntime140.dll is missing."
[[ -f "$msvcp" && ! -L "$msvcp" && -s "$msvcp" ]] || fail "VC++ runtime msvcp140.dll is missing."

launch_url="$DEFAULT_MAP?NumPlay=$NUM_PLAY?MaxPlayers=$MAX_PLAYERS?BalanceBots=$BALANCE_BOTS?AILevel=$AI_LEVEL?BriefingTime=$BRIEFING_TIME?RandomCommander=$RANDOM_COMMANDER?RedStartingTickets=$RED_STARTING_TICKETS?BlueStartingTickets=$BLUE_STARTING_TICKETS?FriendlyFire=$FRIENDLY_FIRE"

LOG_ROOT="$SERVER_ROOT/AFFGame/Logs"
GAME_LOG="$SERVER_ROOT/$GAME_LOG_RELATIVE"
ensure_real_directory "$LOG_ROOT"
if [[ -L "$GAME_LOG" || (-e "$GAME_LOG" && ! -f "$GAME_LOG") ]]; then
    fail "Refusing unsafe game log: $GAME_LOG"
fi
if [[ ! -e "$GAME_LOG" ]]; then
    : > "$GAME_LOG"
fi
[[ -f "$GAME_LOG" && ! -L "$GAME_LOG" && -r "$GAME_LOG" ]] || fail "The game log is unsafe or unreadable."
require_single_link "$GAME_LOG" "Game log"
prepare_readiness_ipc
start_log_follower "$GAME_LOG"

printf 'Launching Angels Fall First: game UDP %s, query UDP %s, map %s; waiting for fresh Launch.log readiness.\n' \
    "$SERVER_PORT" "$QUERY_PORT" "$DEFAULT_MAP"

STARTING_PROCESS=1
validate_private_log "$WINE_DIAGNOSTIC_LOG" "Wine diagnostic log"
setsid bash -c '
    readiness_read_fd="$1"
    readiness_write_fd="$2"
    shift 2
    if [[ "$readiness_read_fd" =~ ^[0-9]+$ ]]; then
        exec {readiness_read_fd}<&-
    fi
    if [[ "$readiness_write_fd" =~ ^[0-9]+$ ]]; then
        exec {readiness_write_fd}>&-
    fi
    kill -STOP "$$"
    exec "$@"
' aff-game "$READINESS_READ_FD" "$READINESS_WRITE_FD" \
    wine "$SERVER_EXE" "$launch_url" "-Port=$SERVER_PORT" -configsubdir=Pterodactyl -log -seekfreeloadingserver \
    >> "$WINE_DIAGNOSTIC_LOG" 2>&1 &
GAME_PID=$!
[[ "$GAME_PID" =~ ^[1-9][0-9]*$ ]] || fail "Could not track the Wine server process."
for ((record_attempt = 0; record_attempt < 20; record_attempt++)); do
    if read_process_record "$GAME_PID"; then
        GAME_START_TIME="$PROCESS_START_TIME"
        break
    fi
    sleep 0.01
done
STARTING_PROCESS=0

if (( SHUTDOWN_REQUESTED == 1 )); then
    shutdown_supervisor "$SHUTDOWN_SIGNAL"
fi
if [[ -z "$GAME_START_TIME" ]]; then
    reap_game_leader_if_exited
    fail "Could not record the Wine server process identity."
fi

for ((group_attempt = 0; group_attempt < 100; group_attempt++)); do
    confirm_game_group && break
    game_leader_running || break
    sleep 0.02
done
if (( GAME_GROUP_CONFIRMED == 0 )) && game_leader_running; then
    stop_game_processes
    fail "Could not establish the Wine server process group."
fi
if (( GAME_GROUP_CONFIRMED == 1 )); then
    kill -CONT "$GAME_PID" 2>/dev/null || true
fi

while game_leader_running; do
    if (( SHUTDOWN_REQUESTED == 1 )); then
        shutdown_supervisor "$SHUTDOWN_SIGNAL"
    fi
    consume_readiness_message
done
set +e
wait "$GAME_PID"
game_status=$?
set -e
GAME_REAPED=1

if (( GAME_GROUP_CONFIRMED == 1 )); then
    cleanup_residual_game_group
fi
stop_log_follower
close_readiness_ipc
validate_private_log "$WINE_DIAGNOSTIC_LOG" "Wine diagnostic log"
trap - EXIT INT TERM
exit "$game_status"
AFF_START_SH

bash -n "$START_TMP"
prepare_regular_destination "$SERVER_DIR/start.sh"
install -m 0755 -- "$START_TMP" "$SERVER_DIR/start.sh"
require_regular_file "$SERVER_DIR/start.sh" "Runtime start script"
[[ -x "$SERVER_DIR/start.sh" ]] || fail "Runtime start script is not executable."

printf 'Installation complete: Steam prerequisite App 1007 and Angels Fall First App 407480 are installed.\n'
