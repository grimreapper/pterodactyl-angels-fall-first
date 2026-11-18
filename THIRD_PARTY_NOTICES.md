# Third-party notices

This project does not bundle or redistribute the software listed below. It provides automation that downloads or invokes third-party components under their own terms.

## Angels Fall First Dedicated Server

- Dedicated-server Steam App ID: `407480`
- Game/client Steam App ID: `367270`
- Publisher/developer setup: <https://steamcommunity.com/app/367270/discussions/0/481115363859382148/>

The server payload, game assets, names, trademarks, and other copyrighted content remain the property of their respective owners. The project MIT license does not apply to them. Client users must obtain the game through an authorized channel and comply with its license terms.

## SteamCMD and Steam

SteamCMD is downloaded from Valve's HTTPS distribution endpoint during installation and used for anonymous server installation. It is not included in the release ZIP. Steam and SteamCMD are subject to Valve's applicable terms and licenses.

## Wine, Winetricks, and Microsoft Visual C++ runtime

The selected external runtime container supplies Wine and Winetricks. Winetricks installs the Microsoft Visual C++ 2015–2022 runtime into the server's Wine prefix. These components are not included in this repository or release ZIP and remain subject to their own licenses and redistribution terms.

## Pelican Eggs container images

The egg references external installer and Wine runtime images published at `ghcr.io/pelican-eggs`. Those images are not copied into the artifact and are governed by their upstream projects and included component licenses.
