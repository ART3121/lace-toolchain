# lace-toolchain

O bloco de Windows do bundle do [Lace](https://github.com/ART3121/lace):
Icarus, Verilator, o compilador que o Verilator usa para compilar o modelo,
Perl, `make`, `sh` e Python com cocotb, todos do ambiente UCRT64 do MSYS2,
com cada pacote travado por versão e SHA-256.

Linux e macOS não usam este repositório. Lá o Lace tira os simuladores e o
cocotb do OSS CAD Suite, e o compilador, o `make` e o Perl do sistema. O
Yosys, o Graphviz, o YANC e o surfer-aurora do Windows também vêm de fora
daqui (`bundle/versions.json` do Lace).

## Por que um bundle só para o Windows

O cocotb carrega uma VPI para cada simulador. A do Icarus precisa casar com o
`vvp` que roda; a do Verilator é compilada com o mesmo `g++` que compila o
modelo. Por isso Icarus, Verilator, compilador e Python saem de uma fonte só.
No Windows, o pacote do cocotb no PyPI não traz a VPI do Verilator, e o
OSS CAD Suite não traz compilador, `make`, Perl nem cocotb: o MSYS2 traz tudo.

## De onde veio

A receita do cocotb com Verilator no Windows é a do
[nipscernlab/aurora-toolchain](https://github.com/nipscernlab/aurora-toolchain),
que a AURORA usa: a VPI do Verilator compilada à mão, estática, com
`-DPLI_DLLISPEC=`, e o runner do cocotb corrigido. O que muda aqui:

| | aurora-toolchain (`msys-v1`) | lace-toolchain |
|---|---|---|
| Ambiente do MSYS2 | MINGW64, descontinuado pelo MSYS2 em 15/03/2026 | UCRT64 |
| O que fica travado | gcc, gcc-libs e Python; o resto vem do repositório no dia do build | todos os pacotes, com SHA-256 |
| Camada MSYS (`usr/bin`) | cópia do `/usr/bin` da máquina de build (pacman, gpg, curl...) | só os pacotes do lock |
| Pacotes usados no build | artefato do Actions, que expira | release `pkgs-<id>` |
| Yosys | incluso (0.56) | fora: o Lace usa o do OSS CAD Suite |
| Arquivos de cada pacote | não registra | `lace-msys-<tag>.json` |
| cocotb | 2.0.1, Python 3.12, gcc 15.1 | 2.1.0, Python 3.14, gcc 16.2 |
| Tamanho do zip | 286 MB | cerca de 190 MB |

## Os arquivos

| Arquivo | O que é |
|---|---|
| `packages.txt` | o que o bundle pede, e por quê. É o arquivo que se edita. |
| `packages.lock.json` | gerado: cada pacote com as dependências resolvidas, versão, SHA-256, licença. O build só usa este. |
| `scripts/lock.py` | gera o lock a partir do `packages.txt` e do repositório do MSYS2 de hoje |
| `scripts/assemble.py` | baixa os pacotes do lock, confere e extrai em `dist/msys` |
| `scripts/cocotb.py` | compila o cocotb com o Python e o gcc do bundle, mais a VPI do Verilator |
| `scripts/trim.py` | tira o que nenhum fluxo usa |
| `scripts/smoke.py` | roda os quatro fluxos: Icarus, Verilator, cocotb com Icarus, cocotb com Verilator |
| `scripts/package.py` | gera o zip, o manifesto (`.json`) e o `SHA256SUMS` |
| `scripts/mirror.py` | junta os pacotes do lock para a release `pkgs-<id>` |
| `smoke/` | o flip-flop e os testbenches dos quatro fluxos |

Os scripts usam só a biblioteca padrão do Python 3.14 (o primeiro que lê o
zstd dos pacotes do MSYS2). `lock.py`, `assemble.py`, `trim.py`, `package.py`
e `mirror.py` rodam em qualquer sistema. `cocotb.py` e `smoke.py` rodam os
executáveis do bundle e só funcionam no Windows.

## Montar

No Windows, com o Python 3.14:

```
python scripts/assemble.py dist/msys
python scripts/cocotb.py dist/msys
python scripts/trim.py dist/msys
python scripts/smoke.py dist/msys
python scripts/package.py dist/msys --tag dev
```

O CI (`.github/workflows/build.yml`) faz o mesmo em cada push e pull request,
num `windows-latest`. Os pacotes ficam em `.cache/`, e o `assemble.py` só
baixa o que falta.

## Atualizar uma versão

1. Para pegar o que o MSYS2 tem hoje, rode `python scripts/lock.py`. Para
   travar uma versão, ponha-a ao lado do pacote no `packages.txt`, com o
   motivo, e rode o `lock.py`. Uma versão que o banco do MSYS2 já não lista
   é baixada da pasta do repositório, e as dependências de versão exata dela
   (o `libgcc` de um `gcc`, por exemplo) entram junto.
2. O `lock.py` mostra o que mudou. Confira.
3. Mande para o GitHub: o CI monta e roda os quatro fluxos.
4. Para publicar, rode o workflow à mão (Actions, "Bundle do Windows", Run
   workflow) com a tag `ucrt64-vN`. Ele guarda os pacotes do lock na release
   `pkgs-<id>`, se ela ainda não existe, e cria a release da tag com o zip, o
   manifesto e o `SHA256SUMS`.
5. No Lace, troque no pacote `msys` de `bundle/versions.json` a tag, as
   URLs do zip e do manifesto e os dois SHA-256 (estão no `SHA256SUMS` da
   release). Para testar o Lace com um bloco ainda não publicado,
   `LACE_MSYS_DIST` aponta o `scripts/bundle.py` dele para o `dist/` daqui.

## O que a release publica

- `lace-msys-<tag>.zip`: o bundle, com `msys/ucrt64` e `msys/usr/bin` na raiz;
- `lace-msys-<tag>.json`: as versões pedidas e, para cada pacote, versão,
  licença, pacote-fonte, dependências e os arquivos que estão no zip. O cocotb
  aparece como mais um pacote. É por esta lista que o Lace separa o bloco em
  aplicativos;
- `SHA256SUMS`: o hash dos dois.

O bundle redistribui software GPL (gcc, binutils, make, bash, coreutils). As
licenças que os pacotes trazem ficam em `ucrt64/share/licenses` e
`usr/share/licenses`; a de cada pacote também está no manifesto, com o link
do pacote-fonte no MSYS2.

## Como os programas rodam no bundle

O Verilator é o script `ucrt64/bin/verilator`, em Perl, que o `perl.exe` do
bundle executa; ele chama o `verilator_bin.exe` ao lado. O `--build` chama o
`make` da camada MSYS (`usr/bin/make.exe`), que roda o `verilated.mk` com o
`sh` de lá e compila com o `g++` do UCRT64. O `PATH` precisa ter
`ucrt64/bin` e `usr/bin`.

O `verilated.mk` do pacote do Verilator sai corrigido (`FILE_PATCHES` em
`scripts/assemble.py`): o configure do MSYS2 aceitou o `-Wl,-export_dynamic`
do macOS, que o ld do MinGW lê como `-e xport_dynamic`, o ponto de entrada
do executável.

O cocotb roda pelo `ucrt64/bin/python.exe`. O runner foi corrigido em três
pontos (`RUNNER_PATCHES` em `scripts/cocotb.py`): chama o Verilator pelo
Perl, passa cada opção de ligação num `-LDFLAGS` próprio e liga a VPI
estática com a DLL do cocotb (a `libgpi`; no cocotb 2.1 o `gpilog` e o
`cocotbutils` estão dentro dela) e com a `libstdc++-6.dll` dinâmica, a mesma
da `libgpi`. A AURORA ligava a libstdc++ estática, no gcc 15; no gcc 16.2 a
`libstdc++.a` não tem o construtor de movimento do `std::string` que o
`verilated.o` usa. O smoke define `PYTHONHOME` como `ucrt64`, como o da
AURORA, e roda o Verilator com as opções de aviso do Lace (`-Wno-fatal`,
`-Wno-TIMESCALEMOD`).

## Ainda não testado

Os scripts que rodam fora do Windows foram testados: o lock resolve 51
pacotes (137 MiB), o assemble monta, o trim corta 313 MiB e o package gera um
zip de 187 MiB, sem o cocotb, sem arquivo que não seja de algum pacote. O
`cocotb.py` e o `smoke.py` nunca rodaram. O primeiro build no Windows decide:

- **gcc 16.2.** A AURORA travou o gcc em 15.1 porque o 16.1.0-5 (MINGW64)
  trazia uma libstdc++ com que a VPI do cocotb não ligava. O lock usa o
  16.2.0-4 do UCRT64, sem teste. Se a ligação falhar, trave uma versão 15 do
  `gcc` no `packages.txt`.
- **Python 3.14 com cocotb 2.1.0.** O cocotb 2.1.0 declara suporte ao 3.14 e
  publica wheels para ele; o problema da AURORA com o 3.14 foi no 2.0.1.
- **O runner chamando o Verilator pelo Perl.** A AURORA chamava o script
  direto.
- **O pip do ensurepip do bundle** compilando o cocotb sem internet, só com
  os arquivos do lock.
- **O trim.** Ele corta mais do que o da AURORA (por exemplo, as bibliotecas
  estáticas e os cabeçalhos de openssl, sqlite e ncurses, que só entram pelas
  DLLs). Se um fluxo quebrar só depois do trim, o corte é o suspeito.

Uma diferença conhecida em relação a uma instalação do pacman: o script de
pós-instalação do Perl, que troca `@PERL_RELOCATE@` pelo caminho da
instalação em `Config.pm`, não roda. O `@INC` do Perl é calculado a partir do
`perl.exe` e não depende disso; só compilar módulos XS dependeria.
