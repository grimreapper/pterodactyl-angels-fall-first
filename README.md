# Angels Fall First Dedicated Server — Pterodactyl Egg

Public-ready PTDL_v2 project for the Windows-only Angels Fall First Dedicated Server (Steam App `407480`) under Wine. It combines the developer's [dedicated server setup](https://steamcommunity.com/app/367270/discussions/0/481115363859382148/) with disposable live validation of App `407480` build `25627237`.

This repository contains only egg metadata, shell wrappers, tests, and documentation. The game/server payload is downloaded directly from Steam during installation and is not included or redistributed. Players still need a Steam account that owns the Angels Fall First client; anonymous access to the dedicated-server tool does not grant a client license.

## Network allocations

Create two different UDP allocations on the Pterodactyl server:

- Primary game allocation: the built-in Pterodactyl `SERVER_PORT`, commonly `7777`.
- Steam query allocation: the port in `QUERY_PORT`, default `27015`.

Both are treated as UDP because that is the developer guidance. `QUERY_PORT` must be added to the server's allocations manually and must not equal the primary port. No TCP allocation is claimed by this egg.

Live validation observed both the configured game UDP listener and the query UDP listener. A direct query using the A2S `0x49` request returned a response. Real-client direct join, in-game or Steam browser discovery, Steam favorites behavior, and NAT behavior remain unverified.

## Import and install

1. Run `python3 build_artifacts.py --sync` and the local tests, or use a release ZIP produced from the same sources.
2. Import `egg-angels-fall-first.json` into a suitable Pterodactyl or Pelican nest.
3. Create the server with a primary UDP allocation and add a second UDP allocation matching `QUERY_PORT`.
4. Set a unique `ADMIN_PASSWORD`; the egg intentionally has no placeholder or default secret.
5. Review the other variables, install, and start the server.

The installer downloads the pinned SteamCMD archive over HTTPS and verifies its reviewed byte size and SHA-256. SteamCMD then logs in anonymously, installs prerequisite App `1007` first, forces the Windows platform, and installs App `407480`, in that exact order. A direct App `407480`-only attempt returned `Missing configuration`; the prerequisite order succeeded. Before reporting success, the installer requires complete matching manifests for both apps and a non-empty `Binaries/Win64/AFFGameServer.exe`. A future Valve SteamCMD archive rotation requires the pin in `install.sh` to be reviewed and updated.

The runtime uses `ghcr.io/pelican-eggs/yolks:wine_latest`; installation uses `ghcr.io/pelican-eggs/installers:debian`. The image-level `AUTO_UPDATE` and `WINETRICKS_RUN` hooks are fixed off. `start.sh` alone performs the optional update and conditionally installs `vcrun2022` when the required VC++ DLLs are absent, with each command under the same bounded supervisor.

## Configuration

The Steam payload provides PCServer templates under `AFFGame/Config/PCServer/Cooked`. The installer copies those files into the isolated `AFFGame/Config/Pterodactyl` subdirectory without overwriting an existing instance configuration. At every start, the wrapper builds each replacement in shell memory and writes it directly to the protected final INI, patching only the documented settings in:

- `PCServer-AFFEngine.ini`: `[URL] Port` and `[OnlineSubsystemSteamworks.OnlineSubsystemSteamworks] QueryPort`.
- `PCServer-AFFGame.ini`: server name, description, MOTD, `[Engine.AccessControl] AdminPassword`, and `[UTGame.UTGame] GameSpecificMapCycles`.

The executable is launched with the proven `-Port=<SERVER_PORT>` argument and the fixed `-configsubdir=Pterodactyl`, `-log`, and mandatory `-seekfreeloadingserver` switches. `-QueryPort` was ignored in live testing, so the query port is deliberately patched in `PCServer-AFFEngine.ini`. Unrelated vendor and operator settings are preserved. Steam validation can restore files that belong to the downloaded payload, but the isolated instance configuration is outside the Steam manifest and is preserved on reinstall.

At runtime, the wrapper requires every selected default and cycle entry to be a non-empty, non-symlinked `AFFGame/CookedPCServer/<token>.upk`. The map allowlist exactly matches the 37 package basenames observed in build `25627237`:

```text
AFF-Anaster-Territories, AFF-Ascella, AFF-Darsalaam-Territories, AFF-Darsalaam,
AFF-Delta-Territories, AFF-Errah-Territories, AFF-Errah, AFF-EyeOfGod-Territories,
AFF-Fortress-Conquest, AFF-Fortress-Territories, AFF-Fortress, AFF-Igneous,
AFF-Irega-Territories, AFF-Irega, AFF-Ixion-Territories, AFF-Ixion,
AFF-Kerrat-Territories, AFF-Kerrat, AFF-LaCroix-Territories, AFF-LaCroix,
AFF-Lellebah-Territories, AFF-Lukather-Territories, AFF-Lukather,
AFF-Majuro-Territories, AFF-Mazikeen-Territories, AFF-Meudeverre-Territories,
AFF-Meudeverre, AFF-Mirnoide, AFF-NB3345-Territories, AFF-Ossidiace-Territories,
AFF-Plains-Territories, AFF-Plains, AFF-Poranti, AFF-STR-space, AFF-STR,
AFF-Tannhauser, AFF-Universe
```

## Operator variables

| Variable | Default | Constraint or purpose |
| --- | --- | --- |
| `SERVER_NAME` | `Angels Fall First Server` | 1–80 characters |
| `SERVER_DESCRIPTION` | `Community Angels Fall First server` | 1–255 characters |
| `MOTD` | `Welcome to Angels Fall First!` | 1–255 characters |
| `ADMIN_PASSWORD` | empty | Required, 8–64 characters, common placeholders rejected |
| `DEFAULT_MAP` | `AFF-Ixion` | One observed installed map-package token |
| `MAP_CYCLE` | six maps | Comma-separated observed installed map-package tokens |
| `QUERY_PORT` | `27015` | Separate UDP allocation, 1024–65535 |
| `MAX_PLAYERS` | `32` | 2–64 |
| `NUM_PLAY` | `16` | 0–64 and no greater than `MAX_PLAYERS` |
| `BALANCE_BOTS` | `true` | `true` or `false` |
| `AI_LEVEL` | `3` | 1–5 |
| `BRIEFING_TIME` | `60` | Nonnegative seconds |
| `RANDOM_COMMANDER` | `true` | `true` or `false` |
| `RED_STARTING_TICKETS` | `500` | At least 10 |
| `BLUE_STARTING_TICKETS` | `500` | At least 10 |
| `FRIENDLY_FIRE` | `0` | Percentage, 0–100 |
| `AFF_AUTO_UPDATE` | `1` | `1` runs a supervised update before launch; `0` skips the update |
| `VALIDATE` | `0` | `1` validates during an automatic update |

The default is bot-filled and playable in intent: `AFF-Ixion`, 32 slots, and a minimum of 16 bodies with balancing enabled. The game developer warns that `FriendlyFire` was not extensively tested.

Every exposed value is checked again by `start.sh`, after panel validation. User-controlled strings reject control characters, CR/LF, quotes, and path separators. Passwords use a restricted URL/INI-safe character set. Map names and map-cycle entries use an exact allowlist. Startup arguments are passed directly to Wine without shell evaluation.

## Readiness, shutdown, and live acceptance

A disposable Wine 11 run launched the server process and loaded the selected map. Its `AFFGame/Logs/Launch.log` contained a timestamped `ScriptLog: AFFGameInfo_Incursion_0 Game match has been started` event. A private supervised follower opens the current log at EOF before Wine starts and, only for the exact anchored engine line, writes a fixed message through an anonymous inherited pipe whose write endpoint is closed in every startup and Wine child. The parent emits the wrapper-unique startup token `[AFF-EGG] SERVER READY 8f31c2` once only while both verified process groups remain live. Neither child output nor other log content is copied to panel stdout, so stale lines, signals, prefixes, suffixes, and child-spoofed marker text cannot produce readiness. The egg's custom log location remains `AFFGame/Logs/Launch.log`.

Raw SteamCMD, Wine bootstrap, winetricks, wineserver, and game-process output is retained under `.pterodactyl/` in separate startup and Wine diagnostic logs. The wrapper creates or validates these as single-link regular files with mode `0600`; they are intentionally not panel console output.

The wrapper installs `INT` and `TERM` traps before any supervised launch and does not treat a child PID as a process group until `/proc` proves that the live child is its own group leader. SteamCMD, `wineboot -u`, `wineserver -w`, conditional winetricks, Wine, and the log follower each run in a tracked `setsid` group. Before group confirmation, shutdown targets only the positive leader PID; afterward it targets the exact group. Both paths use hard deadlines and TERM-to-KILL escalation. Natural leader exits retain their original status while surviving descendants are stopped and reaped.

Live acceptance established a clean final-candidate Pterodactyl install with complete App `1007` and App `407480` manifests for build `25627237`, byte-identical runtime wrapper deployment, Wine 11 process startup, map load, both UDP listeners, a direct A2S `0x49` response, exactly one wrapper readiness token, a controlled Panel restart with full recovery, and a five-minute no-OOM/no-container-restart A2S soak. It did not establish real-client direct join, browser discovery, `AdminLogin`, public client joins, or map rotation. Those behaviors remain explicitly unverified.

## Local build and tests

```bash
python3 build_artifacts.py --sync
python3 -m unittest -v tests/test_egg.py
sha256sum -c SHA256SUMS
(cd dist && sha256sum -c SHA256SUMS)
unzip -t dist/angels-fall-first-pterodactyl-egg.zip
```

`build_artifacts.py` embeds `start.sh` byte-for-byte into `install.sh`, embeds `install.sh` byte-for-byte into the egg, writes source checksums, and creates a deterministic ZIP with fixed timestamps and modes. The archive contains no Steam/game payload.
