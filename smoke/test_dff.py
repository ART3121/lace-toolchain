# O testbench em cocotb, o mesmo para o Icarus e o Verilator.
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer


@cocotb.test()
async def q_follows_d(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    for value in (1, 0, 1):
        dut.d.value = value
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        assert int(dut.q.value) == value
