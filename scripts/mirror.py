#!/usr/bin/env python3
"""Junta os pacotes do lock para a release que os guarda.

    python scripts/mirror.py dist/pkgs

O MSYS2 apaga pacotes antigos do repositório com o tempo. A release
`pkgs-<id>` guarda os deste lock, e o assemble.py a procura antes do MSYS2
(`LACE_TOOLCHAIN_REPO`). Copia cada pacote do cache, conferido, para a pasta
dada, com o nome que a release usa (`mirror_name`), e imprime o id do lock,
que dá nome à release. Roda em qualquer sistema.
"""

import argparse
import shutil
from pathlib import Path

from common import fetch_checked, load_lock, lock_id, mirror_name, package_cache, package_sources


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="a pasta onde os pacotes ficam")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for entry in load_lock()["msys2"]:
        path = fetch_checked(package_sources(entry, None), package_cache(entry), entry["sha256"])
        shutil.copy2(path, args.out / mirror_name(entry["file"]))
    print(lock_id())


if __name__ == "__main__":
    main()
