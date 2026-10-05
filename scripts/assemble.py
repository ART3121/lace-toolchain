#!/usr/bin/env python3
"""Monta o bundle a partir do packages.lock.json.

    python scripts/assemble.py dist/msys

Baixa cada pacote do lock, confere o SHA-256 e extrai em <saída>/ucrt64 e
<saída>/usr/bin. Os pacotes vêm do cache (.cache/pkgs), da release que os
guarda (`LACE_TOOLCHAIN_REPO` ou `LACE_TOOLCHAIN_MIRROR`) ou do repositório
do MSYS2, nessa ordem; o SHA-256 é o mesmo em todos.

Grava também <saída>/../packages-files.json: os arquivos de cada pacote, que
o scripts/package.py usa para a lista final. Roda em qualquer sistema.

Depois de extrair, corrige o que os pacotes trazem errado para o Windows
(`FILE_PATCHES`). Cada correção tem que casar exatamente uma vez: um pacote
novo que mude o trecho para o build, em vez de sair um bundle quebrado.
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


# (o que, arquivo no bundle, trecho, troca)
FILE_PATCHES = [
    (
        # O configure do Verilator do MSYS2 aceitou o -Wl,-export_dynamic do
        # macOS: o ld do MinGW o lê como -e xport_dynamic (o ponto de entrada
        # do executável) e só avisa, e o modelo sai com a entrada errada. No
        # Windows não há o que exportar: a VPI é estática.
        "verilated.mk sem o -export_dynamic do macOS",
        "ucrt64/share/verilator/include/verilated.mk",
        "CFG_LDFLAGS_DYNAMIC = -Wl,-export_dynamic\n",
        "CFG_LDFLAGS_DYNAMIC =\n",
    ),
    # O -Os liga o -fdeclone-ctor-dtor, e o g++ 16.2 do UCRT64 passa a chamar
    # os construtores na forma C4 (`...C4EOS4_`), que a libstdc++ dele não
    # exporta (só C1 e C2): um programa que move um std::string não liga.
    # Reproduzido no Wine com 4 linhas; com -O2, -O0 ou -Os
    # -fno-declone-ctor-dtor liga e roda. O Verilator compila o runtime
    # (verilated.cpp) e o modelo com -Os quando ninguém passa outro -O.
    (
        "verilated.mk: -Os sem o -fdeclone-ctor-dtor (OPT_FAST)",
        "ucrt64/share/verilator/include/verilated.mk",
        "OPT_FAST = -Os\n",
        "OPT_FAST = -Os -fno-declone-ctor-dtor\n",
    ),
    (
        "verilated.mk: -Os sem o -fdeclone-ctor-dtor (OPT_GLOBAL)",
        "ucrt64/share/verilator/include/verilated.mk",
        "OPT_GLOBAL = -Os\n",
        "OPT_GLOBAL = -Os -fno-declone-ctor-dtor\n",
    ),
]


def apply_patches(out: Path) -> None:
    for what, rel, old, new in FILE_PATCHES:
        path = out / rel
        text = path.read_text(encoding="utf-8")
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{rel}: o trecho para {what} aparece {count} vezes (esperado: 1)")
        path.write_text(text.replace(old, new), encoding="utf-8", newline="")
        print(f"  {rel}: {what}", flush=True)


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

    say("corrigindo os pacotes")
    apply_patches(out)

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
