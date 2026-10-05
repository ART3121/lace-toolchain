#!/usr/bin/env python3
"""Gera o packages.lock.json a partir do packages.txt.

    python scripts/lock.py

Resolve as dependências de cada pacote pedido contra o banco de hoje dos
repositórios do MSYS2 e grava cada pacote do resultado com versão, arquivo e
SHA-256. Uma versão pedida que o banco já não tem (o MSYS2 só lista a mais
nova) é baixada da pasta do repositório, que guarda as antigas, e as
dependências dela saem do `.PKGINFO`.

O lock é o que o build usa. Rodar este script de novo é atualizar o bundle:
confira o que mudou (ele mostra) e rode o build, que testa os quatro fluxos.
Roda em qualquer sistema; só baixa.
"""

import json
import sys
import urllib.request

from common import (
    CACHE,
    LOCK,
    REPOS,
    REQUEST,
    Package,
    download,
    load_repo,
    package_file,
    package_url,
    parse_dependency,
    read_pkginfo,
    satisfies,
    say,
    sha256_of,
)


class RequestError(Exception):
    pass


def parse_request(path):
    """As entradas do packages.txt: o que pedir (com a versão travada, se
    houver), o que ignorar e o que vem do PyPI."""
    wants, ignores, pypi = [], set(), []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        words = raw.split("#", 1)[0].split()
        if not words:
            continue
        where = f"{path.name}:{number}"
        kind, args = words[0], words[1:]
        if kind in REPOS and len(args) in (1, 2):
            full = REPOS[kind]["prefix"] + args[0]
            wants.append((kind, full, args[1] if len(args) == 2 else None))
        elif kind == "ignore" and len(args) == 2 and args[0] in REPOS:
            ignores.add(REPOS[args[0]]["prefix"] + args[1])
        elif kind == "pypi" and len(args) == 2:
            pypi.append((args[0], args[1]))
        else:
            raise RequestError(f"{where}: não entendi a linha: {raw.strip()}")
    return wants, ignores, pypi


def repo_of(name):
    prefix = REPOS["ucrt64"]["prefix"]
    return "ucrt64" if name.startswith(prefix) else "msys"


class Resolver:
    def __init__(self, ignores):
        say("baixando os bancos dos repositórios")
        self.repos = {repo: load_repo(repo) for repo in REPOS}
        self.ignores = ignores
        self.pins = {}
        self.chosen = {}
        self.edges = {}

    def exact(self, repo, name, version):
        """O pacote `name` na versão `version`: do banco, se for a atual, ou
        da pasta do repositório."""
        current = self.repos[repo].get(name)
        if current and current.version == version:
            return current
        arches = dict.fromkeys([REPOS[repo]["arch"], "any"])
        errors = []
        for arch in arches:
            file = package_file(repo, name, version, arch)
            path = CACHE / "pkgs" / file
            if not path.is_file():
                try:
                    download(package_url(repo, file), path)
                except RuntimeError as e:
                    errors.append(str(e))
                    continue
            info = read_pkginfo(path)
            return Package(
                repo=repo,
                name=name,
                version=version,
                file=file,
                sha256=sha256_of(path),
                size=path.stat().st_size,
                depends=info.get("depend", []),
                provides=info.get("provides", []),
                license=info.get("license", []),
                base=info.get("pkgbase", [name])[0],
            )
        raise RequestError(f"{name} {version} não está no repositório {repo}:\n  " + "\n  ".join(errors))

    def candidates(self, name):
        found = [pkgs[name] for pkgs in self.repos.values() if name in pkgs]
        if found:
            return found
        return [
            p
            for pkgs in self.repos.values()
            for p in pkgs.values()
            if p.provided_version(name) is not None
        ]

    def pick(self, dependency, wanted_by):
        name, op, version = parse_dependency(dependency)
        if name in self.ignores:
            return None
        for pkg in self.chosen.values():
            provided = pkg.provided_version(name)
            if provided is not None:
                if not satisfies(provided, op, version):
                    raise RequestError(
                        f"{wanted_by} pede {dependency}, e o lock já tem {pkg.name} {pkg.version}; "
                        "trave uma versão compatível no packages.txt"
                    )
                return pkg
        if name in self.pins:
            repo, pinned = self.pins[name]
            pkg = self.exact(repo, name, pinned)
            if not satisfies(pkg.version, op, version):
                raise RequestError(f"{wanted_by} pede {dependency}, e o packages.txt trava {name} {pinned}")
            return pkg
        found = self.candidates(name)
        if not found:
            raise RequestError(f"nenhum pacote atende {dependency} (pedido por {wanted_by})")
        if len(found) > 1:
            names = ", ".join(sorted(p.name for p in found))
            raise RequestError(
                f"vários pacotes atendem {name} (pedido por {wanted_by}): {names}; peça um deles no packages.txt"
            )
        pkg = found[0]
        provided = pkg.provided_version(name)
        if satisfies(provided, op, version):
            return pkg
        if op == "=" and pkg.name == name:
            return self.exact(repo_of(name), name, version)
        raise RequestError(f"{wanted_by} pede {dependency}, e o repositório tem {pkg.name} {pkg.version}")

    def resolve(self, wants):
        for repo, name, version in wants:
            if version:
                self.pins[name] = (repo, version)
        # Os travados primeiro: assim as dependências de versão exata deles
        # (o libgcc de um gcc travado, por exemplo) entram antes das soltas.
        ordered = sorted(wants, key=lambda w: w[2] is None)
        queue = [(name, "packages.txt") for _, name, _ in ordered]
        while queue:
            dependency, wanted_by = queue.pop(0)
            pkg = self.pick(dependency, wanted_by)
            if pkg is None:
                continue
            if wanted_by != "packages.txt":
                self.edges.setdefault(wanted_by, set()).add(pkg.name)
            if pkg.name in self.chosen:
                continue
            self.chosen[pkg.name] = pkg
            queue.extend((d, pkg.name) for d in pkg.depends)
        return self.chosen


def pypi_entry(name, version):
    """O arquivo de `name` `version` no PyPI: o fonte do cocotb (que o build
    compila) e o wheel puro dos outros."""
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=60) as response:
        data = json.load(response)
    files = data["urls"]
    if name == "cocotb":
        chosen = [f for f in files if f["packagetype"] == "sdist"]
    else:
        chosen = [f for f in files if f["filename"].endswith(("-py3-none-any.whl", "-py2.py3-none-any.whl"))]
    if not chosen:
        raise RequestError(f"{name} {version} não tem {'fonte' if name == 'cocotb' else 'wheel puro'} no PyPI")
    f = chosen[0]
    return {
        "name": name,
        "version": version,
        "file": f["filename"],
        "url": f["url"],
        "sha256": f["digests"]["sha256"],
    }


def describe_changes(old, new):
    """O que mudou de um lock para o outro, para quem vai revisar."""
    before = {p["name"]: p["version"] for p in old.get("msys2", []) + old.get("pypi", [])}
    after = {p["name"]: p["version"] for p in new["msys2"] + new["pypi"]}
    lines = []
    for name in sorted(before.keys() | after.keys()):
        a, b = before.get(name), after.get(name)
        if a == b:
            continue
        if a is None:
            lines.append(f"  + {name} {b}")
        elif b is None:
            lines.append(f"  - {name} {a}")
        else:
            lines.append(f"  ~ {name} {a} -> {b}")
    return lines


def main():
    try:
        wants, ignores, pypi = parse_request(REQUEST)
        resolver = Resolver(ignores)
        chosen = resolver.resolve(wants)
        requested = {name for _, name, _ in wants}
        msys2 = [
            {
                "repo": p.repo,
                "name": p.name,
                "version": p.version,
                "file": p.file,
                "sha256": p.sha256,
                "size": p.size,
                "depends": sorted(resolver.edges.get(p.name, ())),
                "requested": p.name in requested,
                "license": p.license,
                "base": p.base or p.name,
            }
            for p in sorted(chosen.values(), key=lambda p: (p.repo != "ucrt64", p.name))
        ]
        say("consultando o PyPI")
        python = [pypi_entry(name, version) for name, version in pypi]
    except RequestError as e:
        sys.exit(f"erro: {e}")

    lock = {
        "_doc": [
            "Gerado por scripts/lock.py a partir de packages.txt. Não edite à mão.",
            "msys2: cada pacote do bundle, com as dependências já resolvidas (depends).",
            "pypi: o fonte do cocotb e os wheels que o build dele usa.",
        ],
        "msys2": msys2,
        "pypi": python,
    }
    old = json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.is_file() else {}
    LOCK.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    total = sum(p["size"] or 0 for p in msys2)
    say(f"{LOCK.name}: {len(msys2)} pacotes do MSYS2 ({total / 2**20:.0f} MiB), {len(python)} do PyPI")
    for p in msys2:
        if p["requested"]:
            print(f"  {p['name']} {p['version']}")
    changes = describe_changes(old, lock)
    if old and changes:
        say("mudanças em relação ao lock anterior")
        print("\n".join(changes))
    elif old:
        say("nada mudou")


if __name__ == "__main__":
    main()
