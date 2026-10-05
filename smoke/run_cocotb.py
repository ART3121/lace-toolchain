# Compila e roda o test_dff.py pelo runner do cocotb, no simulador de $SIM.
import os
from pathlib import Path

from cocotb_tools.runner import get_runner

here = Path(__file__).parent
sim = os.environ["SIM"]
build_dir = Path.cwd() / f"sim_{sim}"
runner = get_runner(sim)
runner.build(
    sources=[here / "dff.v"],
    hdl_toplevel="dff",
    build_dir=build_dir,
    build_args=["-g2012"] if sim == "icarus" else [],
    timescale=("1ns", "1ps"),
    always=True,
    waves=True,
)
runner.test(
    hdl_toplevel="dff",
    test_module="test_dff",
    build_dir=build_dir,
    test_dir=here,
    waves=True,
)
print(f"SMOKE_OK cocotb-{sim}")
