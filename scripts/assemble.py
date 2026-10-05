#!/usr/bin/env python3
"""Monta o bundle a partir do packages.lock.json.

    python scripts/assemble.py dist/msys

Baixa cada pacote do lock, confere o SHA-256 e extrai em <saída>/ucrt64 e
<saída>/usr/bin. Os pacotes vêm do cache (.cache/pkgs), da release que os
guarda (`LACE_TOOLCHAIN_REPO` ou `LACE_TOOLCHAIN_MIRROR`) ou do repositório
do MSYS2, nessa ordem; o SHA-256 é o mesmo em todos.

Grava também <saída>/../packages-files.json: os arquivos de cada pacote, que
o scripts/package.py usa para a lista final. Roda em qualquer sistema.
"""

import argparse
import json
import shutil
from pathlib import Path

from common import (
    default_mirror,
    extract_package,
    fetch_checked,
    load_lock,
    package_cache,
    package_sources,
    say,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="a pasta do bundle (apagada antes)")
    parser.add_argument("--mirror", default=default_mirror(), help="a URL da release com os pacotes")
    args = parser.parse_args()

    lock = load_lock()
    out = args.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    say(f"{len(lock['msys2'])} pacotes" + (f" (espelho: {args.mirror})" if args.mirror else ""))
    files = {}
    installs = []
    for entry in lock["msys2"]:
        path = fetch_checked(package_sources(entry, args.mirror), package_cache(entry), entry["sha256"])
        files[entry["name"]] = extract_package(path, entry["repo"], out)
        print(f"  {entry['name']} {entry['version']}: {len(files[entry['name']])} arquivos", flush=True)
        if has_install_script(path):
            installs.append(entry["name"])

    listing = out.parent / "packages-files.json"
    listing.write_text(json.dumps(files, indent=1) + "\n", encoding="utf-8")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    say(f"montado em {out}: {sum(map(len, files.values()))} arquivos, {size / 2**20:.0f} MiB")
    if installs:
        # O pacman rodaria estes scripts depois de instalar; aqui eles não
        # rodam. Até hoje nenhum fez falta: o smoke é quem diz.
        say("pacotes com script de instalação que não rodou: " + ", ".join(installs))


def has_install_script(path: Path) -> bool:
    import tarfile

    with tarfile.open(path, "r:zst") as tar:
        return ".INSTALL" in tar.getnames()


if __name__ == "__main__":
    main()
