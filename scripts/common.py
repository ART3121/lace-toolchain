"""O que os scripts do lace-toolchain dividem: os arquivos do repositório, os
repositórios do MSYS2, a comparação de versões do pacman, o download conferido
pelo SHA-256 e a extração dos pacotes.

Só a biblioteca padrão. Pede o Python 3.14, o primeiro que lê zstd
(`compression.zstd`), o formato dos pacotes do MSYS2.
"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info < (3, 14):
    sys.exit("o lace-toolchain pede o Python 3.14 ou mais novo (para ler zstd)")

from compression import zstd  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REQUEST = ROOT / "packages.txt"
LOCK = ROOT / "packages.lock.json"
CACHE = ROOT / ".cache"
SMOKE = ROOT / "smoke"

# Os dois repositórios de onde o bundle sai. O UCRT64 tem os programas nativos
# de Windows; o MSYS, a camada POSIX (sh, make, coreutils).
REPOS = {
    "ucrt64": {
        "url": "https://repo.msys2.org/mingw/ucrt64",
        "db": "ucrt64.db",
        "prefix": "mingw-w64-ucrt-x86_64-",
        "arch": "any",
    },
    "msys": {
        "url": "https://repo.msys2.org/msys/x86_64",
        "db": "msys.db",
        "prefix": "",
        "arch": "x86_64",
    },
}

# A pasta de cada repositório dentro do bundle. Do MSYS só entra usr/bin (a
# AURORA mostrou que usr/lib, usr/share e etc/ não fazem falta) e as licenças.
KEEP = {"ucrt64": ("ucrt64/",), "msys": ("usr/bin/", "usr/share/licenses/")}

# Onde ficam os pacotes-fonte de cada repositório (`<base>-<versão>.src.tar.zst`).
SOURCES = {
    "ucrt64": "https://repo.msys2.org/mingw/sources",
    "msys": "https://repo.msys2.org/msys/sources",
}

# Os arquivos de controle do pacman, que não são do programa.
CONTROL = {".PKGINFO", ".BUILDINFO", ".MTREE", ".INSTALL", ".CHANGELOG"}


def say(message: str) -> None:
    print(f"==> {message}", flush=True)


# ---------------------------------------------------------------- versões


def _is_alpha(c: str) -> bool:
    return c.isascii() and c.isalpha()


def _is_digit(c: str) -> bool:
    return c.isascii() and c.isdigit()


def _rpmvercmp(a: str, b: str) -> int:
    """A comparação de versões do pacman (`rpmvercmp` da libalpm), linha a
    linha: segmentos numéricos e alfabéticos, separados por qualquer outra
    coisa."""
    if a == b:
        return 0
    one = two = 0  # início do segmento
    end1 = end2 = 0  # fim do segmento anterior
    while one < len(a) and two < len(b):
        while one < len(a) and not (a[one].isascii() and a[one].isalnum()):
            one += 1
        while two < len(b) and not (b[two].isascii() and b[two].isalnum()):
            two += 1
        if one >= len(a) or two >= len(b):
            break
        if one - end1 != two - end2:
            return -1 if one - end1 < two - end2 else 1
        end1, end2 = one, two
        if _is_digit(a[end1]):
            while end1 < len(a) and _is_digit(a[end1]):
                end1 += 1
            while end2 < len(b) and _is_digit(b[end2]):
                end2 += 1
            numeric = True
        else:
            while end1 < len(a) and _is_alpha(a[end1]):
                end1 += 1
            while end2 < len(b) and _is_alpha(b[end2]):
                end2 += 1
            numeric = False
        s1, s2 = a[one:end1], b[two:end2]
        if not s2:
            # Segmentos de tipos diferentes: o numérico é o mais novo.
            return 1 if numeric else -1
        if numeric:
            s1, s2 = s1.lstrip("0"), s2.lstrip("0")
            if len(s1) != len(s2):
                return 1 if len(s1) > len(s2) else -1
        if s1 != s2:
            return -1 if s1 < s2 else 1
        one, two = end1, end2
    if one >= len(a) and two >= len(b):
        return 0
    # Um resto alfabético nunca ganha de um fim de versão.
    if (one >= len(a) and not (two < len(b) and _is_alpha(b[two]))) or (
        one < len(a) and _is_alpha(a[one])
    ):
        return -1
    return 1


def _split_evr(version: str) -> tuple[int, str, str | None]:
    epoch = 0
    if ":" in version:
        e, version = version.split(":", 1)
        epoch = int(e) if e.isdigit() else 0
    release = None
    if "-" in version:
        version, release = version.rsplit("-", 1)
    return epoch, version, release


def vercmp(a: str, b: str) -> int:
    """`vercmp` do pacman: época, versão e, se as duas têm, o release."""
    ea, va, ra = _split_evr(a)
    eb, vb, rb = _split_evr(b)
    if ea != eb:
        return -1 if ea < eb else 1
    c = _rpmvercmp(va, vb)
    if c or ra is None or rb is None:
        return c
    return _rpmvercmp(ra, rb)


_CONSTRAINT = re.compile(r"^([^<>=]+)(<=|>=|=|<|>)?(.*)$")


def parse_dependency(text: str) -> tuple[str, str | None, str | None]:
    """`nome`, `nome=1.2-3`, `nome>=1.2`: o nome, o operador e a versão."""
    m = _CONSTRAINT.match(text.strip())
    name, op, version = m.group(1), m.group(2), m.group(3) or None
    return name, op, version


def satisfies(version: str, op: str | None, wanted: str | None) -> bool:
    if op is None:
        return True
    # "=1.2" sem release casa com qualquer release da 1.2, como no pacman.
    if op == "=" and "-" not in wanted:
        version = _split_evr(version)[1]
    c = vercmp(version, wanted)
    return {"=": c == 0, ">=": c >= 0, "<=": c <= 0, ">": c > 0, "<": c < 0}[op]


# ------------------------------------------------------------ repositórios


@dataclass
class Package:
    """Um pacote do MSYS2, do banco do repositório ou do `.PKGINFO`."""

    repo: str
    name: str
    version: str
    file: str
    sha256: str | None = None
    size: int | None = None
    depends: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    license: list[str] = field(default_factory=list)
    base: str | None = None

    def provided_version(self, name: str) -> str | None:
        """A versão com que este pacote atende `name` (ele mesmo ou um
        `provides`), ou None se não atende."""
        if name == self.name:
            return self.version
        for p in self.provides:
            pname, _, pversion = parse_dependency(p)
            if pname == name:
                return pversion or self.version
        return None


def _desc_fields(text: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    key = None
    for line in text.splitlines():
        if line.startswith("%") and line.endswith("%"):
            key = line[1:-1]
            fields[key] = []
        elif line and key:
            fields[key].append(line)
    return fields


def load_repo(repo: str) -> dict[str, Package]:
    """Os pacotes do banco atual de um repositório (baixado de novo a cada
    chamada: o banco muda todo dia)."""
    info = REPOS[repo]
    path = CACHE / "db" / info["db"]
    download(f"{info['url']}/{info['db']}", path)
    data = zstd.decompress(path.read_bytes())
    packages = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            if not member.name.endswith("/desc"):
                continue
            f = _desc_fields(tar.extractfile(member).read().decode())
            pkg = Package(
                repo=repo,
                name=f["NAME"][0],
                version=f["VERSION"][0],
                file=f["FILENAME"][0],
                sha256=f.get("SHA256SUM", [None])[0],
                size=int(f["CSIZE"][0]) if "CSIZE" in f else None,
                depends=f.get("DEPENDS", []),
                provides=f.get("PROVIDES", []),
                license=f.get("LICENSE", []),
                base=f.get("BASE", [None])[0],
            )
            packages[pkg.name] = pkg
    return packages


def package_file(repo: str, name: str, version: str, arch: str) -> str:
    return f"{name}-{version}-{arch}.pkg.tar.zst"


def package_url(repo: str, file: str) -> str:
    return f"{REPOS[repo]['url']}/{urllib.parse.quote(file)}"


def source_url(repo: str, base: str, version: str) -> str:
    """O pacote-fonte (o PKGBUILD, os patches e o fonte original)."""
    return f"{SOURCES[repo]}/{urllib.parse.quote(f'{base}-{version}.src.tar.zst')}"


def read_pkginfo(path: Path) -> dict[str, list[str]]:
    """Os campos do `.PKGINFO` de um pacote (`chave = valor`, repetíveis)."""
    with tarfile.open(path, "r:zst") as tar:
        text = tar.extractfile(".PKGINFO").read().decode()
    fields: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith("#") or " = " not in line:
            continue
        key, value = line.split(" = ", 1)
        fields.setdefault(key, []).append(value)
    return fields


# --------------------------------------------------------------- download


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(url: str, dest: Path, tries: int = 4) -> None:
    """Baixa `url` para `dest`, tentando de novo numa falha de rede."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    for attempt in range(1, tries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "lace-toolchain"})
            with urllib.request.urlopen(request, timeout=60) as response, open(partial, "wb") as f:
                shutil.copyfileobj(response, f, 1 << 20)
            partial.replace(dest)
            return
        except OSError as e:
            partial.unlink(missing_ok=True)
            if attempt == tries:
                raise RuntimeError(f"não consegui baixar {url}: {e}") from e
            time.sleep(2 * attempt)


def fetch_checked(urls: list[str], dest: Path, sha256: str) -> Path:
    """`dest` com o SHA-256 esperado: do cache, se já estiver lá, ou da
    primeira URL que entregar o arquivo certo."""
    if dest.is_file() and sha256_of(dest) == sha256:
        return dest
    errors = []
    for url in urls:
        try:
            download(url, dest)
        except RuntimeError as e:
            errors.append(str(e))
            continue
        got = sha256_of(dest)
        if got == sha256:
            return dest
        errors.append(f"{url}: SHA-256 {got}, esperado {sha256}")
        dest.unlink()
    raise RuntimeError(f"{dest.name}: nenhuma fonte serviu\n  " + "\n  ".join(errors))


# -------------------------------------------------------------------- lock


def load_lock() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def lock_id(lock_path: Path = LOCK) -> str:
    """O que identifica um lock: os 12 primeiros dígitos do SHA-256 dele. Dá
    nome à release que guarda os pacotes (`pkgs-<id>`)."""
    return sha256_of(lock_path)[:12]


def mirror_name(file: str) -> str:
    """O nome do pacote na release de pacotes. O GitHub troca caracteres como
    `~` e `+` nos nomes dos arquivos; aqui a troca é nossa e previsível."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", file)


def package_cache(entry: dict) -> Path:
    return CACHE / "pkgs" / entry["file"]


def package_sources(entry: dict, mirror: str | None) -> list[str]:
    urls = []
    if mirror:
        urls.append(f"{mirror.rstrip('/')}/{mirror_name(entry['file'])}")
    urls.append(package_url(entry["repo"], entry["file"]))
    return urls


def default_mirror() -> str | None:
    """A release com os pacotes deste lock, se `LACE_TOOLCHAIN_REPO` diz em
    que repositório do GitHub ela está. `LACE_TOOLCHAIN_MIRROR` dá a URL
    inteira."""
    if os.environ.get("LACE_TOOLCHAIN_MIRROR"):
        return os.environ["LACE_TOOLCHAIN_MIRROR"]
    repo = os.environ.get("LACE_TOOLCHAIN_REPO")
    if repo:
        return f"https://github.com/{repo}/releases/download/pkgs-{lock_id()}"
    return None


# --------------------------------------------------------------- extração


def extract_package(path: Path, repo: str, out: Path) -> list[str]:
    """Extrai em `out` a parte do pacote que o bundle leva (`KEEP`) e devolve
    os arquivos que entraram, relativos a `out`. Links viram cópias: o
    Windows só cria link simbólico com privilégio de administrador."""
    keep = KEEP[repo]
    files = []
    links = []
    with tarfile.open(path, "r:zst") as tar:
        for member in tar.getmembers():
            name = member.name
            if name in CONTROL or not name.startswith(keep):
                continue
            if ".." in Path(name).parts or Path(name).is_absolute():
                raise RuntimeError(f"{path.name}: caminho perigoso {name}")
            dest = out / name
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, open(dest, "wb") as f:
                    shutil.copyfileobj(src, f)
                os.chmod(dest, member.mode & 0o777 | 0o200)
                os.utime(dest, (member.mtime, member.mtime))
                files.append(name)
            elif member.issym() or member.islnk():
                links.append(member)
        for member in links:
            if member.issym():
                target = (Path(member.name).parent / member.linkname).as_posix()
            else:
                target = member.linkname
            source = out / os.path.normpath(target)
            dest = out / member.name
            if not source.is_file():
                # Um link para fora do que o bundle leva: fica de fora também.
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            files.append(member.name)
    return files


# ------------------------------------------------------- o bundle em disco


def bundle_python(bundle: Path) -> Path:
    return bundle / "ucrt64" / "bin" / "python.exe"


def bundle_env(bundle: Path) -> dict[str, str]:
    """Um ambiente em que só o bundle está no PATH (com o System32, sem o
    qual nada no Windows roda): prova que o bundle não depende do MSYS2 da
    máquina."""
    drop = ("PATH", "PYTHONHOME", "PYTHONPATH", "VERILATOR_ROOT")
    env = {k: v for k, v in os.environ.items() if k.upper() not in drop}
    # Sem .pyc novos: rodar o smoke não pode acrescentar arquivos ao bundle.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    paths = [str(bundle / "ucrt64" / "bin"), str(bundle / "usr" / "bin")]
    system_root = os.environ.get("SystemRoot")
    if system_root:
        paths += [str(Path(system_root) / "System32"), system_root]
    env["PATH"] = os.pathsep.join(paths)
    return env


def run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    """Roda `cmd`, mostrando-o antes, e para tudo se ele falhar."""
    print("  $ " + " ".join(str(c) for c in cmd), flush=True)
    result = subprocess.run([str(c) for c in cmd], **kwargs)
    if result.returncode != 0:
        sys.exit(f"falhou (código {result.returncode}): {cmd[0]}")
    return result


def require_windows(what: str) -> None:
    if os.name != "nt":
        sys.exit(f"{what} roda os executáveis do bundle, e isso só funciona no Windows")
