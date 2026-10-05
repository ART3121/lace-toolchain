#!/usr/bin/env python3
"""Empacota o bundle testado para a release.

    python scripts/package.py dist/msys --tag ucrt64-v1

Grava em dist/:

- lace-msys-<tag>.zip: o bundle, com msys/ na raiz (msys/ucrt64, msys/usr);
- lace-msys-<tag>.json: o que há no zip, pacote a pacote: versão, de quem
  depende e os arquivos que restaram depois do trim. É por ela que o Lace
  separa o bundle em aplicativos (Icarus, Verilator, cocotb);
- SHA256SUMS: o hash dos dois.

Roda depois do smoke.py. Roda em qualquer sistema.
"""

import argparse
import json
import zipfile
from pathlib import Path

from common import load_lock, lock_id, say, sha256_of, source_url

# Data fixa nas entradas do zip: o mesmo bundle dá o mesmo zip.
ZIP_DATE = (2026, 1, 1, 0, 0, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bundle", type=Path, help="a pasta do bundle (dist/msys)")
    parser.add_argument("--tag", required=True, help="a tag da release (ucrt64-vN)")
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    dist = bundle.parent
    name = f"lace-msys-{args.tag}"

    lock = load_lock()
    owners = json.loads((dist / "packages-files.json").read_text(encoding="utf-8"))
    cocotb_files = set(json.loads((dist / "cocotb-files.json").read_text(encoding="utf-8")))
    present = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file())
    present_set = set(present)

    packages = []
    owned = set()
    for entry in lock["msys2"]:
        files = [f for f in owners[entry["name"]] if f in present_set and f not in cocotb_files]
        owned.update(files)
        packages.append(
            {
                "name": entry["name"],
                "repo": entry["repo"],
                "version": entry["version"],
                "license": entry["license"],
                "source": source_url(entry["repo"], entry["base"], entry["version"]),
                "depends": entry["depends"],
                "files": files,
            }
        )
    cocotb = next(e for e in lock["pypi"] if e["name"] == "cocotb")
    python = next(e for e in lock["msys2"] if e["name"].endswith("-python"))
    packages.append(
        {
            "name": "cocotb",
            "repo": "pypi",
            "version": cocotb["version"],
            "license": ["BSD-3-Clause"],
            "source": cocotb["url"],
            "depends": [python["name"]],
            "files": sorted(f for f in cocotb_files if f in present_set),
        }
    )
    owned.update(cocotb_files)
    unowned = [f for f in present if f not in owned]
    if unowned:
        # Um arquivo sem pacote não tem como entrar num aplicativo do Lace.
        raise SystemExit(
            f"{len(unowned)} arquivos do bundle não são de nenhum pacote (o primeiro: {unowned[0]})"
        )

    manifest = {
        "tag": args.tag,
        "lock": lock_id(),
        "root": "msys",
        "versions": {
            e["name"].removeprefix("mingw-w64-ucrt-x86_64-"): e["version"]
            for e in lock["msys2"]
            if e["requested"]
        }
        | {"cocotb": cocotb["version"]},
        "packages": packages,
    }
    manifest_path = dist / f"{name}.json"
    manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    archive = dist / f"{name}.zip"
    say(f"compactando {len(present)} arquivos em {archive.name}")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in present:
            info = zipfile.ZipInfo(f"msys/{rel}", date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(bundle / rel, "rb") as src, zf.open(info, "w") as dst:
                while block := src.read(1 << 20):
                    dst.write(block)

    sums = dist / "SHA256SUMS"
    sums.write_text(
        "".join(f"{sha256_of(p)}  {p.name}\n" for p in (archive, manifest_path)),
        encoding="utf-8",
    )
    say(f"{archive.name}: {archive.stat().st_size / 2**20:.0f} MiB; {manifest_path.name}; {sums.name}")


if __name__ == "__main__":
    main()
