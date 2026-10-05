#!/usr/bin/env python3
"""Compila o cocotb e o instala no Python do bundle.

    python scripts/cocotb.py dist/msys

Roda no Windows, depois do assemble.py, com o Python, o gcc e o pip do
próprio bundle e só o bundle no PATH. Sem internet: o pip só enxerga os
arquivos do PyPI que o lock lista, conferidos pelo SHA-256.

O pacote do cocotb para Windows não traz a VPI do Verilator: o build dele só
a compila em sistemas POSIX. Por isso o cocotb sai do fonte, e a VPI do
Verilator é compilada à parte, estática, como a AURORA descobriu que funciona
(nipscernlab/aurora-toolchain):

- `-DPLI_DLLISPEC=` deixa as funções vpi_* sem `__declspec(dllimport)`, para
  casarem com as que o Verilator define dentro do executável do modelo;
- estática porque o Verilator liga a VPI no executável (não há dlopen);
- o runner do cocotb muda em três pontos (`RUNNER_PATCHES`).

Grava <saída>/../cocotb-files.json: os arquivos que o cocotb pôs no bundle.
"""

import argparse
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

from common import (
    CACHE,
    bundle_env,
    bundle_python,
    fetch_checked,
    load_lock,
    require_windows,
    run,
    say,
)

# As mudanças no cocotb_tools/runner.py do cocotb 2.1.0, classe Verilator.
# Cada uma precisa casar exatamente uma vez: um cocotb novo que mude o trecho
# para o build aqui, em vez de sair um bundle quebrado.
RUNNER_PATCHES = [
    (
        # O script verilator (Perl, sem extensão) não executa no Windows, e o
        # shutil.which não o acha: roda pelo perl.exe do bundle.
        "achar o verilator e rodá-lo pelo Perl",
        '''    def _simulator_in_path_build_only(self) -> None:
        executable = shutil.which("verilator")
        if executable is None:
            raise SystemExit("ERROR: verilator executable not found!")
        self.executable: str = executable
''',
        '''    def _simulator_in_path_build_only(self) -> None:
        # lace-toolchain: no Windows o verilator é um script Perl sem
        # extensão, que o Windows não executa e o shutil.which não acha.
        # Ele roda pelo perl.exe que está no PATH.
        if os.name == "nt":
            folders = os.environ.get("PATH", "").split(os.pathsep)
            scripts = [os.path.join(f, "verilator") for f in folders if f]
            script = next((s for s in scripts if os.path.isfile(s)), None)
            perl = shutil.which("perl")
            if script is None or perl is None:
                raise SystemExit("ERROR: verilator and perl must be in PATH!")
            self.executable: str = script
            self._verilator_command = [perl, script]
            return
        executable = shutil.which("verilator")
        if executable is None:
            raise SystemExit("ERROR: verilator executable not found!")
        self.executable: str = executable
        self._verilator_command = [executable]
''',
    ),
    (
        "chamar o verilator pelo comando acima",
        '''        cmds.append(
            [
                self.executable,
                "-cc",
''',
        '''        cmds.append(
            [
                *self._verilator_command,
                "-cc",
''',
    ),
    (
        # O wrapper Perl do verilator remonta os argumentos numa linha de
        # comando, e um -LDFLAGS com espaços se parte: cada opção vai num
        # -LDFLAGS próprio. Sem -Wl,-rpath (não existe em PE, e o verilator
        # escapa as vírgulas). A VPI estática não carrega as dependências:
        # o executável liga também a DLL do cocotb (libgpi; no 2.1 o gpilog e
        # o cocotbutils, que a AURORA ligava no 2.0, estão dentro dela; o
        # pacote não traz mais libgpilog nem libcocotbutils). A libstdc++ fica
        # dinâmica, a mesma libstdc++-6.dll da libgpi: com o gcc 16.2, a
        # libstdc++.a não tem o construtor de movimento do std::string que o
        # verilated.o usa (a AURORA, no gcc 15, ligava estática).
        "ligar a VPI estática",
        '''                "-LDFLAGS",
                f"-Wl,-rpath,{cocotb_tools.config.libs_dir} -L{cocotb_tools.config.libs_dir} -lcocotbvpi_verilator",
''',
        '''                "-LDFLAGS", f"-L{cocotb_tools.config.libs_dir}",
                "-LDFLAGS", "-lcocotbvpi_verilator",
                "-LDFLAGS", "-lgpi",
''',
    ),
]

VPI_SOURCES = ["VpiImpl", "VpiCbHdl", "VpiObj", "VpiIterator", "VpiSignal"]


def patch_runner(source: Path) -> None:
    runner = source / "src" / "cocotb_tools" / "runner.py"
    text = runner.read_text(encoding="utf-8")
    for what, old, new in RUNNER_PATCHES:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"runner.py: o trecho para {what} aparece {count} vezes (esperado: 1)")
        text = text.replace(old, new)
        print(f"  runner.py: {what}")
    runner.write_text(text, encoding="utf-8")


def snapshot(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bundle", type=Path, help="a pasta do bundle (dist/msys)")
    args = parser.parse_args()
    require_windows("O build do cocotb")
    bundle = args.bundle.resolve()
    python = bundle_python(bundle)
    env = bundle_env(bundle)

    build = CACHE / "build"
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)

    # A pasta que o pip enxerga tem só os arquivos deste lock: o cache pode
    # guardar versões de locks antigos, e o pip escolheria a mais nova.
    lock = load_lock()
    say("conferindo os arquivos do PyPI")
    wheelhouse = build / "wheels"
    wheelhouse.mkdir()
    files = {}
    for entry in lock["pypi"]:
        cached = fetch_checked([entry["url"]], CACHE / "pypi" / entry["file"], entry["sha256"])
        files[entry["name"]] = shutil.copy2(cached, wheelhouse / entry["file"])
    cocotb = next(e for e in lock["pypi"] if e["name"] == "cocotb")

    with tarfile.open(files["cocotb"]) as tar:
        tar.extractall(build, filter="data")
    source = build / f"cocotb-{cocotb['version']}"
    say(f"corrigindo o runner do cocotb {cocotb['version']}")
    patch_runner(source)

    # O pip do próprio Python do bundle (o wheel que o ensurepip guarda),
    # rodado direto do wheel: nada do pip fica instalado no bundle. O
    # EXTERNALLY-MANAGED do Python do MSYS2 fica, para o usuário instalar
    # pacotes num venv; aqui ele é contornado de propósito.
    pips = sorted(bundle.glob("ucrt64/lib/python3.*/ensurepip/_bundled/pip-*.whl"))
    if not pips:
        raise SystemExit("o Python do bundle não traz o pip do ensurepip")
    before = snapshot(bundle)
    # As dependências do cocotb (find_libpython, pytest e as do pytest) o pip
    # acha na mesma pasta; as do build ficam só no ambiente isolado dele.
    say("compilando e instalando o cocotb no Python do bundle")
    run(
        [
            python,
            pips[-1] / "pip",
            "install",
            "--no-index",
            "--find-links",
            wheelhouse,
            "--break-system-packages",
            "--no-cache-dir",
            "--no-warn-script-location",
            "--disable-pip-version-check",
            source,
        ],
        env=env,
        cwd=build,
    )

    libs = sorted(bundle.glob("ucrt64/lib/python3.*/site-packages/cocotb/libs"))
    if not libs:
        raise SystemExit("o cocotb não ficou em site-packages/cocotb/libs")
    libs = libs[-1]

    # As mesmas fontes e definições da VPI do Icarus que o build compila
    # (_get_vpi_lib_ext em cocotb_build_libs.py), com VERILATOR no lugar de
    # ICARUS e o vpi_user.h que o cocotb traz (src/cocotb/_vendor/vpi).
    say("compilando a VPI do Verilator (estática)")
    objects = build / "vpi_verilator"
    objects.mkdir()
    share = source / "src" / "cocotb" / "share"
    for name in VPI_SOURCES:
        run(
            [
                bundle / "ucrt64" / "bin" / "g++.exe",
                "-O2",
                "-std=c++11",
                "-fvisibility=hidden",
                "-fvisibility-inlines-hidden",
                "-DCOCOTBVPI_EXPORTS=",
                "-DVERILATOR=",
                "-D__STDC_FORMAT_MACROS=",
                "-DPLI_DLLISPEC=",
                f"-I{share / 'include'}",
                f"-I{source / 'src' / 'cocotb'}",
                "-c",
                share / "lib" / "gpi" / "vpi" / f"{name}.cpp",
                "-o",
                objects / f"{name}.o",
            ],
            env=env,
        )
    archive = objects / "libcocotbvpi_verilator.a"
    run(
        [
            bundle / "ucrt64" / "bin" / "ar.exe",
            "rcs",
            archive,
            *(objects / f"{n}.o" for n in VPI_SOURCES),
        ],
        env=env,
    )
    shutil.copy2(archive, libs / archive.name)

    # A VPI do Icarus tem o nome que o cocotb dá a ela no Windows (o build
    # com o Python do MSYS2 não segue o do Linux): quem diz é o próprio
    # cocotb, pelo caminho que o runner passa ao vvp.
    found = subprocess.run(
        [python, "-m", "cocotb_tools.config", "--lib-name-path", "vpi", "icarus"],
        env=env,
        capture_output=True,
        text=True,
    )
    icarus = Path(found.stdout.strip()) if found.returncode == 0 else None
    print(f"  cocotb/libs: {', '.join(sorted(p.name for p in libs.iterdir()))}")
    if icarus is None or not icarus.is_file():
        raise SystemExit(
            f"a VPI do Icarus não está onde o cocotb a procura ({found.stdout.strip() or found.stderr.strip()})"
        )
    if not (libs / archive.name).is_file():
        raise SystemExit(f"falta {archive.name} em {libs}")
    say(f"VPIs: {icarus.name}, {archive.name}")

    added = sorted(snapshot(bundle) - before)
    listing = bundle.parent / "cocotb-files.json"
    listing.write_text(json.dumps(added, indent=1) + "\n", encoding="utf-8")
    say(f"cocotb {cocotb['version']} no bundle: {len(added)} arquivos")


if __name__ == "__main__":
    main()
