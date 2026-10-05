#!/usr/bin/env python3
"""Tira do bundle o que nenhum fluxo usa.

    python scripts/trim.py dist/msys

Roda depois do cocotb.py (que ainda precisa do ensurepip e dos cabeçalhos do
Python) e antes do smoke.py, que é quem diz se o corte foi longe demais: um
corte novo só fica se os quatro fluxos continuarem passando.

Dois tipos de corte:

- `REMOVE`: caminhos que saem, venham de que pacote vierem;
- `DLL_ONLY`: pacotes que só entram pelas DLLs que os outros carregam. Deles
  saem cabeçalhos, bibliotecas estáticas, executáveis e documentação; ficam
  as DLLs, as licenças e o que estiver em `DLL_ONLY_KEEPS`.

O que não pode sair: o g++ inteiro (cc1plus, as, ld, os cabeçalhos do C++ e
do Windows, as bibliotecas do C e do Windows), porque ele compila o modelo do
Verilator na máquina do usuário; o zlib com cabeçalho e biblioteca, que o
modelo liga quando grava FST; o Python com a biblioteca padrão e o cocotb;
Verilator, Icarus e Perl; e a camada MSYS inteira.
"""

import argparse
import json
from pathlib import Path, PurePosixPath

from common import REPOS, say

REMOVE = [
    # Documentação e traduções (o Lace roda as ferramentas com LC_ALL=C).
    ("ucrt64/share/man/**", "manuais"),
    ("ucrt64/share/doc/**", "documentação"),
    ("ucrt64/share/info/**", "documentação"),
    ("ucrt64/share/gtk-doc/**", "documentação"),
    ("ucrt64/share/locale/**", "traduções"),
    ("ucrt64/share/gettext/**", "ferramentas do gettext"),
    ("ucrt64/share/aclocal/**", "macros do autotools"),
    ("ucrt64/share/gdb/**", "scripts do gdb, que o bundle não traz"),
    ("ucrt64/lib/perl5/core_perl/pod/**", "documentação do Perl"),
    # Python: a suíte de testes dele (160 MiB), a IDE e o Tkinter (o tk fica
    # fora do lock), e o ensurepip, que o cocotb.py já usou.
    ("ucrt64/lib/python3.*/test/**", "testes do Python"),
    ("ucrt64/lib/python3.*/idlelib/**", "IDLE"),
    ("ucrt64/lib/python3.*/tkinter/**", "Tkinter"),
    ("ucrt64/lib/python3.*/turtledemo/**", "demos do turtle"),
    ("ucrt64/lib/python3.*/ensurepip/**", "ensurepip"),
    ("ucrt64/lib/python3.*/lib-dynload/_tkinter*", "Tkinter"),
    # gcc: o que não compila nem liga.
    ("ucrt64/bin/gcov*.exe", "cobertura do gcc"),
    ("ucrt64/bin/lto-dump.exe", "depuração do LTO"),
    # Verilator: os executáveis de depuração (só com --debug) e os exemplos.
    ("ucrt64/bin/verilator_bin_dbg.exe", "Verilator de depuração"),
    ("ucrt64/bin/verilator_coverage_bin_dbg.exe", "Verilator de depuração"),
    ("ucrt64/share/verilator/examples/**", "exemplos do Verilator"),
]

DLL_ONLY = [
    "bzip2",
    "expat",
    "gettext-runtime",
    "gmp",
    "isl",
    "libb2",
    "libffi",
    "libiconv",
    "libsystre",
    "libtre",
    "mpc",
    "mpdecimal",
    "mpfr",
    "ncurses",
    "openssl",
    "pcre2",
    "readline",
    "sqlite3",
    "termcap",
    "wineditline",
    "xz",
    "zstd",
]

# O que um pacote DLL_ONLY leva além das DLLs, porque as DLLs leem em tempo
# de execução.
DLL_ONLY_KEEPS = {
    "ncurses": ["ucrt64/share/terminfo/**", "ucrt64/lib/terminfo/**", "ucrt64/share/tabset/**"],
    "openssl": ["ucrt64/lib/engines-3/**", "ucrt64/lib/ossl-modules/**", "ucrt64/etc/ssl/**"],
}


def matches(path: str, patterns: list[str]) -> bool:
    p = PurePosixPath(path)
    return any(p.full_match(pattern) for pattern in patterns)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bundle", type=Path, help="a pasta do bundle (dist/msys)")
    args = parser.parse_args()
    bundle = args.bundle
    owners = json.loads((bundle.parent / "packages-files.json").read_text(encoding="utf-8"))

    doomed: dict[str, str] = {}
    files = [p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()]
    for path in files:
        for pattern, reason in REMOVE:
            if PurePosixPath(path).full_match(pattern):
                doomed[path] = reason
                break

    prefix = REPOS["ucrt64"]["prefix"]
    for short in DLL_ONLY:
        name = prefix + short
        if name not in owners:
            raise SystemExit(f"DLL_ONLY cita {short}, que não está no lock")
        keeps = ["ucrt64/bin/*.dll", "ucrt64/share/licenses/**", *DLL_ONLY_KEEPS.get(short, [])]
        for path in owners[name]:
            if not matches(path, keeps):
                doomed.setdefault(path, f"{short}: só a DLL")

    before = sum((bundle / p).stat().st_size for p in files)
    removed = 0
    by_reason: dict[str, int] = {}
    for path, reason in doomed.items():
        target = bundle / path
        if target.is_file():
            removed += target.stat().st_size
            by_reason[reason] = by_reason.get(reason, 0) + target.stat().st_size
            target.unlink()
    for folder in sorted((p for p in bundle.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()

    for reason, size in sorted(by_reason.items(), key=lambda item: -item[1])[:12]:
        print(f"  {size / 2**20:7.1f} MiB  {reason}")
    say(
        f"{len(doomed)} arquivos fora, {removed / 2**20:.0f} MiB; "
        f"o bundle tem {(before - removed) / 2**20:.0f} MiB"
    )


if __name__ == "__main__":
    main()
