# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge
from cocotb.triggers import ClockCycles
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge
from cocotb.types import Logic
from cocotb.types import LogicArray

async def await_half_sclk(dut):
    """Wait for half of the SCLK period (10 us)."""
    start_time = cocotb.utils.get_sim_time(units="ns")
    while True:
        await ClockCycles(dut.clk, 1)
        if (start_time + 100 * 100 * 0.5) < cocotb.utils.get_sim_time(units="ns"):
            break


def ui_in_logicarray(ncs, bit, sclk):
    """Setup the ui_in value as a LogicArray [00000, ncs, bit, sclk]."""
    return LogicArray(f"00000{ncs}{bit}{sclk}")


async def send_spi_transaction(dut, r_w, address, data):
    """
    Send a 16-bit SPI transaction:
    - 1 bit for Read/Write (1=Write, 0=Read)
    - 7 bits for Address
    - 8 bits for Data
    """
    if isinstance(data, LogicArray):
        data_int = int(data)
    else:
        data_int = data

    if address < 0 or address > 127:
        raise ValueError("Address must be 7-bit (0-127)")
    if data_int < 0 or data_int > 255:
        raise ValueError("Data must be 8-bit (0-255)")

    first_byte = (int(r_w) << 7) | address

    # Start transaction - pull nCS low
    sclk = 0
    ncs = 0
    bit = 0
    dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
    await ClockCycles(dut.clk, 1)

    # Send Address / RW Byte
    for i in range(8):
        bit = (first_byte >> (7 - i)) & 0x1
        sclk = 0
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await await_half_sclk(dut)
        sclk = 1
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await await_half_sclk(dut)

    # Send Data Byte
    for i in range(8):
        bit = (data_int >> (7 - i)) & 0x1
        sclk = 0
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await await_half_sclk(dut)
        sclk = 1
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await await_half_sclk(dut)

    # End transaction - pull nCS high
    sclk = 0
    ncs = 1
    bit = 0
    dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)

    # Give DUT time for synchronization (2-3 FF stages) and transaction finalization
    await ClockCycles(dut.clk, 600)
    return ui_in_logicarray(ncs, bit, sclk)


async def reset_dut(dut):
    """Helper to initialize clocks and perform reset sequence."""
    dut._log.info("Resetting DUT")
    dut.ena.value = 1
    dut.ui_in.value = ui_in_logicarray(ncs=1, bit=0, sclk=0)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 10)


@cocotb.test()
async def test_pwm_freq(dut):
    dut._log.info("Start PWM Frequency Test")

    # Start system clock (10 MHz / 100 ns period)
    clock = Clock(dut.clk, 100, units="ns")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)

    # 1. Enable Output on uo_out (Addr 0x00)
    await send_spi_transaction(dut, r_w=1, address=0x00, data=0xFF)

    # 2. Enable PWM Mode on uo_out (Addr 0x02)
    await send_spi_transaction(dut, r_w=1, address=0x02, data=0xFF)

    # 3. Set Duty Cycle to 50% (Addr 0x04) so SCLK toggles reliably
    await send_spi_transaction(dut, r_w=1, address=0x04, data=0x80)

    # Measure period between two consecutive rising edges
    await RisingEdge(dut.uo_out_0)
    t_start = cocotb.utils.get_sim_time(units="ns")

    await RisingEdge(dut.uo_out_0)
    t_end = cocotb.utils.get_sim_time(units="ns")

    period_ns = t_end - t_start
    freq_hz = 1e9 / period_ns

    dut._log.info(f"Measured PWM Period: {period_ns} ns ({freq_hz:.2f} Hz)")

    # Verify tolerance: 3 kHz ±1% (2970 Hz to 3030 Hz)
    assert 2970 <= freq_hz <= 3030, f"Expected 3000 Hz ±1%, got {freq_hz:.2f} Hz"

    dut._log.info("PWM Frequency Test Completed Successfully")


@cocotb.test()
async def test_pwm_duty(dut):
    dut._log.info("Start PWM Duty Cycle Test")

    # Start system clock
    clock = Clock(dut.clk, 100, units="ns")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)

    # Enable Output (0x00) and PWM Mode (0x02) on uo_out
    await send_spi_transaction(dut, r_w=1, address=0x00, data=0xFF)
    await send_spi_transaction(dut, r_w=1, address=0x02, data=0xFF)

    test_cases = [
        (0x00, 0.0),    # 0% Duty Cycle
        (0x80, 50.0),   # 50% Duty Cycle
        (0xFF, 100.0)   # 100% Duty Cycle
    ]

    for data_val, expected_pct in test_cases:
        dut._log.info(f"Testing Duty Cycle Reg 0x04 = 0x{data_val:02X} (~{expected_pct}%)")
        await send_spi_transaction(dut, r_w=1, address=0x04, data=data_val)

        # Allow output to update
        await ClockCycles(dut.clk, 100)

        if data_val == 0x00:
            # Check static low across 1 full PWM period (~3330 system clock cycles)
            for _ in range(3500):
                await ClockCycles(dut.clk, 1)
                assert dut.uo_out.value == 0, f"Expected 0% output to stay LOW, got {dut.uo_out.value}"

        elif data_val == 0xFF:
            # Check static high across 1 full PWM period
            for _ in range(3500):
                await ClockCycles(dut.clk, 1)
                assert dut.uo_out.value == 1, f"Expected 100% output to stay HIGH, got {dut.uo_out.value}"

        else:
            # Measure high time and total period
            await RisingEdge(dut.uo_out_0)
            t_rise = cocotb.utils.get_sim_time(units="ns")

            await FallingEdge(dut.uo_out_0)
            t_fall = cocotb.utils.get_sim_time(units="ns")

            await RisingEdge(dut.uo_out_0)
            t_next_rise = cocotb.utils.get_sim_time(units="ns")

            high_time = t_fall - t_rise
            period = t_next_rise - t_rise
            measured_duty = (high_time / period) * 100.0

            dut._log.info(f"Measured Duty Cycle: {measured_duty:.2f}%")

            # Check ±1% tolerance
            assert abs(measured_duty - expected_pct) <= 1.0, (
                f"Duty cycle error: expected {expected_pct}%, got {measured_duty:.2f}%"
            )

    dut._log.info("PWM Duty Cycle Test Completed Successfully")