---
name: Playwright Chromium on Replit (NixOS)
description: How to get Playwright's downloaded Chromium binary to find its shared libraries in the Nix store
---

## The problem
Playwright downloads its own Chromium binary (not a NixOS-patched ELF). That binary looks for shared libraries in standard Linux paths (`/usr/lib`, etc.), but on Replit's NixOS environment those libraries only exist in `/nix/store` paths — not on `LD_LIBRARY_PATH` by default.

## The fix
1. Install missing system deps via `installSystemDependencies`: `glib`, `nspr`, `nss`, `atk`, `at-spi2-atk`, `at-spi2-core`, `dbus`, `xorg.libX11`, `xorg.libXcomposite`, `xorg.libXdamage`, `xorg.libXext`, `xorg.libXfixes`, `xorg.libXrandr`, `xorg.libxcb`, `libxkbcommon`, `alsa-lib`, `mesa`.
2. `libgbm.so.1` is NOT provided by the `mesa` Nix package — it is a separate package `mesa-libgbm`. Find its Nix store path with: `ls /nix/store/ | grep mesa-libgbm`.
3. Get all library paths via: `nix-shell -p <packages> --run 'echo $NIX_LDFLAGS'` then extract the `-L` paths.
4. Create a `run.sh` wrapper that exports `LD_LIBRARY_PATH` with all Nix store lib paths and then calls `python scrapa_alla.py`. Set the workflow command to `bash run.sh`.

**Why:** The Playwright-downloaded binary is a standard Linux ELF; `patchelf` or `LD_LIBRARY_PATH` are the only ways to make it find Nix-store libraries without recompiling.

**How to apply:** Any time Playwright or another pre-compiled binary is used in a Replit project and fails with "error while loading shared libraries: libXxx.so: cannot open shared object file".
